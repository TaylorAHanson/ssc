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
from typing import Any, Dict, List, Optional, Tuple

from app.core.config import settings

logger = logging.getLogger(__name__)


class McpDependencyError(RuntimeError):
    """Raised when MCP functionality is used but ``databricks_mcp`` is missing."""


def _host() -> str:
    host = (settings.DATABRICKS_HOST or settings.DATABRICKS_WORKSPACE_URL or "").strip().rstrip("/")
    if host and not host.startswith(("https://", "http://")):
        host = f"https://{host}"
    return host


class _PlatformOAuthM2M:
    """CredentialsStrategy that wraps platform-injected auth as ``oauth-m2m``.

    Inside a Databricks App the platform injects credentials (typically via
    ``DATABRICKS_TOKEN``). The SDK auto-detects them but may label the auth type
    as ``'pat'``, which ``DatabricksMCPClient`` rejects. This wrapper delegates
    actual token generation to the platform-configured client while reporting
    ``auth_type='oauth-m2m'`` so ``DatabricksMCPClient`` accepts it.
    """

    def __init__(self, inner_ws):
        self._inner_ws = inner_ws

    def auth_type(self) -> str:
        return "oauth-m2m"

    def __call__(self, cfg):
        inner = self._inner_ws
        return lambda: inner.config.authenticate()


def build_sp_workspace_client():
    """A ``WorkspaceClient`` authenticated as the app Service Principal.

    Prefers explicit M2M client_id/secret when configured; otherwise relies on the
    SDK's default auth chain. Inside a Databricks App, wraps the auto-detected
    credentials with an ``oauth-m2m`` label so ``DatabricksMCPClient`` accepts them.
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
    # credentials, then wrap it with a strategy that reports 'oauth-m2m' so
    # DatabricksMCPClient accepts it (it rejects 'pat'-labeled clients).
    if os.environ.get("DATABRICKS_APP_PORT"):
        inner_ws = WorkspaceClient()
        config = Config(
            host=inner_ws.config.host,
            credentials_strategy=_PlatformOAuthM2M(inner_ws),
        )
        return WorkspaceClient(config=config)

    # Local development: SDK picks up auth from .databrickscfg or env vars.
    if host:
        return WorkspaceClient(host=host)
    return WorkspaceClient()


class _OboOAuthCredentials:
    """CredentialsStrategy that wraps a forwarded OAuth token.

    Reports ``auth_type`` as ``'oauth-m2m'`` so ``DatabricksMCPClient``
    recognizes it as OAuth-compatible. The actual token is the user's
    forwarded OAuth token (OBO) — the label is just for client-side gating;
    the Databricks API validates the token itself regardless of the label.
    """

    def __init__(self, token: str):
        self._token = token

    def auth_type(self) -> str:
        return "oauth-m2m"

    def __call__(self, cfg):
        token = self._token
        return lambda: {"Authorization": f"Bearer {token}"}


def build_obo_workspace_client(token: str):
    """A ``WorkspaceClient`` authenticated as the user via a forwarded OBO token.

    The forwarded access token from Databricks Apps IS an OAuth access token.
    Using ``auth_type='pat'`` makes ``DatabricksMCPClient`` reject it (custom and
    external MCP servers require OAuth). Instead we supply a proper
    ``CredentialsStrategy`` that reports ``auth_type='oauth-m2m'`` and sends the
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
    """List tools on ``server_url``.

    Tries multiple auth strategies in order:
    1. **OBO** (On-Behalf-Of user) — required for AI-Gateway external MCP servers
       that use Per-User OAuth (e.g. system_ai_agent_github_mcp).
    2. **Service Principal** (OAuth M2M) — required for custom MCP apps hosted on
       Databricks Apps (DatabricksMCPClient rejects non-standard OAuth types).

    Falls through on auth/protocol errors so at least one strategy can succeed.
    Returns normalized dicts; raises a ``RuntimeError`` with the unwrapped cause
    when ALL strategies fail.
    """
    strategies: list = []
    if obo_token:
        strategies.append(("OBO", lambda: build_obo_workspace_client(obo_token)))
    strategies.append(("SP", build_sp_workspace_client))

    last_error: Optional[Exception] = None
    for label, build_ws in strategies:
        ws = build_ws()
        client = _mcp_client(server_url, ws)
        try:
            raw = client.list_tools()
            return [_tool_to_dict(t) for t in (raw or [])]
        except Exception as e:  # noqa: BLE001
            last_error = e
            logger.info(
                "list_tools [%s]: %s strategy failed: %s",
                server_url, label, _readable_exc(e),
            )
            continue

    raise RuntimeError(_readable_exc(last_error)) from last_error


def _list_mcp_servers_with_client(ws, host: str) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Core discovery logic using a pre-built WorkspaceClient.

    Separated so we can retry with a different client on auth failure.
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


def list_workspace_mcp_servers(obo_token: Optional[str] = None) -> List[Dict[str, Any]]:
    """Enumerate MCP servers available in the workspace via the Databricks SDK.

    Lets an admin pick a server instead of hand-typing a name + URL. Pulls the
    bounded, cheap-to-list kinds:

    - **External MCP servers registered in AI Gateway** — these are Unity Catalog
      ``HTTP`` connections, reachable at ``/api/2.0/mcp/external/{name}``.
    - **Genie spaces** — ``/api/2.0/mcp/genie/{space_id}``.
    - **Custom MCP servers hosted as Databricks Apps** (name starts ``mcp-``) —
      ``{app_url}/mcp``.

    Tries OBO first (when available) so per-user connections are visible. If OBO
    returns nothing (possibly due to auth issues), automatically retries with the
    Service Principal as fallback. Returns dicts shaped for the quick-add form:
    ``{name, server_url, kind, detail}``.
    """
    host = _host()

    # Primary attempt: OBO if token available, otherwise SP.
    if obo_token:
        ws = build_obo_workspace_client(obo_token)
        out, errors = _list_mcp_servers_with_client(ws, host)
        if out:
            return out
        # OBO returned nothing — retry with SP as fallback (the SP may have
        # broader visibility, e.g. system connections granted to the app).
        if errors:
            logger.info(
                "list_workspace_mcp_servers: OBO returned empty (errors: %s). "
                "Retrying with Service Principal.",
                "; ".join(errors),
            )
        ws_sp = build_sp_workspace_client()
        out_sp, errors_sp = _list_mcp_servers_with_client(ws_sp, host)
        if out_sp:
            return out_sp
        if errors_sp:
            logger.warning(
                "list_workspace_mcp_servers: SP fallback also failed: %s",
                "; ".join(errors_sp),
            )
        return []
    else:
        ws = build_sp_workspace_client()
        out, errors = _list_mcp_servers_with_client(ws, host)
        if errors and not out:
            logger.warning(
                "list_workspace_mcp_servers: SP returned empty (errors: %s)",
                "; ".join(errors),
            )
        return out


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
    """Invoke ``tool_name`` on ``server_url`` as the SP or the user (OBO).

    ``identity_mode='obo'`` tries the forwarded user token first (needed for
    Per-User OAuth servers like system_ai_*), then falls back to SP if the OBO
    auth is rejected (e.g. custom apps that only accept OAuth M2M).
    ``'sp'`` always uses the SP directly.
    """
    strategies: list = []
    if identity_mode == "obo" and obo_token:
        strategies.append(("obo", lambda: build_obo_workspace_client(obo_token)))
    strategies.append(("sp", build_sp_workspace_client))

    last_error: Optional[str] = None
    for used, build_ws in strategies:
        ws = build_ws()
        logger.info("MCP call tool=%s server=%s identity=%s", tool_name, server_url, used)
        client = _mcp_client(server_url, ws)
        try:
            result = client.call_tool(tool_name, arguments or {})
            break
        except Exception as e:  # noqa: BLE001
            last_error = _readable_exc(e)
            logger.info(
                "MCP call_tool [%s]: %s strategy failed: %s",
                tool_name, used, last_error,
            )
            continue
    else:
        return {"ok": False, "error": last_error, "tool": tool_name}
    is_error = bool(getattr(result, "isError", False)) or (
        isinstance(result, dict) and result.get("isError")
    )
    text = _content_to_text(result)
    if is_error:
        return {"ok": False, "error": text, "tool": tool_name}
    return {"ok": True, "result": text, "tool": tool_name}
