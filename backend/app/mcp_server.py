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
import asyncio
import functools
import logging
from typing import Any, Dict, Optional, Tuple

from fastapi import HTTPException
from mcp.server.fastmcp import FastMCP

from app.api.identity import TOKEN_HEADER, forwarded_identity, identifiers
from app.core.config import dev_features_allowed, settings
from app.tools import get_external_tools
from app.tools.tool_executor import ToolContext, executor

logger = logging.getLogger(__name__)

# Initialize FastMCP Server. Stateless so any replica can serve any request
# (Databricks Apps may run >1 instance); the Streamable HTTP app is created in
# app.main via mcp.streamable_http_app() and its session manager is run there.
#
# `streamable_http_path="/"` is critical: FastMCP's streamable_http_app() serves
# the protocol on this path *within* its own ASGI app. Since app.main mounts it
# at "/mcp", leaving the default ("/mcp") would expose the endpoint at the doubled
# path "/mcp/mcp" (and "/mcp" would fall through to the SPA 404 handler). Setting
# it to "/" makes the mounted endpoint resolve to exactly "/mcp".
mcp = FastMCP(settings.PROJECT_NAME, stateless_http=True, streamable_http_path="/")


def _request_headers():
    try:
        request = mcp.get_context().request_context.request
    except Exception:
        return None
    return None if request is None else request.headers


def _resolve_identity(fwd: Dict[str, Any], obo_token: Optional[str]) -> Dict[str, Any]:
    """The caller's full identity (email, groups, app roles), resolved exactly as the API does."""
    from app.api.deps import resolve_user
    from app.db.session import get_session_local

    db = get_session_local()()
    try:
        user = resolve_user(
            db, email=fwd.get("email"), username=fwd.get("username"), forwarded=fwd,
            obo_token=obo_token, allow_service_principals=True,
        )
    finally:
        db.close()
    # Same shape as the chat agent's identity: roles and groups comma-joined.
    return {
        "email": user.email,
        "name": user.full_name,
        "roles": ", ".join(user.roles),
        "entitlements": ", ".join(user.entitlements),
        "actor_type": user.actor_type,
        "application_id": user.application_id,
        "channel": "mcp",
    }


async def _caller_identity() -> Tuple[Optional[str], Dict[str, Any], Optional[str]]:
    """``(obo_token, user_identity, refusal)`` for the live MCP request.

    The app's ``AuthMiddleware`` skips ``/mcp``, so the Databricks Apps forwarding
    headers are read straight off the request. ``refusal`` explains why no identity
    could be resolved; it is None when one was, or when there is no request context
    (e.g. stdio). There is no service-principal fallback: a tool that needs to know
    who is calling is refused when nobody was identified.
    """
    headers = _request_headers()
    if headers is None:
        return None, {}, None
    fwd = forwarded_identity(headers)
    obo_token = headers.get(TOKEN_HEADER) or None
    if not obo_token and settings.MOCK_USER_TOKEN and dev_features_allowed():
        obo_token = settings.MOCK_USER_TOKEN
    if not identifiers(fwd) and not dev_features_allowed():
        return obo_token, {}, "No caller identity was forwarded with this request."
    try:
        identity = await asyncio.to_thread(_resolve_identity, fwd, obo_token)
    except HTTPException as e:
        return obo_token, {}, str(e.detail)
    except Exception as e:  # noqa: BLE001 - identity resolution must not crash the endpoint
        logger.warning("MCP: could not resolve the caller's identity: %s", e)
        return obo_token, {}, "The caller's identity could not be resolved."
    return obo_token, identity, None


def _refusal(tool, user_identity: Dict[str, Any], refusal: Optional[str]) -> Optional[str]:
    if refusal and (tool.is_mutating or tool.requires_identity):
        return f"Tool '{tool.name}' needs to know who is calling. {refusal}"
    if user_identity.get("actor_type") == "service_principal" and not tool.allow_service_principals:
        return f"Service principals can't call '{tool.name}'."
    return None


def _governed_shim(tool):
    """Wrap a tool so external MCP calls go through the shared ToolExecutor.

    ``functools.wraps`` copies the underlying function's signature/annotations so
    FastMCP still generates the correct input schema, while execution is routed
    through governance (validation, OPA pre-flight for mutating tools, audit).
    The caller's OBO token and full identity (email, groups, app roles, and the
    principal for a registered service principal) are resolved from the request,
    so tools enforce the caller's own permissions. A mutating or identity-dependent
    tool is refused when no identity could be resolved; it never runs as the app's
    service principal on the caller's behalf.
    """
    @functools.wraps(tool._func)
    async def _shim(**kwargs):
        obo_token, user_identity, refusal = await _caller_identity()
        reason = _refusal(tool, user_identity, refusal)
        if reason:
            logger.warning("MCP: refused %s: %s", tool.name, reason)
            return {"error": reason, "status": "refused"}
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

