"""Tests for DB-backed graph resolution (the no-code execution override).

A published workflow's ``graph_spec`` must win over the code catalog; a missing or
malformed DB spec must fall back to code so execution never breaks.
"""
from app.services.workflow_service import WorkflowService
from app.v2.graphs import build_graph_for, published_graph_spec

_MINIMAL_SPEC = {
    "name": "workspace_access",
    "complete_fact": "access_granted",
    "stages": [
        {"kind": "gate", "name": "manager_approval", "type": "manager"},
        {"kind": "step", "name": "provision", "tool": "add_group_membership",
         "approvals": ["manager"], "success_fact": "access_granted",
         "args": {"group": {"$var": "workspace"},
                  "members": {"$list": [{"$var": "requested_by_email"}]}}},
    ],
}


def test_published_graph_spec_returns_db_spec(db_session):
    WorkflowService.create(
        db_session, key="workspace_access", name="WS Access",
        request_type="workspace_access", graph_spec=_MINIMAL_SPEC, status="published",
    )
    spec = published_graph_spec(db_session, "workspace_access")
    assert spec == _MINIMAL_SPEC


def test_published_graph_spec_ignores_drafts(db_session):
    WorkflowService.create(
        db_session, key="workspace_access", name="WS",
        request_type="workspace_access", graph_spec=_MINIMAL_SPEC, status="draft",
    )
    assert published_graph_spec(db_session, "workspace_access") is None


def test_build_graph_for_prefers_db_then_falls_back(db_session):
    # No DB session -> code catalog (does not raise).
    assert build_graph_for("workspace_access", None) is not None

    # Published DB spec -> builds from it.
    WorkflowService.create(
        db_session, key="workspace_access", name="WS",
        request_type="workspace_access", graph_spec=_MINIMAL_SPEC, status="published",
    )
    assert build_graph_for("workspace_access", db_session) is not None


def test_build_graph_for_falls_back_on_invalid_db_spec(db_session):
    WorkflowService.create(
        db_session, key="workspace_access", name="WS",
        request_type="workspace_access",
        graph_spec={"name": "x", "stages": [{"kind": "step", "name": "s", "tool": "ghost"}]},
        status="published",
    )
    # Invalid graph_spec must not raise; resolution falls back to the code catalog.
    assert build_graph_for("workspace_access", db_session) is not None
