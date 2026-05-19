import pytest
from unittest.mock import AsyncMock, patch
from app.tools.finops.get_efficiency import get_resource_efficiency_metrics

@pytest.fixture
def tool():
    return get_resource_efficiency_metrics

@pytest.fixture
def mock_provider():
    with patch("app.tools.finops.get_efficiency.DatabricksProvider") as MockProvider:
        provider_instance = MockProvider.return_value
        provider_instance.execute_sql = AsyncMock()
        yield provider_instance

@pytest.mark.asyncio
async def test_get_efficiency_idle_clusters(tool, mock_provider):
    mock_provider.execute_sql.return_value = {
        "rows": [
            {"cluster_id": "test-cluster-id", "cluster_name": "test-cluster", "owned_by": "me"}
        ]
    }
    
    result = await tool.execute(metric="idle_time", threshold_hours=48)
    
    assert "inefficient_resources" in result
    assert result["metric"] == "idle_time"
    assert len(result["inefficient_resources"]) == 1
    
    args = mock_provider.execute_sql.call_args[0]
    query = args[0]
    assert "system.compute.clusters" in query
    assert "cluster_name" in query
    assert "delete_time IS NULL" in query
