import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.tools.finops.get_cost_summary import get_cost_summary

@pytest.fixture
def tool():
    return get_cost_summary

@pytest.fixture
def mock_provider():
    with patch("app.tools.finops.get_cost_summary.DatabricksProvider") as MockProvider:
        provider_instance = MockProvider.return_value
        provider_instance.execute_sql = AsyncMock()
        yield provider_instance

@pytest.mark.asyncio
async def test_get_cost_summary_success(tool, mock_provider):
    start_date = "2023-01-01"
    end_date = "2023-01-31"
    
    mock_provider.execute_sql.return_value = {
        "rows": [{"usage_date": "2023-01-01", "total_cost": 100.0}]
    }
    
    result = await tool.execute(start_date=start_date, end_date=end_date, granularity="daily")
    
    assert "costs" in result
    assert len(result["costs"]) == 1
    assert result["costs"][0]["total_cost"] == 100.0
    
    # Verify query construction
    # Verify query construction
    args = mock_provider.execute_sql.call_args[0]
    query = args[0]
    
    # Updated expectations for new JOIN logic
    assert "SUM(u.usage_quantity * lp.pricing.default) as total_cost" in query
    assert "FROM system.billing.usage u" in query
    assert "JOIN system.billing.list_prices lp" in query
    assert "u.sku_name = lp.sku_name" in query
    assert f"u.usage_date BETWEEN '{start_date}' AND '{end_date}'" in query
    assert "GROUP BY 1" in query

@pytest.mark.asyncio
async def test_get_cost_summary_group_by(tool, mock_provider):
    mock_provider.execute_sql.return_value = {"rows": []}
    
    await tool.execute(start_date="2023-01-01", end_date="2023-01-31", group_by="workspace_id")
    
    args = mock_provider.execute_sql.call_args[0]
    query = args[0]
    assert "u.workspace_id" in query
    # Check simple group by logic
    assert "GROUP BY 1" in query
    # Logic was: cols.append(group_by) -> select group_by, sum... -> group by 1
