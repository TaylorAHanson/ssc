"""Agent-side assembly test: the registry drives EDH vs authoring tool sets.

Verifies the wiring in ``app/api/v1/agent.py`` resolves tools per chat surface
from the Tool Registry (replacing the old static ``required_role`` filter +
``_AUTHORING_TOOL_NAMES`` whitelist).
"""
from app.api.v1.agent import _resolve_visible_tools
from app.models.user import User
from app.services.tool_registry_service import ToolRegistryService


def _admin() -> User:
    return User(id="a@example.com", email="a@example.com", full_name="A", roles=["Platform Admin"])


def test_surfaces_get_distinct_tool_sets(db_session):
    ToolRegistryService.sync_local_tools(db_session)
    admin = _admin()

    edh_names = {getattr(t, "name", None) for t in _resolve_visible_tools(db_session, admin, "edh")}
    wf_names = {getattr(t, "name", None) for t in _resolve_visible_tools(db_session, admin, "workflow")}

    # Authoring building block lives on the workflow surface, not EDH.
    assert "save_workflow_draft" in wf_names
    assert "save_workflow_draft" not in edh_names

    # A runtime/provisioning tool lives on EDH, not the authoring surface.
    assert "execute_workflow" in edh_names
    assert "execute_workflow" not in wf_names

    # Shared general-purpose tools (context catalog) are available to BOTH surfaces.
    assert "search_context_catalog" in edh_names
    assert "search_context_catalog" in wf_names


def test_resolution_falls_back_on_registry_error(monkeypatch, db_session):
    def boom(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(
        "app.services.tool_registry_service.ToolRegistryService.resolve_tools_for_surface",
        boom,
    )
    # Falls back to the static gating rather than leaving the agent tool-less.
    tools = _resolve_visible_tools(db_session, _admin(), "edh")
    assert isinstance(tools, list) and len(tools) > 0
