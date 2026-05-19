import pytest
from unittest.mock import AsyncMock, patch
from app.tools.governance.check_overprovisioning import check_overprovisioned_users
from app.tools.governance.check_orphans import check_orphaned_assets
from app.tools.governance.check_quality import check_asset_quality

@pytest.mark.asyncio
async def test_check_overprovisioning_risk_score():
    with patch("app.tools.governance.check_overprovisioning.DatabricksProvider") as MockP:
        MockP.return_value.execute_sql = AsyncMock(return_value={"rows": [{"email": "user@example.com", "over_provisioning_score": 2}]})
        tool = check_overprovisioned_users
        
        result = await tool.execute(check_type="risk_score")
        assert "risk_assessment" in result
        assert result["risk_assessment"][0]["over_provisioning_score"] == 2
        
        args, _ = MockP.return_value.execute_sql.call_args
        assert "over_provisioning_score" in args[0]
        assert "system.access.audit" in args[0]

@pytest.mark.asyncio
async def test_check_overprovisioning_workspace_admins():
    with patch("app.tools.governance.check_overprovisioning.DatabricksProvider") as MockP:
        MockP.return_value.execute_sql = AsyncMock(return_value={"rows": [{"email": "admin@example.com"}]})
        tool = check_overprovisioned_users
        
        result = await tool.execute(check_type="workspace_admins")
        assert "workspace_admins" in result
        assert result["workspace_admins"][0]["email"] == "admin@example.com"
        
        args, _ = MockP.return_value.execute_sql.call_args
        assert "system.information_schema.group_members" in args[0]

@pytest.mark.asyncio
async def test_check_orphans():
    with patch("app.tools.governance.check_orphans.DatabricksProvider") as MockP:
        MockP.return_value.execute_sql = AsyncMock(return_value={"rows": [{"name": "orphaned_cat"}]})
        tool = check_orphaned_assets
        
        result = await tool.execute(asset_type="CATALOG")
        assert "assets" in result
        assert "system.information_schema.catalogs" in MockP.return_value.execute_sql.call_args[0][0]

@pytest.mark.asyncio
async def test_check_quality():
    with patch("app.tools.governance.check_quality.DatabricksProvider") as MockP:
        MockP.return_value.execute_sql = AsyncMock(return_value={"rows": [{"name": "bad_table"}]})
        tool = check_asset_quality
        
        result = await tool.execute(check_type="missing_description", scope="TABLE")
        assert "issues" in result
        assert "comment IS NULL" in MockP.return_value.execute_sql.call_args[0][0]
