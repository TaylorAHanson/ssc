"""
MCP Server Adapter.

Exposes tools via the standard Model Context Protocol so they can be registered
as a custom MCP provider in Databricks AI Gateway and reused by other agents/apps.

Only tools that explicitly opt in with ``external=True`` (see ``app.tools.mcp.tool``)
are published here; all other tools stay app-internal.
"""
import functools
import logging
from mcp.server.fastmcp import FastMCP
from app.core.config import settings
from app.tools import get_external_tools
from app.tools.tool_executor import ToolContext, executor

logger = logging.getLogger(__name__)

# Initialize FastMCP Server
mcp = FastMCP(settings.PROJECT_NAME)


def _governed_shim(tool):
    """Wrap a tool so external MCP calls go through the shared ToolExecutor.

    ``functools.wraps`` copies the underlying function's signature/annotations so
    FastMCP still generates the correct input schema, while execution is routed
    through governance (validation, OPA pre-flight for mutating tools, audit).
    Identity (OBO) propagation from the MCP transport is a later milestone; for
    now external calls run without an injected user token.
    """
    @functools.wraps(tool._func)
    async def _shim(**kwargs):
        ctx = ToolContext(tool_call_id=f"mcp:{tool.name}")
        return await executor.run(tool, ctx, **kwargs)
    return _shim


# Register only externally-exposed tools (per-tool `external` switch), each
# routed through the ToolExecutor.
_external_tools = get_external_tools()
for tool in _external_tools:
    if hasattr(tool, "_func"):
        try:
            mcp.tool(name=tool.name, description=tool.description)(_governed_shim(tool))
            logger.info(f"Registered external MCP tool: {tool.name}")
        except Exception as e:
            logger.warning(f"Failed to register tool {tool.name} with MCP: {e}")
    else:
        logger.warning(f"Skipping tool {tool.name}: No _func attribute found")

logger.info(
    f"MCP server exposes {len(_external_tools)} external tool(s): "
    f"{[t.name for t in _external_tools]}"
)

