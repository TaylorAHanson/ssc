"""
Registry of V2 graph tools, keyed by name.

Data-defined specs reference a step's tool by **name** (a string) instead of an
imported Python object, so the registry resolves that name back to the concrete
:class:`~app.tools.mcp.McpTool` (or :class:`~app.tools.external.mcp_remote.RemoteMcpTool`).

Two sources contribute to the registry:

1. **Code-defined tools** — introspected from ``app.workflows.tools`` (hardcoded
   provider wrappers like ``grant_uc_access``, ``terraform_apply``, etc.).
2. **DB-registered MCP tools** — rows in ``ToolRegistryModel`` with
   ``enabled=True`` and ``enabled_for_workflow_execution=True``. These are tools
   discovered from remote MCP servers (via the Tool Registry UI) that an admin has
   enabled for workflow use. They are wrapped as ``RemoteMcpTool`` instances so they
   flow through the same ``ToolExecutor`` governance pipeline.

``available_tools()`` powers the authoring UI's tool picker (name + side-effect
class + whether it mutates) so an author can only wire steps to real tools.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. Code-defined tools (static, cached forever)
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _code_registry() -> Dict[str, Any]:
    """Tools defined in ``app.workflows.tools`` (provider wrappers)."""
    import app.workflows.tools as T
    from app.tools.mcp import McpTool

    reg: Dict[str, Any] = {}
    for attr in vars(T).values():
        if isinstance(attr, McpTool):
            reg[attr.name] = attr
    logger.debug("V2 code-tool registry built with %d tools", len(reg))
    return reg


# ---------------------------------------------------------------------------
# 2. DB-registered MCP tools (dynamic, queried on demand)
# ---------------------------------------------------------------------------

def _get_db_session():
    """Open a short-lived session for internal lookups (non-FastAPI contexts)."""
    from app.db.session import get_lakebase_session
    return get_lakebase_session()


def _load_db_tools(db) -> Dict[str, Any]:
    """Load enabled workflow MCP tools from DB and wrap as ``RemoteMcpTool``."""
    from app.db.tool_registry import ToolRegistryModel, McpSourceModel, TOOL_ORIGIN_MCP
    from app.tools.external.mcp_remote import RemoteMcpTool

    reg: Dict[str, Any] = {}
    try:
        rows = (
            db.query(ToolRegistryModel, McpSourceModel.server_url)
            .join(
                McpSourceModel,
                ToolRegistryModel.source_id == McpSourceModel.id,
                isouter=True,
            )
            .filter(
                ToolRegistryModel.enabled.is_(True),
                ToolRegistryModel.enabled_for_workflow_execution.is_(True),
                ToolRegistryModel.origin == TOOL_ORIGIN_MCP,
            )
            .all()
        )
        for tool_row, server_url in rows:
            if not server_url:
                continue
            reg[tool_row.tool_name] = RemoteMcpTool(
                name=tool_row.tool_name,
                server_url=server_url,
                description=tool_row.description or "",
                input_schema=tool_row.input_schema,
                is_mutating=tool_row.is_mutating,
                side_effect_class=tool_row.side_effect_class or "read",
                identity_mode=tool_row.identity_mode or "obo",
                success_predicate=tool_row.success_predicate,
            )
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to load DB tools for workflow registry: %s", e)
    return reg


def _db_tools(db: Optional[Any] = None) -> Dict[str, Any]:
    """Get DB-registered tools, opening a session if none provided."""
    if db is not None:
        return _load_db_tools(db)
    # No db provided — open our own short-lived session.
    session = _get_db_session()
    try:
        return _load_db_tools(session)
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_tool(name: str, db: Any = None) -> Any:
    """Return the tool for ``name`` or raise KeyError.

    Checks code-defined tools first, then DB-registered MCP tools.
    """
    # Fast path: code-defined tools (no DB hit)
    code = _code_registry()
    if name in code:
        return code[name]
    # Slow path: DB-registered tools
    db_tools = _db_tools(db)
    if name in db_tools:
        return db_tools[name]
    raise KeyError(f"unknown tool '{name}'")


def has_tool(name: str, db: Any = None) -> bool:
    """Check if a tool exists (code-defined OR DB-registered)."""
    if name in _code_registry():
        return True
    return name in _db_tools(db)


def all_tool_objects(db: Any = None) -> Dict[str, Any]:
    """All workflow tools as ``{name: tool}`` (code + DB)."""
    merged = dict(_code_registry())
    merged.update(_db_tools(db))
    return merged


def available_tools(db: Any = None) -> List[Dict[str, Any]]:
    """Metadata for every wireable tool (for the authoring UI / validation).

    Returns both code-defined and DB-registered MCP tools that are enabled for
    workflow execution. When ``db`` is provided, code-defined tools are filtered
    by their DB gating state (``enabled_for_workflow_execution``); DB-registered
    tools are already filtered by the query in ``_load_db_tools``.
    """
    # -- Code-defined tools (optionally filtered by DB state) --
    allowed_names = None
    if db is not None:
        try:
            from app.db.tool_registry import ToolRegistryModel

            rows = (
                db.query(ToolRegistryModel.tool_name)
                .filter(
                    ToolRegistryModel.enabled.is_(True),
                    ToolRegistryModel.enabled_for_workflow_execution.is_(True),
                )
                .all()
            )
            allowed_names = {r[0] for r in rows}
        except Exception:  # noqa: BLE001
            allowed_names = None

    out = []
    for name, t in sorted(_code_registry().items()):
        if allowed_names is not None and name not in allowed_names:
            continue
        accepted = getattr(t, "accepted_args", None)
        args = sorted(accepted["named"]) if accepted else []
        required = sorted(accepted["required"]) if accepted else []
        out.append({
            "name": name,
            "description": getattr(t, "description", ""),
            "side_effect_class": getattr(t, "side_effect_class", "read"),
            "is_mutating": getattr(t, "is_mutating", False),
            "external": getattr(t, "external", False),
            "args": args,
            "required_args": required,
        })

    # -- DB-registered MCP tools (already filtered by enabled + workflow_execution) --
    db_tool_map = _db_tools(db)
    for name, t in sorted(db_tool_map.items()):
        # Skip if already covered by a code-defined tool with the same name
        if name in _code_registry():
            continue
        accepted = getattr(t, "accepted_args", None)
        args = sorted(accepted["named"]) if accepted else []
        required = sorted(accepted["required"]) if accepted else []
        out.append({
            "name": name,
            "description": getattr(t, "description", ""),
            "side_effect_class": getattr(t, "side_effect_class", "read"),
            "is_mutating": getattr(t, "is_mutating", False),
            "external": getattr(t, "external", False),
            "args": args,
            "required_args": required,
        })
    return out
