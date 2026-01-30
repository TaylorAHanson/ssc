import pytest
from unittest.mock import AsyncMock, patch
from app.tools.governance.search_audit_logs import SearchAuditLogsTool

@pytest.mark.asyncio
async def test_search_audit_logs_list():
    with patch("app.tools.governance.search_audit_logs.DatabricksProvider") as MockP:
        MockP.return_value.execute_sql = AsyncMock(return_value={"rows": [{"action_name": "login"}]})
        tool = SearchAuditLogsTool()
        
        result = await tool.execute(start_date="2023-01-01", end_date="2023-01-02", email="user@example.com")
        
        assert "results" in result
        assert len(result["results"]) == 1
        assert result["query_type"] == "list"
        
        args, kwargs = MockP.return_value.execute_sql.call_args
        assert "system.access.audit" in args[0]
        assert "user_identity.email = 'user@example.com'" in args[0]
        assert "ORDER BY event_time DESC" in args[0]
        assert kwargs["timeout_seconds"] == 300

@pytest.mark.asyncio
async def test_search_audit_logs_count():
    with patch("app.tools.governance.search_audit_logs.DatabricksProvider") as MockP:
        MockP.return_value.execute_sql = AsyncMock(return_value={"rows": [{"event_count": 100}]})
        tool = SearchAuditLogsTool()
        
        result = await tool.execute(
            start_date="2023-01-01", 
            end_date="2023-01-02", 
            aggregation_type="count"
        )
        
        assert result["results"][0]["event_count"] == 100
        assert result["query_type"] == "count"
        
        args, _ = MockP.return_value.execute_sql.call_args
        assert "SELECT COUNT(*) as event_count" in args[0]
        assert "GROUP BY" not in args[0]

@pytest.mark.asyncio
async def test_search_audit_logs_group_by():
    with patch("app.tools.governance.search_audit_logs.DatabricksProvider") as MockP:
        MockP.return_value.execute_sql = AsyncMock(return_value={"rows": [{"actor": "user1", "event_count": 50}]})
        tool = SearchAuditLogsTool()
        
        result = await tool.execute(
            start_date="2023-01-01", 
            end_date="2023-01-02", 
            aggregation_type="count",
            group_by_columns=["user_identity.email"]
        )
        
        assert result["results"][0]["event_count"] == 50
        
        args, _ = MockP.return_value.execute_sql.call_args
        assert "GROUP BY user_identity.email" in args[0]
        assert "ORDER BY event_count DESC" in args[0]
