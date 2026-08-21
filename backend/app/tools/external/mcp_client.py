"""Databricks MCP discovery + invocation helpers.

Thin wrappers around the Databricks SDK + ``databricks_mcp`` so the rest of the
app can list and call tools on a managed/external MCP server without caring about
identity plumbing. Two identities are supported:

- **SP** (Service Principal): the app's own OAuth M2M credentials. Used for tool
  *discovery* (listing what the SP can see) and for tools an admin pins to
  ``identity_mode='sp'``.
- **OBO** (On-Behalf-Of): a forwarded user access token. Used for tools an admin
  pins to ``identity_mode='obo'`` so Unity Catalog enforces the *user's* grants.

``databricks_mcp`` is imported lazily so importing this module never hard-fails in
environments where the optional dependency isn't installed.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

from app.core.config import settings

logger = logging.getLogger(__name__)


class McpDependencyError(RuntimeError):
    """Raised when MCP functionality is used but ``databricks_mcp`` is missing."""


def _host() -> str:
    host = (settings.DATABRICKS_HOST or settings.DATABRICKS_WORKSPACE_URL or "").strip().rstrip("/")
    if host and not host.startswith(("https://", "http://")):
        host = f"https://{host}"
    return host


class _PlatformOAuthStrategy:
    """CredentialsStrategy that wraps platform-injected auth as an ``OAuthCredentialsProvider``.

    ``DatabricksMCPClient`` validates OAuth by calling ``config.oauth_token()`` which
    checks ``isinstance(config._header_factory, OAuthCredentialsProvider)``. The SDK's
    built-in auth (``'runtime'``, ``'pat'``, etc.) does NOT produce an
    ``OAuthCredentialsProvider`` — even though the actual token IS OAuth inside a
    Databricks App. This strategy wraps the auto-detected client's
    ``config.authenticate()`` so the header factory satisfies the isinstance check.
    """

    def __init__(self, inner_ws):
        self._inner_ws = inner_ws

    def auth_type(self) -> str:
        return "oauth-m2m"

    def __call__(self, cfg):
        from databricks.sdk.credentials_provider import OAuthCredentialsProvider
        from databricks.sdk.oauth import Token as OAuthToken

        inner = self._inner_ws

        def _headers():
            return inner.config.authenticate()

        def _token():
            hdrs = inner.config.authenticate()
            access_token = hdrs.get("Authorization", "").replace("Bearer ", "")
            return OAuthToken(access_token=access_token, token_type="Bearer")

        return OAuthCredentialsProvider(_headers, _token)


def build_sp_workspace_client():
    """A ``WorkspaceClient`` authenticated as the app Service Principal.

    Prefers explicit M2M client_id/secret when configured; otherwise relies on the
    SDK's default auth chain. Inside a Databricks App, wraps the auto-detected auth
    with ``OAuthCredentialsProvider`` so ``DatabricksMCPClient``'s
    ``config.oauth_token()`` check passes.
    """
    from databricks.sdk import WorkspaceClient
    from databricks.sdk.config import Config
    import os

    host = _host()
    client_id = (settings.DATABRICKS_CLIENT_ID or "").strip()
    client_secret = (settings.DATABRICKS_CLIENT_SECRET or "").strip()

    # Explicit OAuth M2M credentials take precedence.
    if host and client_id and client_secret:
        return WorkspaceClient(host=host, client_id=client_id, client_secret=client_secret)

    # Inside a Databricks App: create an inner client that auto-detects platform
    # credentials, then wrap it so _header_factory is an OAuthCredentialsProvider
    # (required by DatabricksMCPClient's oauth_token() isinstance check).
    if os.environ.get("DATABRICKS_APP_PORT"):
        inner_ws = WorkspaceClient()
        logger.info(
            "build_sp_workspace_client (app env): inner auth_type='%s'",
            getattr(inner_ws.config, 'auth_type', '?'),
        )
        config = Config(
            host=inner_ws.config.host,
            credentials_strategy=_PlatformOAuthStrategy(inner_ws),
        )
        return WorkspaceClient(config=config)

    # Local development: SDK picks up auth from .databrickscfg or env vars.
    if host:
        return WorkspaceClient(host=host)
    return WorkspaceClient()


class _OboOAuthCredentials:
    """CredentialsStrategy that wraps a forwarded OAuth token as ``OAuthCredentialsProvider``.

    ``DatabricksMCPClient`` validates OAuth via ``config.oauth_token()`` which checks
    ``isinstance(config._header_factory, OAuthCredentialsProvider)``. A plain lambda
    returning headers does NOT satisfy this. We return a proper
    ``OAuthCredentialsProvider`` so the isinstance check passes.
    """

    def __init__(self, token: str):
        self._token = token

    def auth_type(self) -> str:
        return "oauth-m2m"

    def __call__(self, cfg):
        from databricks.sdk.credentials_provider import OAuthCredentialsProvider
        from databricks.sdk.oauth import Token as OAuthToken

        token = self._token

        def _headers():
            return {"Authorization": f"Bearer {token}"}

        def _token_provider():
            return OAuthToken(access_token=token, token_type="Bearer")

        return OAuthCredentialsProvider(_headers, _token_provider)


def build_obo_workspace_client(token: str):
    """A ``WorkspaceClient`` authenticated as the user via a forwarded OBO token.

    The forwarded access token from Databricks Apps IS an OAuth access token.
    Using ``auth_type='pat'`` makes ``DatabricksMCPClient`` reject it (custom and
    external MCP servers require OAuth). Instead we supply a proper
    ``CredentialsStrategy`` that reports ``auth_type='oauth-obo'`` and sends the
    token as ``Authorization: Bearer ...``.

    Explicitly passing empty ``client_id``/``client_secret`` suppresses the
    "more than one authorization method" error that would otherwise fire when
    the platform-injected SP OAuth env vars are present alongside the token.
    """
    from databricks.sdk import WorkspaceClient
    from databricks.sdk.config import Config

    host = _host()
    config = Config(
        host=host,
        credentials_strategy=_OboOAuthCredentials(token),
        # Suppress platform-injected SP creds to avoid multi-auth conflict.
        client_id="",
        client_secret="",
    )
    return WorkspaceClient(config=config)


def _mcp_client(server_url: str, workspace_client):
    try:
        from databricks_mcp import DatabricksMCPClient
    except Exception as e:  # noqa: BLE001
        raise McpDependencyError(
            "The 'databricks-mcp' package is required for MCP tool discovery/invocation. "
            "Install it (see backend/requirements.txt) to enable external tools."
        ) from e
    return DatabricksMCPClient(server_url=server_url, workspace_client=workspace_client)


def _tool_to_dict(tool: Any) -> Dict[str, Any]:
    """Normalize an MCP ``Tool`` object into the shape the registry stores."""
    name = getattr(tool, "name", None) or ""
    description = getattr(tool, "description", "") or ""
    # MCP tools expose a JSON Schema as ``inputSchema`` (camelCase) per the spec.
    input_schema = (
        getattr(tool, "inputSchema", None)
        or getattr(tool, "input_schema", None)
        or {"type": "object", "properties": {}}
    )
    # Annotations may carry a ``readOnlyHint``; when explicitly read-only we can
    # safely classify as a non-mutating read. Anything else stays conservative.
    is_mutating = False
    annotations = getattr(tool, "annotations", None)
    if annotations is not None:
        read_only = getattr(annotations, "readOnlyHint", None)
        if read_only is None and isinstance(annotations, dict):
            read_only = annotations.get("readOnlyHint")
        if read_only is False:
            is_mutating = True
    return {
        "name": name,
        "description": description,
        "input_schema": input_schema if isinstance(input_schema, dict) else {},
        "is_mutating": is_mutating,
        "side_effect_class": "infra" if is_mutating else "read",
    }


def _readable_exc(exc: BaseException) -> str:
    """Flatten anyio/MCP ``ExceptionGroup``s into a human-readable cause string.

    ``DatabricksMCPClient`` runs its HTTP calls inside an anyio task group, so the
    real cause (e.g. ``403 Forbidden`` when the identity lacks ``USE CONNECTION``)
    surfaces only as "unhandled errors in a TaskGroup (1 sub-exception)". Unwrap
    the leaves so the source's ``last_sync_error`` shows the actual reason.
    """
    subs = getattr(exc, "exceptions", None)
    if subs:
        return "; ".join(_readable_exc(s) for s in subs)
    return f"{type(exc).__name__}: {exc}"


def list_tools(server_url: str, obo_token: Optional[str] = None) -> List[Dict[str, Any]]:
    """List tools on ``server_url`` under a single identity.

    When ``obo_token`` is provided the listing runs On-Behalf-Of the user, so
    AI-Gateway external MCP servers registered with Per-User OAuth (which the
    Service Principal cannot see) are discoverable under the caller's identity;
    otherwise it runs as the Service Principal. There is intentionally no
    cross-identity fallback — discovery reflects exactly what the chosen identity
    can see. Returns normalized dicts; raises a ``RuntimeError`` with the
    unwrapped cause on connection/auth errors so the caller can record a useful
    sync failure.
    """
    ws = build_obo_workspace_client(obo_token) if obo_token else build_sp_workspace_client()
    label = "OBO" if obo_token else "SP"
    logger.info("list_tools [%s]: identity=%s", server_url, label)
    client = _mcp_client(server_url, ws)
    try:
        raw = client.list_tools()
    except Exception as e:  # noqa: BLE001 - normalize the opaque TaskGroup wrapper
        raise RuntimeError(_readable_exc(e)) from e
    return [_tool_to_dict(t) for t in (raw or [])]


def _list_mcp_servers_with_client(ws, host: str) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Core discovery logic using a pre-built WorkspaceClient.

    Returns ``(sources, errors)``: each server kind is listed independently so one
    failing kind never hides the others, and the per-kind failure reasons come back
    with the results instead of being swallowed.
    """
    out: List[Dict[str, Any]] = []
    errors: List[str] = []

    # External MCP == Unity Catalog HTTP connections (AI Gateway registration).
    try:
        from databricks.sdk.service.catalog import ConnectionType

        for conn in ws.connections.list():
            if getattr(conn, "connection_type", None) != ConnectionType.HTTP:
                continue
            name = conn.name
            out.append({
                "name": name,
                "server_url": f"{host}/api/2.0/mcp/external/{name}",
                "kind": "external",
                "detail": (getattr(conn, "comment", None) or getattr(conn, "url", None) or "HTTP connection"),
            })
    except Exception as e:  # noqa: BLE001 - one source kind failing must not block others
        errors.append(f"connections: {e}")
        logger.warning("list_workspace_mcp_servers: connections failed: %s", e)

    # Managed Genie MCP servers.
    try:
        resp = ws.genie.list_spaces()
        spaces = getattr(resp, "spaces", None) or []
        for space in spaces:
            sid = getattr(space, "space_id", None) or getattr(space, "id", None)
            if not sid:
                continue
            title = getattr(space, "title", None) or getattr(space, "name", None) or sid
            out.append({
                "name": f"Genie: {title}",
                "server_url": f"{host}/api/2.0/mcp/genie/{sid}",
                "kind": "genie",
                "detail": f"Genie space {sid}",
            })
    except Exception as e:  # noqa: BLE001
        errors.append(f"genie: {e}")
        logger.warning("list_workspace_mcp_servers: genie failed: %s", e)

    # Custom MCP servers hosted as Databricks Apps (naming convention: mcp-*).
    try:
        for app in ws.apps.list():
            app_name = getattr(app, "name", "") or ""
            app_url = getattr(app, "url", None)
            if not (app_url and app_name.startswith("mcp-")):
                continue
            out.append({
                "name": app_name,
                "server_url": app_url.rstrip("/") + "/mcp",
                "kind": "custom_app",
                "detail": getattr(app, "description", None) or "Databricks App",
            })
    except Exception as e:  # noqa: BLE001
        errors.append(f"apps: {e}")
        logger.warning("list_workspace_mcp_servers: apps failed: %s", e)

    return out, errors


class McpDiscovery(NamedTuple):
    """Result of a workspace MCP scan, including *why* it came back empty.

    An empty ``sources`` list means something very different depending on
    ``errors``: no servers exist/are visible (nothing to fix) versus the listing
    calls themselves failing (auth expired, missing grants). Callers need both to
    tell an admin the truth.
    """

    sources: List[Dict[str, Any]]
    errors: List[str]
    identity: str  # "obo" (as the caller) | "sp" (as the app Service Principal)


def list_workspace_mcp_servers(obo_token: Optional[str] = None) -> McpDiscovery:
    """Enumerate MCP servers available in the workspace via the Databricks SDK.

    Lets an admin pick a server instead of hand-typing a name + URL. Pulls the
    bounded, cheap-to-list kinds:

    - **External MCP servers registered in AI Gateway** — these are Unity Catalog
      ``HTTP`` connections, reachable at ``/api/2.0/mcp/external/{name}``.
    - **Genie spaces** — ``/api/2.0/mcp/genie/{space_id}``.
    - **Custom MCP servers hosted as Databricks Apps** (name starts ``mcp-``) —
      ``{app_url}/mcp``.

    Runs On-Behalf-Of the user when ``obo_token`` is given (so per-user
    connections/spaces are visible), else as the Service Principal. There is
    intentionally no cross-identity fallback — discovery reflects exactly what the
    chosen identity can see. Sources are dicts shaped for the quick-add form:
    ``{name, server_url, kind, detail}``.
    """
    host = _host()
    identity = "obo" if obo_token else "sp"
    ws = build_obo_workspace_client(obo_token) if obo_token else build_sp_workspace_client()
    out, errors = _list_mcp_servers_with_client(ws, host)
    if errors and not out:
        logger.warning(
            "list_workspace_mcp_servers: %s returned empty (errors: %s)",
            identity.upper(), "; ".join(errors),
        )
    return McpDiscovery(sources=out, errors=errors, identity=identity)


def _content_to_text(result: Any) -> str:
    """Flatten an MCP ``CallToolResult`` into plain text for the agent."""
    content = getattr(result, "content", None)
    if content is None and isinstance(result, dict):
        content = result.get("content")
    if not content:
        return str(result)
    parts: List[str] = []
    for item in content:
        text = getattr(item, "text", None)
        if text is None and isinstance(item, dict):
            text = item.get("text")
        parts.append(text if text is not None else str(item))
    return "\n".join(p for p in parts if p)


def call_tool(
    server_url: str,
    tool_name: str,
    arguments: Dict[str, Any],
    *,
    identity_mode: str,
    obo_token: Optional[str] = None,
) -> Dict[str, Any]:
    """Invoke ``tool_name`` on ``server_url`` under the configured identity.

    Identity is governed solely by the per-tool ``identity_mode`` setting from the
    Tool Registry — there is intentionally **NO** automatic Service-Principal
    fallback. Silently retrying an ``obo``-pinned tool as the SP would execute it
    with the app's (broader) grants instead of the user's, bypassing Unity Catalog
    enforcement — a privilege-escalation risk. An OBO failure is therefore
    surfaced as an error rather than escalated.

    - ``identity_mode='obo'`` runs strictly On-Behalf-Of the user and requires a
      forwarded user token; if none is present the call fails (it is never run as
      the SP).
    - ``identity_mode='sp'`` runs as the app Service Principal.
    """
    if identity_mode == "obo":
        if not obo_token:
            return {
                "ok": False,
                "error": (
                    "Tool is configured to run on behalf of the user (OBO), but no "
                    "user token was available for this request. Not falling back to "
                    "the Service Principal."
                ),
                "tool": tool_name,
            }
        ws = build_obo_workspace_client(obo_token)
        used = "obo"
    else:
        ws = build_sp_workspace_client()
        used = "sp"

    logger.info("MCP call tool=%s server=%s identity=%s", tool_name, server_url, used)
    client = _mcp_client(server_url, ws)
    try:
        result = client.call_tool(tool_name, arguments or {})
    except Exception as e:  # noqa: BLE001 - normalize the opaque TaskGroup wrapper
        return {"ok": False, "error": _readable_exc(e), "tool": tool_name}
    is_error = bool(getattr(result, "isError", False)) or (
        isinstance(result, dict) and result.get("isError")
    )
    text = _content_to_text(result)
    if is_error:
        return {"ok": False, "error": text, "tool": tool_name}
    return {"ok": True, "result": text, "tool": tool_name}
