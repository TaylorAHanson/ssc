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
from typing import Any, Dict, List, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)


class McpDependencyError(RuntimeError):
    """Raised when MCP functionality is used but ``databricks_mcp`` is missing."""


def _host() -> str:
    host = (settings.DATABRICKS_HOST or settings.DATABRICKS_WORKSPACE_URL or "").strip()
    return host.rstrip("/")


def build_sp_workspace_client():
    """A ``WorkspaceClient`` authenticated as the app Service Principal.

    Prefers explicit M2M client_id/secret when configured; otherwise falls back to
    the SDK's default auth chain (which, inside a Databricks App, is the injected
    SP OAuth).
    """
    from databricks.sdk import WorkspaceClient

    host = _host()
    client_id = (settings.DATABRICKS_CLIENT_ID or "").strip()
    client_secret = (settings.DATABRICKS_CLIENT_SECRET or "").strip()
    if host and client_id and client_secret:
        return WorkspaceClient(host=host, client_id=client_id, client_secret=client_secret)
    if host:
        return WorkspaceClient(host=host)
    return WorkspaceClient()


def build_obo_workspace_client(token: str):
    """A ``WorkspaceClient`` authenticated as the user via a forwarded OBO token.

    Forces ``auth_type='pat'`` to avoid the "more than one authorization method"
    error when SP OAuth env vars are also present (as they are inside a Databricks
    App).
    """
    from databricks.sdk import WorkspaceClient

    return WorkspaceClient(host=_host(), token=token, auth_type="pat")


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


def list_tools(server_url: str) -> List[Dict[str, Any]]:
    """List tools the Service Principal can see on ``server_url``.

    Returns normalized dicts; raises on connection/auth errors so the caller can
    record a sync failure.
    """
    ws = build_sp_workspace_client()
    client = _mcp_client(server_url, ws)
    raw = client.list_tools()
    return [_tool_to_dict(t) for t in (raw or [])]


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

    ``identity_mode='obo'`` uses the forwarded user token when available and falls
    back to the SP only if no token is present; ``'sp'`` always uses the SP.
    """
    if identity_mode == "obo" and obo_token:
        ws = build_obo_workspace_client(obo_token)
        used = "obo"
    else:
        ws = build_sp_workspace_client()
        used = "sp"
    logger.info("MCP call tool=%s server=%s identity=%s", tool_name, server_url, used)
    client = _mcp_client(server_url, ws)
    result = client.call_tool(tool_name, arguments or {})
    is_error = bool(getattr(result, "isError", False)) or (
        isinstance(result, dict) and result.get("isError")
    )
    text = _content_to_text(result)
    if is_error:
        return {"ok": False, "error": text, "tool": tool_name}
    return {"ok": True, "result": text, "tool": tool_name}
