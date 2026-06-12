"""Unified tool catalog: the single name -> McpTool resolver across local origins.

Two code locations define :class:`~app.tools.mcp.McpTool` objects:

- ``app.tools.**`` — chat-agent tools (auto-discovered into ``AVAILABLE_TOOLS``);
  catalog origin ``"local"``.
- ``app.workflows.tools`` — provider-backed workflow graph tools (explicitly NOT
  auto-discovered as chat tools); catalog origin ``"workflow"``.

The DB-backed Tool Registry governs *which usage contexts* (main agent, workflow
agent, workflow execution) each tool may be used in. This module is purely the
in-process resolver from a tool name to its concrete ``McpTool`` object plus its
origin — the thing that lets the registry treat both definition sites as one
catalog. Remote MCP tools (registry origin ``"mcp"``) are not resolved here; they
become ``RemoteMcpTool`` adapters built from registry rows.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

# Catalog origins (aligned with app.db.tool_registry.TOOL_ORIGIN_*).
CATALOG_ORIGIN_LOCAL = "local"
CATALOG_ORIGIN_WORKFLOW = "workflow"


def _local_tools() -> Dict[str, Any]:
    from app.tools import AVAILABLE_TOOLS

    return {t.name: t for t in AVAILABLE_TOOLS if getattr(t, "name", None)}


def _workflow_tools() -> Dict[str, Any]:
    try:
        from app.workflows.tool_registry import all_tool_objects

        return all_tool_objects()
    except Exception:  # noqa: BLE001 - workflow tools optional; degrade to chat-only
        return {}


def all_tools() -> List[Tuple[str, Any, str]]:
    """Return ``(name, tool, origin)`` for every locally-defined tool.

    On a name collision the chat (``local``) tool wins, since that is the
    historical default; tool names are expected to be disjoint across the two
    definition sites.
    """
    out: List[Tuple[str, Any, str]] = []
    seen: set = set()
    for name, tool in _local_tools().items():
        out.append((name, tool, CATALOG_ORIGIN_LOCAL))
        seen.add(name)
    for name, tool in _workflow_tools().items():
        if name in seen:
            continue
        out.append((name, tool, CATALOG_ORIGIN_WORKFLOW))
        seen.add(name)
    return out


def _index() -> Dict[str, Tuple[Any, str]]:
    return {name: (tool, origin) for name, tool, origin in all_tools()}


def get_by_name(name: str) -> Optional[Any]:
    """Resolve a tool name to its ``McpTool`` object (any local origin), or ``None``."""
    entry = _index().get(name)
    return entry[0] if entry else None


def origin_of(name: str) -> Optional[str]:
    """Return the catalog origin (``"local"`` | ``"workflow"``) for ``name``, or ``None``."""
    entry = _index().get(name)
    return entry[1] if entry else None
