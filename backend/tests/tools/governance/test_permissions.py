import pytest
from unittest.mock import AsyncMock, patch
from app.tools.governance.check_permissions import CheckObjectPermissionsTool
from app.tools.governance.audit_access import AuditUserAccessTool

@pytest.fixture
def mock_provider():
    # Patch generic since we might use different paths/classes
    with patch("app.providers.databricks.DatabricksProvider") as MockProvider:
        provider_instance = MockProvider.return_value
        provider_instance.execute_sql = AsyncMock()
        yield provider_instance

@pytest.mark.asyncio
async def test_check_object_permissions(mock_provider):
    # We need to patch the tool's provider usage specifically or rely on the class structure using generic provider
    # Since tools use `from app.providers.databricks import DatabricksProvider`, patching that global import works best if done correctly in test file scope or fixture.
    # However, Python imports sometimes bind early.
    
    # Better to patch at the tool module level
    with patch("app.tools.governance.check_permissions.DatabricksProvider") as MockP:
        MockP.return_value.execute_sql = AsyncMock(return_value={"rows": ["grant1"]})
        tool = CheckObjectPermissionsTool()
        result = await tool.execute(object_type="TABLE", object_name="main.db.tbl")
        
        assert result["grants"] == ["grant1"]
        assert "SHOW GRANTS ON TABLE main.db.tbl" in MockP.return_value.execute_sql.call_args[0][0]

@pytest.mark.asyncio
async def test_audit_user_access(mock_provider):
     with patch("app.tools.governance.audit_access.DatabricksProvider") as MockP:
        MockP.return_value.execute_sql = AsyncMock(return_value={"rows": ["priv1"]})
        tool = AuditUserAccessTool()
        result = await tool.execute(user_email="test@example.com")
        
        assert "direct_grants" in result
        assert result["direct_grants"]["catalogs"] == ["priv1"]
        # Should call 3 times (cat, schema, table)
        assert MockP.return_value.execute_sql.call_count == 3
