"""
Unit tests for Terramate workflow and conversational tools, and check_resource_access (ADR-0004).
"""
from unittest.mock import AsyncMock, patch
import pytest

from app.tools.check_resource_access import check_resource_access
from app.tools.self_service.check_provisioning_status import check_provisioning_status
from app.workflows.tools import (
    terramate_check_status,
    terramate_provision,
    terramate_submit_request,
)


@pytest.mark.asyncio
async def test_terramate_submit_request_workflow_tool():
    with patch("app.providers.terramate.client.TerramateProvider.create_request", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = {
            "success": True,
            "request_id": "req-999",
            "status": "pending",
        }

        result = await terramate_submit_request.execute(
            request_type="workspace",
            parameters={"name": "my-ws"},
            idempotency_key="key-abc",
            _user_email="requester@databricks.com",
        )

        assert result["ok"] is True
        assert result["terramate_request_id"] == "req-999"
        assert result["status"] == "pending"
        mock_create.assert_called_once_with(
            request_type="workspace",
            params={"name": "my-ws"},
            idempotency_key="key-abc",
        )


@pytest.mark.asyncio
async def test_terramate_check_status_workflow_tool():
    with patch("app.providers.terramate.client.TerramateProvider.get_request", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {
            "id": "req-999",
            "type": "workspace",
            "status": "in_progress",
            "steps": [
                {
                    "ordinal": 0,
                    "key": "create",
                    "status": "submitted",
                    "pr_url": "https://github.com/org/repo/pull/10",
                }
            ],
        }

        result = await terramate_check_status.execute(terramate_request_id="req-999")
        assert result["exists"] is True
        assert result["status"] == "in_progress"
        assert result["is_terminal"] is False
        assert result["is_succeeded"] is False
        assert result["active_pr_url"] == "https://github.com/org/repo/pull/10"

        mock_get.return_value = {
            "id": "req-999",
            "type": "workspace",
            "status": "succeeded",
            "steps": [{"ordinal": 0, "status": "done"}],
        }
        result_succeeded = await terramate_check_status.execute(terramate_request_id="req-999")
        assert result_succeeded["is_terminal"] is True
        assert result_succeeded["is_succeeded"] is True

        mock_get.return_value = None
        result_404 = await terramate_check_status.execute(terramate_request_id="missing")
        assert result_404["exists"] is False
        assert result_404["status"] == "not_found"
        assert result_404["is_terminal"] is True


@pytest.mark.asyncio
async def test_terramate_provision_workflow_tool():
    with patch("app.providers.terramate.client.TerramateProvider.create_request", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = {
            "success": True,
            "request_id": "req-123",
            "status": "pending",
        }

        result = await terramate_provision.execute(
            request_type="schema",
            parameters={"catalog": "main", "name": "finance"},
            idempotency_key="idemp-key-1",
        )

        assert result["ok"] is True
        assert result["terramate_request_id"] == "req-123"
        assert result["status"] == "pending"


@pytest.mark.asyncio
async def test_terramate_provision_rejects_invalid_type():
    # Demonstrates client-side Pydantic validation: 'test' is rejected before any network call
    result = await terramate_provision.execute(
        request_type="test",
        parameters={"test": True},
    )
    assert "error" in result
    assert "Invalid arguments for tool 'terramate_provision'" in result["error"]


@pytest.mark.asyncio
async def test_check_provisioning_status_conversational_tool():
    with patch("app.providers.terramate.client.TerramateProvider.get_request", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {
            "id": "req-123",
            "type": "schema",
            "status": "in_progress",
            "requester": "analyst@databricks.com",
            "steps": [
                {
                    "ordinal": 0,
                    "key": "schema",
                    "status": "submitted",
                    "pr_number": 42,
                    "pr_url": "https://github.com/org/repo/pull/42",
                    "stuck": True,
                }
            ],
        }

        result = await check_provisioning_status.execute(
            request_id="req-123",
        )

        assert result["success"] is True
        assert result["found"] is True
        assert result["status"] == "in_progress"
        assert result["is_terminal"] is False
        assert len(result["steps"]) == 1
        assert result["steps"][0]["pr_number"] == 42
        assert "https://github.com/org/repo/pull/42" in result["active_pr_urls"]
        assert "Action required on GitHub" in result["message"]
        assert "waiting longer than expected" in result["message"]


@pytest.mark.asyncio
async def test_check_resource_access_direct_uc():
    with patch("app.providers.databricks.DatabricksProvider.execute_sql", new_callable=AsyncMock) as mock_sql:
        mock_sql.return_value = {
            "rows": [
                {"Principal": "data_engineers", "ActionType": "USE_SCHEMA"},
                {"Principal": "data_engineers", "ActionType": "SELECT"},
                {"Principal": "analysts", "ActionType": "SELECT"},
            ]
        }

        # Check all grants
        res_all = await check_resource_access.execute(resource_name="main.finance")
        assert res_all["success"] is True
        assert res_all["exists"] is True
        assert len(res_all["grants"]) == 2

        # Check principal-specific
        res_principal = await check_resource_access.execute(
            resource_name="main.finance",
            principal="data_engineers",
        )
        assert res_principal["success"] is True
        assert res_principal["has_grants"] is True
        assert "USE_SCHEMA" in res_principal["principal_privileges"]
        assert "SELECT" in res_principal["principal_privileges"]
