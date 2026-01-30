import pytest
from unittest.mock import AsyncMock, patch
from app.tools.governance.search_audit_logs import SearchAuditLogsTool

@pytest.mark.asyncio
async def test_search_audit_logs():
    with patch("app.tools.governance.search_audit_logs.DatabricksProvider") as MockP:
        MockP.return_value.execute_sql = AsyncMock(return_value={"rows": [{"action_name": "login"}]})
        tool = SearchAuditLogsTool()
        
        result = await tool.execute(start_date="2023-01-01", end_date="2023-01-02", email="user@example.com")
        
        assert "events" in result
        assert len(result["events"]) == 1
        
        query = MockP.return_value.execute_sql.call_args[0][0]
        assert "system.access.audit" in query
        assert "user_identity.email = 'user@example.com'" in query
