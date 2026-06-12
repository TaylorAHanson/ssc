"""
Registry of V2 graph tools, keyed by name.

Data-defined specs reference a step's tool by **name** (a string) instead of an
imported Python object, so the registry resolves that name back to the concrete
:class:`~app.tools.mcp.McpTool`. It is built by introspecting ``app.workflows.tools``
for decorated tools, which keeps it automatically in sync as tools are added.

``available_tools()`` powers the authoring UI's tool picker (name + side-effect
class + whether it mutates) so an author can only wire steps to real tools.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _registry() -> Dict[str, Any]:
    import app.workflows.tools as T
    from app.tools.mcp import McpTool

    reg: Dict[str, Any] = {}
    for attr in vars(T).values():
        if isinstance(attr, McpTool):
            reg[attr.name] = attr
    logger.debug("V2 tool registry built with %d tools", len(reg))
    return reg


def get_tool(name: str) -> Any:
    """Return the McpTool for ``name`` or raise KeyError (caught by the validator)."""
    try:
        return _registry()[name]
    except KeyError:
        raise KeyError(f"unknown tool '{name}'")


def has_tool(name: str) -> bool:
    return name in _registry()


def all_tool_objects() -> Dict[str, Any]:
    """All workflow/provider tools as ``{name: McpTool}`` (for the unified catalog)."""
    return dict(_registry())


def available_tools(db: Any = None) -> List[Dict[str, Any]]:
    """Metadata for every wireable tool (for the authoring UI / validation).

    When ``db`` is provided, the list is filtered to tools the Tool Registry has
    enabled for workflow execution (``enabled_for_workflow_execution``) so an admin
    can globally retire a building block. ``get_tool``/``has_tool`` are intentionally
    NOT filtered, so already-published specs keep resolving even if a building block
    is later disabled. With no ``db`` the full code catalog is returned (safe default).
    """
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
        except Exception:  # noqa: BLE001 - never break the picker on a registry hiccup
            allowed_names = None

    out = []
    for name, t in sorted(_registry().items()):
        if allowed_names is not None and name not in allowed_names:
            continue
        accepted = getattr(t, "accepted_args", None)
        # Expose the tool's real arg names so authors (UI + agent) wire correct
        # args instead of guessing (e.g. `to_email`, not `to`). Tools that only
        # take **kwargs report an empty arg list (open contract).
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
