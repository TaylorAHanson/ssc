"""
MCP Server Adapter.

Exposes tools via the standard Model Context Protocol so they can be registered
as a custom MCP provider in Databricks AI Gateway and reused by other agents/apps.

Only tools that explicitly opt in with ``external=True`` (see ``app.tools.mcp.tool``)
are published here; all other tools stay app-internal.

Transport is **Streamable HTTP** (``/mcp``), which is what Databricks AI Gateway's
custom/external MCP registration expects. ``stateless_http=True`` avoids requiring
sticky sessions, which matters when the app runs as multiple Databricks App
replicas behind a load balancer. The server is mounted by ``app.main`` and its
session manager lifespan is started there.
"""
import functools
import logging

from mcp.server.fastmcp import FastMCP

from app.core.config import settings
from app.tools import get_external_tools
from app.tools.tool_executor import ToolContext, executor

logger = logging.getLogger(__name__)

# Initialize FastMCP Server. Stateless so any replica can serve any request
# (Databricks Apps may run >1 instance); the Streamable HTTP app is created in
# app.main via mcp.streamable_http_app() and its session manager is run there.
mcp = FastMCP(settings.PROJECT_NAME, stateless_http=True)


def _extract_obo_identity():
    """Pull the calling user's OBO token + email off the live MCP HTTP request.

    The app's ``AuthMiddleware`` deliberately skips ``/mcp`` (it has its own auth
    model), so ``request.state`` is *not* populated here — we read the Databricks
    Apps forwarding headers (``X-Forwarded-Access-Token`` / ``X-Forwarded-Email``)
    straight off the request instead. Returns ``(obo_token, user_identity)``;
    both empty when there's no request context (e.g. stdio) or no forwarded token
    (e.g. local dev), in which case the executor falls back to the SP.
    """
    try:
        request = mcp.get_context().request_context.request
    except Exception:
        return None, {}
    if request is None:
        return None, {}
    headers = request.headers
    obo_token = headers.get("x-forwarded-access-token") or None
    if not obo_token and settings.MOCK_USER_TOKEN:
        obo_token = settings.MOCK_USER_TOKEN
    email = headers.get("x-forwarded-email") or None
    identity = {"email": email} if email else {}
    return obo_token, identity


def _governed_shim(tool):
    """Wrap a tool so external MCP calls go through the shared ToolExecutor.

    ``functools.wraps`` copies the underlying function's signature/annotations so
    FastMCP still generates the correct input schema, while execution is routed
    through governance (validation, OPA pre-flight for mutating tools, audit).
    The calling user's OBO token/identity is lifted off the request so tools
    pinned to OBO enforce the *user's* Unity Catalog grants; when absent the
    ToolExecutor falls back to the Service Principal.
    """
    @functools.wraps(tool._func)
    async def _shim(**kwargs):
        obo_token, user_identity = _extract_obo_identity()
        ctx = ToolContext(
            tool_call_id=f"mcp:{tool.name}",
            obo_token=obo_token,
            user_identity=user_identity,
        )
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

