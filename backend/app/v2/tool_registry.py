"""
Registry of V2 graph tools, keyed by name.

Data-defined specs reference a step's tool by **name** (a string) instead of an
imported Python object, so the registry resolves that name back to the concrete
:class:`~app.tools.mcp.McpTool`. It is built by introspecting ``app.v2.tools``
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
    import app.v2.tools as T
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


def available_tools() -> List[Dict[str, Any]]:
    """Metadata for every wireable tool (for the authoring UI / validation)."""
    out = []
    for name, t in sorted(_registry().items()):
        out.append({
            "name": name,
            "description": getattr(t, "description", ""),
            "side_effect_class": getattr(t, "side_effect_class", "read"),
            "is_mutating": getattr(t, "is_mutating", False),
            "external": getattr(t, "external", False),
        })
    return out
