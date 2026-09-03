import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from databricks.sdk.service.apps import (
    App,
    AppAccessControlResponse,
    AppPermissions,
    AppPermissionsDescription,
    ComputeState,
    ComputeStatus,
)

from app.core.config import settings
from app.core.workspaces import WorkspaceConfig
from app.db.enforcement_audit import EnforcementAuditModel
from app.db.request import RequestModel
from app.providers.databricks.handlers.app_handler import (
    AppResourceHandler,
    is_protected_app,
)
from app.services.sentinel_findings import replace_run_findings
import app.workflows.sentinel as sentinel


def _make_request(db, state_context=None):
    req = RequestModel(
        id=f"req-{uuid.uuid4()}",
        type="enforcement_sentinel",
        title="Test Sentinel Run",
        status="processing",
        current_state="processing",
        state_context=state_context or {},
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db.add(req)
    db.commit()
    return req


def _keep_open(session):
    session.close = lambda: None
    return session


# ---------------------------------------------------------------------------
# 1. Protected App Detection Tests
# ---------------------------------------------------------------------------
def test_is_protected_app_builtins():
    assert is_protected_app("edh-ssc") is True
    assert is_protected_app("edh-ssc-prod") is True
    assert is_protected_app("edh-ssc-local-user") is True
    assert is_protected_app("my-edh-ssc-app") is True
    assert is_protected_app("mcp-server") is True
    assert is_protected_app("mcp-server-dev") is True
    assert is_protected_app("") is True  # Safe default on empty
    assert is_protected_app("rogue-unauthorized-app") is False


def test_is_protected_app_env_and_settings(monkeypatch):
    monkeypatch.setenv("DATABRICKS_APP_NAME", "my-custom-platform")
    assert is_protected_app("my-custom-platform") is True

    with patch.object(settings, "SENTINEL_PROTECTED_APP_NAMES", "critical-dash*,finance-bi"):
        assert is_protected_app("critical-dashboard") is True
        assert is_protected_app("finance-bi") is True
        assert is_protected_app("other-random-app") is False


# ---------------------------------------------------------------------------
# 2. Step Determination and Mode Gating Tests
# ---------------------------------------------------------------------------
def test_determine_intended_step():
    # Apps map KILL to stop_and_revoke
    assert sentinel.determine_intended_step("HIGH", "KILL", "app") == "stop_and_revoke"
    assert sentinel.determine_intended_step("HIGH", "STOP_AND_REVOKE", "app") == "stop_and_revoke"

    # Non-apps map KILL to kill
    assert sentinel.determine_intended_step("HIGH", "KILL", "cluster") == "kill"
    assert sentinel.determine_intended_step("HIGH", "KILL", "job") == "kill"

    # Non-destructive / lower severity actions
    assert sentinel.determine_intended_step("MEDIUM", "KILL", "app") == "warn"
    assert sentinel.determine_intended_step("LOW", "KILL", "app") == "warn"
    assert sentinel.determine_intended_step("HIGH", "WARN", "app") == "warn"


def test_resolve_automated_step_toggle():
    # Toggle OFF (default): apps are downgraded to warn
    with patch.object(settings, "SENTINEL_AUTO_ENFORCE_APPS", False):
        assert sentinel.resolve_automated_step("HIGH", "KILL", "app") == "warn"
        assert sentinel.resolve_automated_step("HIGH", "KILL", "cluster") == "warn"

    # Toggle ON: apps get stop_and_revoke, while clusters/jobs STAY downgraded to warn!
    with patch.object(settings, "SENTINEL_AUTO_ENFORCE_APPS", True):
        assert sentinel.resolve_automated_step("HIGH", "KILL", "app") == "stop_and_revoke"
        assert sentinel.resolve_automated_step("HIGH", "STOP_AND_REVOKE", "app") == "stop_and_revoke"
        # Non-app resources MUST remain manual-only
        assert sentinel.resolve_automated_step("HIGH", "KILL", "cluster") == "warn"
        assert sentinel.resolve_automated_step("HIGH", "KILL", "job") == "warn"
        assert sentinel.resolve_automated_step("HIGH", "KILL", "sql_warehouse") == "warn"


# ---------------------------------------------------------------------------
# 3. AppResourceHandler Tests
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_app_handler_stop_and_revoke_protected():
    mock_ws = MagicMock()
    handler = AppResourceHandler(mock_ws)

    res = await handler.stop_and_revoke("edh-ssc-prod")
    assert res["status"] == "skipped_protected"
    assert res["stopped"] is False
    mock_ws.apps.stop.assert_not_called()
    mock_ws.apps.set_permissions.assert_not_called()


@pytest.mark.asyncio
async def test_app_handler_stop_and_revoke_success():
    mock_ws = MagicMock()
    mock_app = MagicMock(spec=App)
    mock_app.name = "rogue-app"
    mock_app.creator = "rogue-owner@company.com"
    mock_app.compute_status = MagicMock(spec=ComputeStatus)
    mock_app.compute_status.state = ComputeState.ACTIVE

    mock_perms = MagicMock(spec=AppPermissions)
    mock_perms.access_control_list = [
        MagicMock(as_dict=lambda: {"user_name": "rogue-owner@company.com", "permission_level": "CAN_MANAGE"}),
        MagicMock(as_dict=lambda: {"group_name": "users", "permission_level": "CAN_USE"}),
    ]

    mock_ws.apps.get.return_value = mock_app
    mock_ws.apps.get_permissions.return_value = mock_perms
    mock_ws.config.client_id = "test-sp-client-id"

    handler = AppResourceHandler(mock_ws)
    res = await handler.stop_and_revoke("rogue-app")

    assert res["status"] == "success"
    assert res["stopped"] is True
    assert res["permissions_revoked"] is True
    assert res["creator"] == "rogue-owner@company.com"
    assert len(res["previous_acl"]) == 2

    # Verify stop was called
    mock_ws.apps.stop.assert_called_once_with(name="rogue-app")

    # Verify permissions were locked down to admins group + SP
    mock_ws.apps.set_permissions.assert_called_once()
    call_args = mock_ws.apps.set_permissions.call_args
    assert call_args.kwargs["app_name"] == "rogue-app"
    new_acl = call_args.kwargs["access_control_list"]
    assert any(req.group_name == "admins" for req in new_acl)
    assert any(req.service_principal_name == "test-sp-client-id" for req in new_acl)
    # Ensure rogue owner is stripped from new ACL
    assert not any(req.user_name == "rogue-owner@company.com" for req in new_acl)


@pytest.mark.asyncio
async def test_app_handler_reinstate_permissions():
    mock_ws = MagicMock()
    handler = AppResourceHandler(mock_ws)

    original_acl = [
        {"user_name": "reinstated-owner@company.com", "permission_level": "CAN_MANAGE"},
        {"group_name": "analysts", "permission_level": "CAN_USE"},
    ]

    ok = await handler.reinstate_permissions("rogue-app", original_acl=original_acl)
    assert ok is True

    mock_ws.apps.set_permissions.assert_called_once()
    reinstated_acl = mock_ws.apps.set_permissions.call_args.kwargs["access_control_list"]
    assert any(req.user_name == "reinstated-owner@company.com" for req in reinstated_acl)
    assert any(req.group_name == "admins" for req in reinstated_acl)


# ---------------------------------------------------------------------------
# 4. End-to-End Sentinel Enforcement Workflow Tests
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_sentinel_run_enforcement_app_auto_stopped(db_session):
    req = _make_request(db_session)

    # Insert findings into DB
    replace_run_findings(
        db_session,
        req.id,
        checks=[],
        violations=[
            {
                "rule_id": "no_apps_enterprise_prod",
                "resource_id": "unauthorized-prod-app",
                "resource_type": "app",
                "policy": "apps",
                "severity": "HIGH",
                "action": "KILL",
                "reason": "App not permitted in prod enterprise workspace without allowlist.",
                "workspace": {"name": "prod-ws", "host": "https://prod.databricks.net", "environment": "prod"},
            }
        ],
    )

    mock_handler = MagicMock()
    mock_handler.workspace_client = MagicMock()
    mock_handler.stop_and_revoke = AsyncMock(return_value={
        "status": "success",
        "stopped": True,
        "permissions_revoked": True,
        "previous_acl": [{"user_name": "app-owner@databricks.com", "permission_level": "CAN_MANAGE"}],
        "creator": "app-owner@databricks.com",
    })

    with (
        patch.object(settings, "SENTINEL_AUTO_ENFORCE_APPS", True),
        patch.object(sentinel, "_new_workspace_client", return_value=mock_handler.workspace_client),
        patch.object(sentinel, "_handlers_by_type", return_value={"app": mock_handler}),
        patch.object(sentinel, "revalidate_violation", new=AsyncMock(return_value={"still_violates": True})),
        patch("app.providers.notifications.client.NotificationProvider.send_email", new=AsyncMock(return_value=True)) as mock_email,
        patch("app.db.session.get_lakebase_session", side_effect=lambda: _keep_open(db_session)),
    ):
        result = await sentinel.run_enforcement(db_session, req)

    assert result["enforced"] is True
    assert result["apps_auto_stopped"] == 1
    mock_handler.stop_and_revoke.assert_called_once_with("unauthorized-prod-app")
    mock_email.assert_called_once()
    assert "app-owner@databricks.com" in mock_email.call_args.kwargs["to"]

    # Verify audit row was persisted with previous ACL snapshot
    audit_row = db_session.query(EnforcementAuditModel).filter(
        EnforcementAuditModel.request_id == req.id,
        EnforcementAuditModel.resource_id == "unauthorized-prod-app",
    ).first()
    assert audit_row is not None
    assert audit_row.executed_action == "automated_stop_and_revoke"
    assert "Previous ACL snapshot:" in audit_row.reason


@pytest.mark.asyncio
async def test_sentinel_run_enforcement_circuit_breaker(db_session):
    req = _make_request(db_session)

    # Insert 2 violating apps
    replace_run_findings(
        db_session,
        req.id,
        checks=[],
        violations=[
            {
                "rule_id": "no_apps_enterprise_prod",
                "resource_id": "app-1",
                "resource_type": "app",
                "policy": "apps",
                "severity": "HIGH",
                "action": "KILL",
                "reason": "Violating app 1",
                "workspace": {"name": "prod-ws", "host": "https://prod.databricks.net", "environment": "prod"},
            },
            {
                "rule_id": "no_apps_enterprise_prod",
                "resource_id": "app-2",
                "resource_type": "app",
                "policy": "apps",
                "severity": "HIGH",
                "action": "KILL",
                "reason": "Violating app 2",
                "workspace": {"name": "prod-ws", "host": "https://prod.databricks.net", "environment": "prod"},
            },
        ],
    )

    mock_handler = MagicMock()
    mock_handler.workspace_client = MagicMock()
    mock_handler.stop_and_revoke = AsyncMock(return_value={
        "status": "success",
        "stopped": True,
        "permissions_revoked": True,
        "previous_acl": [],
        "creator": "owner@company.com",
    })
    mock_handler.warn = AsyncMock(return_value=True)

    with (
        patch.object(settings, "SENTINEL_AUTO_ENFORCE_APPS", True),
        # Set max apps per run to 1 -> second app must trip the breaker!
        patch.object(settings, "SENTINEL_AUTO_ENFORCE_MAX_APPS_PER_RUN", 1),
        patch.object(sentinel, "_new_workspace_client", return_value=mock_handler.workspace_client),
        patch.object(sentinel, "_handlers_by_type", return_value={"app": mock_handler}),
        patch.object(sentinel, "revalidate_violation", new=AsyncMock(return_value={"still_violates": True})),
        patch("app.providers.notifications.client.NotificationProvider.send_email", new=AsyncMock(return_value=True)),
        patch("app.db.session.get_lakebase_session", side_effect=lambda: _keep_open(db_session)),
    ):
        result = await sentinel.run_enforcement(db_session, req)

    assert result["apps_auto_stopped"] == 1
    mock_handler.stop_and_revoke.assert_called_once_with("app-1")
    # Second app was warned due to circuit breaker trip
    mock_handler.warn.assert_called_once()
    assert "app-2" in mock_handler.warn.call_args[0][0]

    # Verify audit entries
    audits = {
        r.resource_id: r.executed_action
        for r in db_session.query(EnforcementAuditModel).filter(EnforcementAuditModel.request_id == req.id).all()
    }
    assert audits["app-1"] == "automated_stop_and_revoke"
    assert audits["app-2"] == "warn_circuit_breaker"


@pytest.mark.asyncio
async def test_sentinel_run_enforcement_self_preservation(db_session):
    req = _make_request(db_session)

    replace_run_findings(
        db_session,
        req.id,
        checks=[],
        violations=[
            {
                "rule_id": "no_apps_enterprise_prod",
                "resource_id": "edh-ssc-prod",
                "resource_type": "app",
                "policy": "apps",
                "severity": "HIGH",
                "action": "KILL",
                "reason": "Platform app flagged mistakenly",
                "workspace": {"name": "prod-ws", "host": "https://prod.databricks.net", "environment": "prod"},
            }
        ],
    )

    mock_handler = MagicMock()
    mock_handler.workspace_client = MagicMock()
    mock_handler.stop_and_revoke = AsyncMock()

    with (
        patch.object(settings, "SENTINEL_AUTO_ENFORCE_APPS", True),
        patch.object(sentinel, "_new_workspace_client", return_value=mock_handler.workspace_client),
        patch.object(sentinel, "_handlers_by_type", return_value={"app": mock_handler}),
        patch.object(sentinel, "revalidate_violation", new=AsyncMock(return_value={"still_violates": True})),
        patch("app.db.session.get_lakebase_session", side_effect=lambda: _keep_open(db_session)),
    ):
        result = await sentinel.run_enforcement(db_session, req)

    assert result["apps_auto_stopped"] == 0
    mock_handler.stop_and_revoke.assert_not_called()

    audit_row = db_session.query(EnforcementAuditModel).filter(
        EnforcementAuditModel.request_id == req.id,
        EnforcementAuditModel.resource_id == "edh-ssc-prod",
    ).first()
    assert audit_row.executed_action == "skipped_protected"


@pytest.mark.asyncio
async def test_sentinel_run_enforcement_revalidate_abort(db_session):
    req = _make_request(db_session)

    replace_run_findings(
        db_session,
        req.id,
        checks=[],
        violations=[
            {
                "rule_id": "no_apps_enterprise_prod",
                "resource_id": "already-fixed-app",
                "resource_type": "app",
                "policy": "apps",
                "severity": "HIGH",
                "action": "KILL",
                "reason": "Violation was resolved just after discovery",
                "workspace": {"name": "prod-ws", "host": "https://prod.databricks.net", "environment": "prod"},
            }
        ],
    )

    mock_handler = MagicMock()
    mock_handler.workspace_client = MagicMock()
    mock_handler.stop_and_revoke = AsyncMock()

    with (
        patch.object(settings, "SENTINEL_AUTO_ENFORCE_APPS", True),
        patch.object(sentinel, "_new_workspace_client", return_value=mock_handler.workspace_client),
        patch.object(sentinel, "_handlers_by_type", return_value={"app": mock_handler}),
        # Live re-validation returns False (no longer violates!)
        patch.object(sentinel, "revalidate_violation", new=AsyncMock(return_value={"still_violates": False, "reason": "Allowlist exception approved"})),
        patch("app.db.session.get_lakebase_session", side_effect=lambda: _keep_open(db_session)),
    ):
        result = await sentinel.run_enforcement(db_session, req)

    assert result["apps_auto_stopped"] == 0
    mock_handler.stop_and_revoke.assert_not_called()

    audit_row = db_session.query(EnforcementAuditModel).filter(
        EnforcementAuditModel.request_id == req.id,
        EnforcementAuditModel.resource_id == "already-fixed-app",
    ).first()
    assert audit_row.executed_action == "aborted_revalidated"


# ---------------------------------------------------------------------------
# 5. Manual Endpoint Enforcement Tests
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_manual_enforce_protected_app_rejected(db_session):
    from fastapi import HTTPException
    from app.api.v1.requests import execute_enforcement_action, EnforcementActionRequest
    from app.models.user import User

    admin_user = User(
        id="usr-admin",
        email="admin@company.com",
        full_name="Admin User",
        roles=["Platform Admin", "Governance Admin"],
        groups=["admins"],
    )

    req = _make_request(db_session)
    body = EnforcementActionRequest(
        resource_id="edh-ssc-prod",
        resource_type="app",
        action="STOP_AND_REVOKE",
        policy_name="apps",
        reason="Manual enforcement test",
    )

    with pytest.raises(HTTPException) as exc_info:
        await execute_enforcement_action(
            request_id=req.id,
            body=body,
            current_user=admin_user,
            db=db_session,
        )

    assert exc_info.value.status_code == 400
    assert "protected" in exc_info.value.detail.lower()


@pytest.mark.asyncio
async def test_manual_enforce_stop_and_revoke_success(db_session):
    from app.api.v1.requests import execute_enforcement_action, EnforcementActionRequest
    from app.models.user import User

    admin_user = User(
        id="usr-admin",
        email="admin@company.com",
        full_name="Admin User",
        roles=["Platform Admin", "Governance Admin"],
        groups=["admins"],
    )

    req = _make_request(db_session)
    body = EnforcementActionRequest(
        resource_id="unauthorized-manual-app",
        resource_type="app",
        action="STOP_AND_REVOKE",
        policy_name="apps",
        reason="Violates enterprise prod policy",
    )

    mock_handler = MagicMock()
    mock_handler.stop_and_revoke = AsyncMock(return_value={
        "status": "success",
        "stopped": True,
        "permissions_revoked": True,
        "previous_acl": [{"user_name": "owner@company.com", "permission_level": "CAN_MANAGE"}],
        "creator": "owner@company.com",
    })

    with (
        patch("app.workflows.sentinel._new_workspace_client", return_value=MagicMock()),
        patch("app.workflows.sentinel.revalidate_violation", new=AsyncMock(return_value={"still_violates": True})),
        patch("app.providers.databricks.handlers.AppResourceHandler", return_value=mock_handler),
    ):
        res = await execute_enforcement_action(
            request_id=req.id,
            body=body,
            current_user=admin_user,
            db=db_session,
        )

    assert res["status"] == "success"
    assert "STOP_AND_REVOKE" in res["message"]
    mock_handler.stop_and_revoke.assert_called_once_with("unauthorized-manual-app")

    audit_row = db_session.query(EnforcementAuditModel).filter(
        EnforcementAuditModel.request_id == req.id,
        EnforcementAuditModel.resource_id == "unauthorized-manual-app",
    ).first()
    assert audit_row is not None
    assert audit_row.executed_action == "manual_stop_and_revoke"
    assert "Previous ACL snapshot:" in audit_row.reason


@pytest.mark.asyncio
async def test_manual_enforce_reinstate_success(db_session):
    from app.api.v1.requests import execute_enforcement_action, EnforcementActionRequest
    from app.models.user import User

    admin_user = User(
        id="usr-admin",
        email="admin@company.com",
        full_name="Admin User",
        roles=["Platform Admin", "Governance Admin"],
        groups=["admins"],
    )

    req = _make_request(db_session)

    # Seed prior audit record with previous ACL snapshot
    prior_audit = EnforcementAuditModel(
        id=str(uuid.uuid4()),
        request_id=req.id,
        resource_id="reinstated-app",
        resource_type="app",
        workspace="prod-ws",
        policy_name="apps",
        severity="MANUAL",
        intended_action="STOP_AND_REVOKE",
        executed_action="manual_stop_and_revoke",
        reason='Manually executed stop_and_revoke. Previous ACL snapshot: [{"user_name": "owner@company.com", "permission_level": "CAN_MANAGE"}].',
    )
    db_session.add(prior_audit)
    db_session.commit()

    body = EnforcementActionRequest(
        resource_id="reinstated-app",
        resource_type="app",
        action="REINSTATE",
        policy_name="apps",
        reason="Exception was granted",
    )

    mock_handler = MagicMock()
    mock_handler.reinstate_permissions = AsyncMock(return_value=True)

    with (
        patch("app.workflows.sentinel._new_workspace_client", return_value=MagicMock()),
        patch("app.workflows.sentinel.revalidate_violation", new=AsyncMock(return_value={"still_violates": True})),
        patch("app.providers.databricks.handlers.AppResourceHandler", return_value=mock_handler),
    ):
        res = await execute_enforcement_action(
            request_id=req.id,
            body=body,
            current_user=admin_user,
            db=db_session,
        )

    assert res["status"] == "success"
    assert "REINSTATE" in res["message"]
    mock_handler.reinstate_permissions.assert_called_once()
    assert mock_handler.reinstate_permissions.call_args[0][0] == "reinstated-app"
    assert mock_handler.reinstate_permissions.call_args.kwargs["original_acl"] == [
        {"user_name": "owner@company.com", "permission_level": "CAN_MANAGE"}
    ]

    audit_row = db_session.query(EnforcementAuditModel).filter(
        EnforcementAuditModel.request_id == req.id,
        EnforcementAuditModel.executed_action == "manual_reinstate",
    ).first()
    assert audit_row is not None

