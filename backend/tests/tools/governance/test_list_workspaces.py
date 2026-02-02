import pytest
from unittest.mock import AsyncMock, patch
from app.tools.governance.list_workspaces import ListWorkspacesTool

@pytest.mark.asyncio
async def test_list_workspaces():
    with patch("app.tools.governance.list_workspaces.DatabricksProvider") as MockP:
        mock_data = {
            "rows": [
                {
                    "account_id": "acc-1",
                    "workspace_id": "1", 
                    "workspace_name": "Prod", 
                    "workspace_url": "https://prod.cloud.databricks.com", 
                    "create_time": "2025-01-01",
                    "status": "RUNNING"
                }
            ]
        }
        MockP.return_value.execute_sql = AsyncMock(return_value=mock_data)
        tool = ListWorkspacesTool()
        
        # Test without filter
        result = await tool.execute()
        assert result["count"] == 1
        assert "workspaces_latest" in result["query"]
        
        # Test with filter
        result = await tool.execute(status="RUNNING")
        assert "WHERE status = 'RUNNING'" in result["query"]
