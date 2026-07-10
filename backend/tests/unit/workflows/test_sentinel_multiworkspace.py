"""End-to-end smoke tests for the multi-workspace Enforcement Sentinel scan.

These exercise :func:`run_discovery` against the in-memory test DB with the
Databricks/OPA boundaries stubbed, verifying that:
  * every configured target workspace is scanned (workspace-scoped handlers),
  * each violation/check is tagged with the workspace it came from,
  * a requested subset scopes the scan to just those workspaces,
  * the data-certification pass runs once (Unity Catalog scoped).
"""
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from app.core.workspaces import WorkspaceConfig
from app.db.request import RequestModel
import app.workflows.sentinel as sentinel


class _FakeComputeHandler:
    """Stand-in workspace-scoped handler; yields one cluster per workspace."""

    _counter = 0

    def __init__(self, client):
        self.client = client  # a per-workspace marker string

    async def discover(self):
        _FakeComputeHandler._counter += 1
        return [
            {
                "id": f"cluster-{self.client}",
                "type": "cluster",
                "name": f"cluster on {self.client}",
                "owner": "sp-123",
            }
        ]


class _FakeDatasetHandler:
    def __init__(self, client):
        self.client = client

    async def discover(self):
        return []  # keep the data-cert pass empty for these tests


class _FakeOpa:
    def __init__(self, *_a, **_k):
        pass

    async def evaluate_namespace(self, input_data):
        # One HIGH violation under a real policy name so it survives the
        # allowed_policy_names filter (compute_and_jobs.rego exists).
        return {
            "compute_and_jobs": {
                "is_violation": True,
                "action": "WARN",
                "severity": "HIGH",
                "reason": "unmanaged cluster",
                "violation_reasons": ["no auto-termination"],
                "rule_results": [{"id": "autotermination", "passed": False}],
            }
        }


def _make_request(db, state_context):
    req = RequestModel(
        id=f"req-{uuid.uuid4()}",
        type="enforcement_sentinel",
        title="Test Sentinel Run",
        status="processing",
        current_state="processing",
        state_context=state_context,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db.add(req)
    db.commit()
    return req


def _ws(name, host, env="prod"):
    return WorkspaceConfig(name=name, host=host, environment=env)


def _patches(workspaces):
    """Common patch set: workspace list, client factory, handlers, OPA."""
    return [
        patch("app.core.workspaces.get_target_workspaces", return_value=workspaces),
        # _new_workspace_client returns a per-host marker string used as the
        # "client" so we can assert which workspace each handler ran against.
        patch.object(sentinel, "_new_workspace_client", side_effect=lambda host=None: host or "home"),
        patch.object(sentinel, "_workspace_scoped_handler_classes", return_value=[_FakeComputeHandler]),
        patch("app.providers.databricks.handlers.DatasetResourceHandler", _FakeDatasetHandler),
        patch("app.providers.opa.client.OpaProvider", _FakeOpa),
    ]


@pytest.mark.asyncio
async def test_scans_all_configured_workspaces(db_session):
    workspaces = [
        _ws("prod-domain-a", "https://a.databricks.net"),
        _ws("prod-domain-b", "https://b.databricks.net"),
    ]
    req = _make_request(db_session, {})  # empty => scan all

    ctx = None
    plist = _patches(workspaces)
    for p in plist:
        p.start()
    try:
        result = await sentinel.run_discovery(db_session, req)
    finally:
        for p in plist:
            p.stop()

    ctx = req.state_context
    # One violation per workspace, each tagged with its own workspace name.
    ws_names = sorted(v["workspace"]["name"] for v in result["violations"])
    assert ws_names == ["prod-domain-a", "prod-domain-b"]
    assert set(ctx["workspaces_scanned"]) == {"prod-domain-a", "prod-domain-b"}
    assert result["violation_count"] == 2
    # Every check carries a workspace tag.
    assert all(c.get("workspace", {}).get("name") for c in result["checks"])


@pytest.mark.asyncio
async def test_requested_subset_scopes_the_scan(db_session):
    workspaces = [
        _ws("prod-domain-a", "https://a.databricks.net"),
        _ws("prod-domain-b", "https://b.databricks.net"),
    ]
    req = _make_request(db_session, {"workspaces": ["prod-domain-b"]})

    plist = _patches(workspaces)
    for p in plist:
        p.start()
    try:
        result = await sentinel.run_discovery(db_session, req)
    finally:
        for p in plist:
            p.stop()

    ws_names = [v["workspace"]["name"] for v in result["violations"]]
    assert ws_names == ["prod-domain-b"]
    assert req.state_context["workspaces_scanned"] == ["prod-domain-b"]


def test_prior_severity_is_scoped_by_workspace(db_session):
    """The same resource_id+policy in two workspaces must not be conflated, but
    legacy audit rows (workspace NULL) still dedup by (resource, policy)."""
    from app.db.enforcement_audit import EnforcementAuditModel

    def _audit(rid, policy, sev, ws, run):
        db_session.add(
            EnforcementAuditModel(
                id=str(uuid.uuid4()),
                request_id=run,
                resource_id=rid,
                resource_type="job",
                workspace=ws,
                policy_name=policy,
                severity=sev,
                intended_action="kill",
                executed_action="warn",
                reason="x",
            )
        )
        db_session.commit()

    # Prior HIGH recorded for workspace A only.
    _audit("job-1", "p", "HIGH", "ws-a", "run-old")
    # Same id+policy in workspace B is a *distinct* finding -> no prior.
    assert sentinel._prior_severity(db_session, "run-new", "job-1", "p", "ws-b") is None
    # Workspace A sees its own prior HIGH.
    assert sentinel._prior_severity(db_session, "run-new", "job-1", "p", "ws-a") == "HIGH"

    # Legacy row (workspace NULL) still matches any workspace lookup.
    _audit("job-2", "p", "HIGH", None, "run-old")
    assert sentinel._prior_severity(db_session, "run-new", "job-2", "p", "ws-b") == "HIGH"


@pytest.mark.asyncio
async def test_active_violations_surfaces_workspace(db_session):
    workspaces = [_ws("prod-domain-a", "https://a.databricks.net")]
    req = _make_request(db_session, {})

    plist = _patches(workspaces)
    for p in plist:
        p.start()
    try:
        await sentinel.run_discovery(db_session, req)
    finally:
        for p in plist:
            p.stop()

    rows = sentinel._active_violations(req.state_context)
    assert rows and rows[0]["workspace"] == "prod-domain-a"
