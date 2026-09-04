"""Unit tests for the dynamic Tool Registry service + remote adapter.

Covers: local-tool seeding defaults across the three usage contexts (main agent,
workflow agent, workflow execution) including provider tools, per-tool update +
role/identity normalization, MCP source CRUD, mocked discovery, surface resolution
into executable tool objects, and the SP/OBO contract of the remote adapter.
"""
import uuid

import pytest

from app.db.tool_registry import (
    IDENTITY_OBO,
    IDENTITY_SP,
    TOOL_ORIGIN_LOCAL,
    TOOL_ORIGIN_MCP,
    TOOL_ORIGIN_WORKFLOW,
    ToolRegistryModel,
)
from app.models.user import User
from app.services.tool_registry_service import (
    DEFAULT_AUTHORING_TOOL_NAMES,
    WORKFLOW_ONLY_TOOL_NAMES,
    ToolRegistryService,
)


def _user(*roles: str) -> User:
    return User(id="u@example.com", email="u@example.com", full_name="U", roles=list(roles))


def _add_tool(db, **overrides):
    row = ToolRegistryModel(
        id=str(uuid.uuid4()),
        tool_name=overrides.get("tool_name", "demo_tool"),
        origin=overrides.get("origin", TOOL_ORIGIN_LOCAL),
        source_id=overrides.get("source_id"),
        description=overrides.get("description", ""),
        input_schema=overrides.get("input_schema"),
        is_mutating=overrides.get("is_mutating", False),
        side_effect_class=overrides.get("side_effect_class", "read"),
        enabled=overrides.get("enabled", True),
        enabled_for_main_agent=overrides.get("enabled_for_main_agent", False),
        enabled_for_workflow_agent=overrides.get("enabled_for_workflow_agent", False),
        enabled_for_workflow_execution=overrides.get("enabled_for_workflow_execution", False),
        allowed_roles=overrides.get("allowed_roles", []),
        identity_mode=overrides.get("identity_mode", IDENTITY_OBO),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


# --------------------------------------------------------------------- seeding
def test_sync_local_tools_seeds_and_separates_surfaces(db_session):
    inserted = ToolRegistryService.sync_local_tools(db_session)
    assert inserted > 0

    rows = (
        db_session.query(ToolRegistryModel)
        .filter(ToolRegistryModel.origin == TOOL_ORIGIN_LOCAL)
        .all()
    )
    assert rows, "expected local chat tools to be seeded"
    # Seeded contexts for chat tools: the authoring set -> workflow agent; main agent
    # gets everything except the workflow build/preview/publish tools. Chat tools are
    # never workflow-execution building blocks.
    for r in rows:
        assert r.enabled_for_workflow_agent is (r.tool_name in DEFAULT_AUTHORING_TOOL_NAMES)
        assert r.enabled_for_main_agent is (r.tool_name not in WORKFLOW_ONLY_TOOL_NAMES)
        assert r.enabled_for_workflow_execution is False

    by_name = {r.tool_name: r for r in rows}
    # Shared context-catalog tools are available to BOTH chat surfaces.
    if "search_context_catalog" in by_name:
        assert by_name["search_context_catalog"].enabled_for_main_agent is True
        assert by_name["search_context_catalog"].enabled_for_workflow_agent is True
    # A workflow build tool stays out of the main agent.
    if "preview_workflow_spec" in by_name:
        assert by_name["preview_workflow_spec"].enabled_for_main_agent is False
        assert by_name["preview_workflow_spec"].enabled_for_workflow_agent is True


def test_sync_local_tools_seeds_provider_tools_workflow_execution_only(db_session):
    ToolRegistryService.sync_local_tools(db_session)
    rows = (
        db_session.query(ToolRegistryModel)
        .filter(ToolRegistryModel.origin == TOOL_ORIGIN_WORKFLOW)
        .all()
    )
    assert rows, "expected provider/workflow tools to be seeded into the catalog"
    # Provider tools are workflow building blocks only: never chat-callable by default,
    # and they run as the Service Principal (no user token to act on behalf of).
    for r in rows:
        assert r.enabled_for_workflow_execution is True
        assert r.enabled_for_main_agent is False
        assert r.enabled_for_workflow_agent is False
        assert r.identity_mode == IDENTITY_SP
    names = {r.tool_name for r in rows}
    # Spot-check a known mutating provider tool is present and gated this way.
    assert "terraform_apply" in names
    assert "terramate_provision" in names


def test_sync_local_tools_updates_origin_change_to_workflow_defaults(db_session):
    # Simulate a tool that originally seeded as local (chat-callable, OBO)
    # moving to workflow origin (provider tool).
    row = ToolRegistryModel(
        id="test-moving-tool",
        tool_name="terramate_provision",
        origin=TOOL_ORIGIN_LOCAL,
        description="Legacy local registration",
        enabled=True,
        enabled_for_main_agent=True,
        enabled_for_workflow_agent=False,
        enabled_for_workflow_execution=False,
        identity_mode=IDENTITY_OBO,
    )
    db_session.add(row)
    db_session.commit()

    # When sync_local_tools runs, terramate_provision is now a workflow tool in code
    ToolRegistryService.sync_local_tools(db_session)

    refreshed = db_session.query(ToolRegistryModel).filter_by(tool_name="terramate_provision").first()
    assert refreshed.origin == TOOL_ORIGIN_WORKFLOW
    assert refreshed.enabled_for_main_agent is False
    assert refreshed.enabled_for_workflow_execution is True
    assert refreshed.identity_mode == IDENTITY_SP


def test_sync_local_tools_is_idempotent(db_session):
    first = ToolRegistryService.sync_local_tools(db_session)
    assert first > 0
    # A second pass inserts nothing new (refresh-only) and preserves admin edits.
    second = ToolRegistryService.sync_local_tools(db_session)
    assert second == 0


def test_sync_preserves_admin_gating(db_session):
    ToolRegistryService.sync_local_tools(db_session)
    row = (
        db_session.query(ToolRegistryModel)
        .filter(ToolRegistryModel.origin == TOOL_ORIGIN_LOCAL)
        .first()
    )
    ToolRegistryService.update_tool(
        db_session, row.id, enabled_for_main_agent=False, enabled_for_workflow_agent=True
    )
    ToolRegistryService.sync_local_tools(db_session)
    refreshed = ToolRegistryService.get_tool(db_session, row.id)
    assert refreshed.enabled_for_main_agent is False
    assert refreshed.enabled_for_workflow_agent is True


# ---------------------------------------------------------------- surface query
def test_tools_for_surface_filters_by_surface(db_session):
    _add_tool(db_session, tool_name="main_only", enabled_for_main_agent=True)
    _add_tool(db_session, tool_name="wf_only", enabled_for_workflow_agent=True)
    _add_tool(db_session, tool_name="exec_only", enabled_for_workflow_execution=True)

    main = {r.tool_name for r in ToolRegistryService.tools_for_surface(db_session, "main_agent", _user("User"))}
    wf = {r.tool_name for r in ToolRegistryService.tools_for_surface(db_session, "workflow_agent", _user("User"))}
    exe = {r.tool_name for r in ToolRegistryService.tools_for_surface(db_session, "workflow_execution", _user("User"))}
    assert "main_only" in main and "wf_only" not in main and "exec_only" not in main
    assert "wf_only" in wf and "main_only" not in wf and "exec_only" not in wf
    assert "exec_only" in exe and "main_only" not in exe and "wf_only" not in exe

    # Legacy surface aliases still resolve to the renamed columns.
    main_alias = {r.tool_name for r in ToolRegistryService.tools_for_surface(db_session, "edh", _user("User"))}
    wf_alias = {r.tool_name for r in ToolRegistryService.tools_for_surface(db_session, "workflow", _user("User"))}
    assert "main_only" in main_alias and "wf_only" in wf_alias


def test_tools_for_surface_role_gated(db_session):
    _add_tool(db_session, tool_name="admin_tool", enabled_for_main_agent=True, allowed_roles=["Governance Admin"])

    no_access = {r.tool_name for r in ToolRegistryService.tools_for_surface(db_session, "main_agent", _user("User"))}
    assert "admin_tool" not in no_access

    gov = {r.tool_name for r in ToolRegistryService.tools_for_surface(db_session, "main_agent", _user("Governance Admin"))}
    assert "admin_tool" in gov

    # Platform Admin is a super-role and passes every gate.
    plat = {r.tool_name for r in ToolRegistryService.tools_for_surface(db_session, "main_agent", _user("Platform Admin"))}
    assert "admin_tool" in plat


def test_disabled_master_switch_hides_tool(db_session):
    _add_tool(db_session, tool_name="off", enabled=False, enabled_for_main_agent=True)
    main = {r.tool_name for r in ToolRegistryService.tools_for_surface(db_session, "main_agent", _user("Platform Admin"))}
    assert "off" not in main


# --------------------------------------------------------------------- updates
def test_update_tool_normalizes_roles(db_session):
    row = _add_tool(db_session, tool_name="t1", enabled_for_main_agent=True)
    updated = ToolRegistryService.update_tool(db_session, row.id, allowed_roles=["governance_admin", "user"])
    assert set(updated.allowed_roles) == {"Governance Admin", "User"}


def test_update_tool_rejects_bad_identity(db_session):
    row = _add_tool(db_session, tool_name="t2")
    with pytest.raises(ValueError):
        ToolRegistryService.update_tool(db_session, row.id, identity_mode="root")


# ----------------------------------------------------------------- source CRUD
def test_source_crud(db_session):
    src = ToolRegistryService.create_source(
        db_session, name="UC ai", server_url="https://h/api/2.0/mcp/functions/system/ai"
    )
    assert src.id and src.enabled
    with pytest.raises(ValueError):
        ToolRegistryService.create_source(db_session, name="UC ai", server_url="https://h/x")

    ToolRegistryService.update_source(db_session, src.id, enabled=False)
    assert ToolRegistryService.get_source(db_session, src.id).enabled is False

    ToolRegistryService.delete_source(db_session, src.id)
    assert ToolRegistryService.get_source(db_session, src.id) is None


# ------------------------------------------------------------------ discovery
def test_discover_source_upserts_disabled_tools(db_session, monkeypatch):
    src = ToolRegistryService.create_source(
        db_session, name="genie", server_url="https://h/api/2.0/mcp/genie"
    )

    fake = [
        {"name": "genie_ask", "description": "ask", "input_schema": {"type": "object"}, "is_mutating": False, "side_effect_class": "read"},
        {"name": "genie_poll_response", "description": "poll", "input_schema": {"type": "object"}, "is_mutating": False, "side_effect_class": "read"},
    ]
    monkeypatch.setattr("app.tools.external.mcp_client.list_tools", lambda url, obo_token=None: fake)

    result = ToolRegistryService.discover_source(db_session, src.id)
    assert result["ok"] and result["count"] == 2

    rows = (
        db_session.query(ToolRegistryModel)
        .filter(ToolRegistryModel.source_id == src.id)
        .all()
    )
    assert {r.tool_name for r in rows} == {"genie_ask", "genie_poll_response"}
    # Newly discovered tools are opt-in: disabled + unassigned until an admin enables.
    for r in rows:
        assert r.origin == TOOL_ORIGIN_MCP
        assert r.enabled is False
        assert r.enabled_for_main_agent is False
        assert r.enabled_for_workflow_agent is False
        assert r.enabled_for_workflow_execution is False

    refreshed_src = ToolRegistryService.get_source(db_session, src.id)
    assert refreshed_src.last_sync_status == "ok" and refreshed_src.last_tool_count == 2


def test_discover_source_records_error(db_session, monkeypatch):
    src = ToolRegistryService.create_source(db_session, name="bad", server_url="https://h/api/2.0/mcp/sql")

    def boom(url, obo_token=None):
        raise RuntimeError("connection refused")

    monkeypatch.setattr("app.tools.external.mcp_client.list_tools", boom)
    result = ToolRegistryService.discover_source(db_session, src.id)
    assert result["ok"] is False and "connection refused" in result["error"]
    assert ToolRegistryService.get_source(db_session, src.id).last_sync_status == "error"


# ----------------------------------------------------------------- resolution
def test_resolve_returns_local_tool_objects(db_session):
    from app.tools import AVAILABLE_TOOLS

    assert AVAILABLE_TOOLS, "expected at least one local tool to be loaded"
    sample = AVAILABLE_TOOLS[0]
    _add_tool(db_session, tool_name=sample.name, origin=TOOL_ORIGIN_LOCAL, enabled_for_main_agent=True)

    resolved = ToolRegistryService.resolve_tools_for_surface(db_session, "main_agent", _user("Platform Admin"))
    assert any(getattr(t, "name", None) == sample.name for t in resolved)


def test_resolve_returns_provider_tool_objects(db_session):
    from app.tools import catalog

    names = {n for n, _t, origin in catalog.all_tools() if origin == TOOL_ORIGIN_WORKFLOW}
    assert names, "expected provider/workflow tools in the catalog"
    name = sorted(names)[0]
    _add_tool(db_session, tool_name=name, origin=TOOL_ORIGIN_WORKFLOW, enabled_for_workflow_execution=True)

    resolved = ToolRegistryService.resolve_tools_for_surface(db_session, "workflow_execution", _user("Platform Admin"))
    assert any(getattr(t, "name", None) == name for t in resolved)


def test_resolve_builds_remote_adapter(db_session):
    from app.tools.external.mcp_remote import RemoteMcpTool

    src = ToolRegistryService.create_source(db_session, name="ext", server_url="https://h/api/2.0/mcp/external/conn")
    _add_tool(
        db_session,
        tool_name="remote_thing",
        origin=TOOL_ORIGIN_MCP,
        source_id=src.id,
        enabled_for_main_agent=True,
        identity_mode=IDENTITY_SP,
    )
    resolved = ToolRegistryService.resolve_tools_for_surface(db_session, "main_agent", _user("Platform Admin"))
    adapters = [t for t in resolved if isinstance(t, RemoteMcpTool)]
    assert len(adapters) == 1
    assert adapters[0].name == "remote_thing"


# --------------------------------------------------------------- remote adapter
@pytest.mark.asyncio
async def test_remote_adapter_forwards_identity_and_strips_context(monkeypatch):
    from app.tools.external.mcp_remote import RemoteMcpTool

    captured = {}

    def fake_call(server_url, tool_name, arguments, *, identity_mode, obo_token=None):
        captured.update(
            server_url=server_url,
            tool_name=tool_name,
            arguments=arguments,
            identity_mode=identity_mode,
            obo_token=obo_token,
        )
        return {"ok": True, "result": "done"}

    monkeypatch.setattr("app.tools.external.mcp_client.call_tool", fake_call)

    tool = RemoteMcpTool(name="remote_thing", server_url="https://h/mcp", identity_mode="obo")
    out = await tool.execute(query="hi", _obo_token="tok-123", _user_email="u@example.com")

    assert out == {"ok": True, "result": "done"}
    assert captured["tool_name"] == "remote_thing"
    assert captured["identity_mode"] == "obo"
    assert captured["obo_token"] == "tok-123"
    # Executor-injected context keys never go over the wire.
    assert captured["arguments"] == {"query": "hi"}
