import pytest
from unittest.mock import AsyncMock, patch
from app.tools.finops.check_tagging import CheckTaggingComplianceTool

@pytest.fixture
def mock_provider():
    with patch("app.tools.finops.check_tagging.DatabricksProvider") as MockProvider:
        provider_instance = MockProvider.return_value
        provider_instance.execute_sql = AsyncMock()
        yield provider_instance

@pytest.mark.asyncio
async def test_check_tagging_compliance(mock_provider):
    tool = CheckTaggingComplianceTool()
    
    mock_provider.execute_sql.return_value = {
        "rows": [{"cluster_name": "bad-cluster", "custom_tags": {}}]
    }
    
    required_tags = ["CostCenter", "Project"]
    result = await tool.execute(required_tags=required_tags)
    
    assert "non_compliant_resources" in result
    assert result["checked_tags"] == required_tags
    
    args = mock_provider.execute_sql.call_args[0]
    query = args[0]
    assert "NOT map_contains_key(custom_tags, 'CostCenter')" in query
    assert "NOT map_contains_key(custom_tags, 'Project')" in query
    assert "OR" in query # Should check if ANY tag is missing
