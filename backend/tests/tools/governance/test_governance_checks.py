import pytest
from unittest.mock import AsyncMock, patch
from app.tools.governance.check_overprovisioning import CheckOverprovisionedUsersTool
from app.tools.governance.check_orphans import CheckOrphanedAssetsTool
from app.tools.governance.check_quality import CheckAssetQualityTool

@pytest.mark.asyncio
async def test_check_overprovisioning():
    with patch("app.tools.governance.check_overprovisioning.DatabricksProvider") as MockP:
        MockP.return_value.execute_sql = AsyncMock(return_value={"rows": [{"grantee": "admin"}]})
        tool = CheckOverprovisionedUsersTool()
        
        result = await tool.execute(check_type="admins")
        assert "high_privilege_users" in result
        assert "system.information_schema.catalog_privileges" in MockP.return_value.execute_sql.call_args[0][0]

@pytest.mark.asyncio
async def test_check_orphans():
    with patch("app.tools.governance.check_orphans.DatabricksProvider") as MockP:
        MockP.return_value.execute_sql = AsyncMock(return_value={"rows": [{"name": "orphaned_cat"}]})
        tool = CheckOrphanedAssetsTool()
        
        result = await tool.execute(asset_type="CATALOG")
        assert "assets" in result
        assert "system.information_schema.catalogs" in MockP.return_value.execute_sql.call_args[0][0]

@pytest.mark.asyncio
async def test_check_quality():
    with patch("app.tools.governance.check_quality.DatabricksProvider") as MockP:
        MockP.return_value.execute_sql = AsyncMock(return_value={"rows": [{"name": "bad_table"}]})
        tool = CheckAssetQualityTool()
        
        result = await tool.execute(check_type="missing_description", scope="TABLE")
        assert "issues" in result
        assert "comment IS NULL" in MockP.return_value.execute_sql.call_args[0][0]
