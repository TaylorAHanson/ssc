import pytest
from unittest.mock import AsyncMock, patch
from app.tools.governance.check_permissions import check_object_permissions
from app.tools.governance.audit_access import audit_user_access

@pytest.mark.asyncio
async def test_check_object_permissions():
    # Better to patch at the tool module level
    with patch("app.tools.governance.check_permissions.DatabricksProvider") as MockP:
        MockP.return_value.execute_sql = AsyncMock(return_value={"rows": ["grant1"]})
        tool = check_object_permissions
        result = await tool.execute(object_type="TABLE", object_name="main.db.tbl")
        
        assert result["grants"] == ["grant1"]
        args, kwargs = MockP.return_value.execute_sql.call_args
        assert "SHOW GRANTS ON TABLE main.db.tbl" in args[0]
        assert kwargs["timeout_seconds"] == 300

@pytest.mark.asyncio
async def test_audit_user_access():
     with patch("app.tools.governance.audit_access.DatabricksProvider") as MockP:
        MockP.return_value.execute_sql = AsyncMock(return_value={"rows": ["priv1"]})
        tool = audit_user_access
        result = await tool.execute(user_email="test@example.com", catalog="main")
        
        assert "direct_grants" in result
        assert result["direct_grants"]["catalog"] == ["priv1"]
        # Should call 3 times (cat, schema, table)
        assert MockP.return_value.execute_sql.call_count == 3
        # Verify first call has timeout and catalog
        args, kwargs = MockP.return_value.execute_sql.call_args_list[0]
        assert "catalog_name = 'main'" in args[0]
        assert kwargs["timeout_seconds"] == 120
