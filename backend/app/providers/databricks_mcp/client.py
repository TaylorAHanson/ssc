"""
Client for Databricks Managed MCP servers.

Databricks exposes Genie (and friends) over the standard MCP
streamable-HTTP transport at ``/api/2.0/mcp/<server>/<resource>``. We
authenticate using OBO (the user's forwarded access token), so each
session inherits the caller's Unity Catalog permissions.

The Genie server is **asynchronous**: ``genie_ask`` returns a query
handle, and the caller polls ``genie_poll_response`` to drain the
answer. That two-step shape is what makes the agent's pending-poll
envelope necessary - the chat doesn't block waiting for Genie.
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Dict, Optional, Tuple

from app.core.config import settings

try:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    _MCP_AVAILABLE = True
except ImportError:  # pragma: no cover - import guard for unit tests w/o mcp
    ClientSession = None  # type: ignore[assignment]
    streamablehttp_client = None  # type: ignore[assignment]
    _MCP_AVAILABLE = False

logger = logging.getLogger(__name__)


class GenieAuthUnavailableError(RuntimeError):
    """Raised when neither a user OBO token nor an SP fallback token is available.

    Distinct from generic RuntimeError so callers can map it to a 401 (or a
    user-actionable tool error) without swallowing unrelated runtime failures
    (network errors, MCP protocol bugs, etc.).
    """


def _normalize_host(raw: Optional[str]) -> str:
    """Trim trailing slashes and ensure an ``https://`` scheme.

    Accepts whatever shape the caller stored in ``DATABRICKS_HOST`` or
    ``DATABRICKS_WORKSPACE_URL`` (with/without scheme, with/without
    trailing slash) and emits a clean ``https://host`` value usable as
    the base of an MCP endpoint URL.
    """
    if not raw:
        return ""
    cleaned = raw.strip().rstrip("/")
    if not cleaned:
        return ""
    if cleaned.startswith("http://") or cleaned.startswith("https://"):
        return cleaned
    return f"https://{cleaned}"


def build_genie_mcp_url(space_id: Optional[str] = None, host: Optional[str] = None) -> str:
    """Build the Managed-MCP URL for Databricks Genie.

    Databricks exposes two related Managed MCP endpoints:

    * ``/api/2.0/mcp/genie`` (general Databricks Genie) - the new
      general-purpose chat in Databricks One. Searches across the
      caller's accessible Unity Catalog data *and* any Genie Spaces
      they're entitled to. This is the default and is correct for the
      "Ask Your Data" tab.
    * ``/api/2.0/mcp/genie/{genie_space_id}`` (Genie Space) - scoped to
      a single curated Genie Space. Use this when the caller wants
      answers locked to a specific space's curated tables/metrics.

    Pass ``space_id`` only when you want the space-scoped server.
    Raises ``ValueError`` if no host is configured.
    """
    base = _normalize_host(host or settings.DATABRICKS_HOST or settings.DATABRICKS_WORKSPACE_URL)
    if not base:
        raise ValueError(
            "Databricks host is not configured. Set DATABRICKS_HOST or "
            "DATABRICKS_WORKSPACE_URL before invoking the Genie MCP tool."
        )
    if space_id:
        return f"{base}/api/2.0/mcp/genie/{space_id}"
    return f"{base}/api/2.0/mcp/genie"


# Environments where we allow the SP fallback. Used as a *secondary*
# signal — the primary signal is the presence of Databricks-Apps
# runtime env vars (see ``_running_in_databricks_apps``). Anything
# deployed inside Databricks Apps is treated as "real" regardless of
# what ``ENVIRONMENT`` says.
_LOCAL_ENVIRONMENTS = frozenset({"development", "dev", "local", "test", "testing"})


def _running_in_databricks_apps() -> bool:
    """Detect whether the process is running inside the Databricks Apps runtime.

    Databricks Apps injects a small set of platform-specific env vars
    into every app process. ``DATABRICKS_APP_PORT`` is the cleanest
    signal — it's only ever set by the platform — so we use it as the
    canonical "are we in Databricks Apps?" check. ``DATABRICKS_APP_NAME``
    is checked as a defensive secondary in case the platform ever
    renames the port variable.

    This is intentionally separate from ``ENVIRONMENT``: the ``dev``,
    ``stage`` and ``prod`` bundle targets all deploy into Databricks
    Apps and must all refuse the SP fallback. Only true local
    ``./dev.sh`` runs (no platform env vars) are allowed to fall back.
    """
    return bool(os.environ.get("DATABRICKS_APP_PORT") or os.environ.get("DATABRICKS_APP_NAME"))


def _is_local_environment() -> bool:
    """True when the resolver is allowed to use the SP fallback.

    Two conditions must both hold:

    1. We're not running inside the Databricks Apps runtime (no
       ``DATABRICKS_APP_PORT`` etc.). Any deployed bundle target —
       ``dev``, ``stage``, ``prod`` — is on the platform and is
       therefore *not* local, even though ``ENVIRONMENT`` may be
       ``"dev"``.
    2. The configured ``ENVIRONMENT`` value is one of the explicitly
       local-flavored names. The default (``production``) is excluded,
       so a deployed app that forgot to set the variable is still
       safe.
    """
    if _running_in_databricks_apps():
        return False
    env = (settings.ENVIRONMENT or "").strip().lower()
    return env in _LOCAL_ENVIRONMENTS


def resolve_genie_bearer_token(obo_token: Optional[str]) -> Tuple[Optional[str], str]:
    """Pick the bearer token to use for Databricks Genie MCP.

    Returns a tuple ``(token, source)`` where ``source`` is one of:

    * ``"obo"`` - the user's forwarded access token. Always preferred,
      and the **only** acceptable source when running inside Databricks
      Apps. Genie answers are scoped to the user's Unity Catalog
      permissions, which is the right behavior because the platform
      injects ``X-Forwarded-Access-Token`` on every request.
    * ``"sp"`` - service-principal OAuth token fetched via the
      Databricks SDK ``Config(...).authenticate()`` flow. **Only**
      used in true local runs (``./dev.sh`` outside Databricks Apps,
      with ``ENVIRONMENT`` set to a local-flavored name) where the
      OBO header isn't available. Genie answers are scoped to the
      SP's UC permissions, which in local dev is acceptable because
      the developer is effectively the user.
    * ``"none"`` - no usable token. Token is ``None``; callers should
      surface a clear "no auth" error to the user.
    """
    if obo_token:
        return obo_token, "obo"

    # Anywhere on the Databricks Apps runtime we *intentionally* refuse
    # to fall back to the SP. Databricks Apps always injects the user
    # OBO token *if and only if* the bundle's ``user_api_scopes``
    # cover the Genie endpoint and the user has granted those scopes.
    # If we silently used the SP instead, the caller would see a
    # confusing 403 from /api/2.0/mcp/genie (because the SP almost
    # never has Genie access) — when the real fix is to add the right
    # OAuth scope to ``databricks.yml``.
    if not _is_local_environment():
        if _running_in_databricks_apps():
            logger.warning(
                "Genie MCP: no OBO token forwarded while running inside "
                "Databricks Apps; refusing the SP fallback. The bundle's "
                "``user_api_scopes`` likely needs to include the Genie / "
                "dashboards scope, or the user hasn't re-authorized the "
                "app since that scope was added."
            )
        else:
            logger.warning(
                "Genie MCP: no OBO token forwarded and ENVIRONMENT=%r is "
                "not a local/dev environment; refusing the SP fallback.",
                settings.ENVIRONMENT,
            )
        return None, "none"

    # Local-only SP fallback. Mirrors the OAuth path used by
    # ``app.model_serving.client`` so we don't introduce a new auth shape.
    client_id = settings.DATABRICKS_CLIENT_ID
    client_secret = settings.DATABRICKS_CLIENT_SECRET
    host = settings.DATABRICKS_HOST or settings.DATABRICKS_WORKSPACE_URL
    if not (client_id and client_secret and host):
        return None, "none"
    try:
        from databricks.sdk.core import Config  # local import to avoid hard dep at import time

        cfg = Config(
            host=host,
            client_id=client_id,
            client_secret=client_secret,
            auth_type="oauth-m2m",
        )
        headers = cfg.authenticate()
        auth_val = headers.get("Authorization") if isinstance(headers, dict) else None
        if isinstance(auth_val, str) and auth_val.startswith("Bearer "):
            logger.info(
                "Genie MCP: no OBO token available; using SP OAuth fallback "
                "(ENVIRONMENT=%r, not running in Databricks Apps). This "
                "path is local-dev-only.",
                settings.ENVIRONMENT,
            )
            return auth_val[len("Bearer "):], "sp"
    except Exception as e:
        logger.warning("Genie MCP: SP OAuth fallback failed: %s", e)
    return None, "none"


@asynccontextmanager
async def open_mcp_session(
    url: str,
    bearer_token: str,
    timeout_seconds: float = 30.0,
    sse_read_timeout: float = 300.0,
) -> AsyncIterator["ClientSession"]:
    """Open an initialized MCP session against ``url`` using ``bearer_token``.

    Yields a fully initialized :class:`mcp.ClientSession` ready for
    ``call_tool`` invocations. The transport (HTTP + auth headers) and
    the session are both cleaned up on exit.

    The bearer token can be either a user OBO token (preferred, scopes
    Genie answers to that user) or a service-principal token (used as a
    fallback in local dev). Resolution happens at the call site via
    :func:`resolve_genie_bearer_token`.
    """
    if not _MCP_AVAILABLE:
        raise RuntimeError(
            "The 'mcp' package is not installed. Install mcp>=1.20 to use "
            "Databricks Managed MCP integrations."
        )
    if not bearer_token:
        raise ValueError(
            "A bearer token is required to talk to Databricks Managed MCP. "
            "Either a user OBO token (preferred) or a service-principal "
            "token must be supplied."
        )

    headers = {"Authorization": f"Bearer {bearer_token}"}
    async with streamablehttp_client(
        url=url,
        headers=headers,
        timeout=timeout_seconds,
        sse_read_timeout=sse_read_timeout,
    ) as (read_stream, write_stream, _get_session_id):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            yield session


async def call_genie_tool(
    tool_name: str,
    arguments: Dict[str, Any],
    obo_token: Optional[str] = None,
    space_id: Optional[str] = None,
    host: Optional[str] = None,
) -> Dict[str, Any]:
    """Invoke a single tool on the Databricks Genie MCP server.

    Convenience wrapper for the common case of "open a session, call one
    tool, close the session". When ``space_id`` is omitted (the default
    for the "Ask Your Data" tab), this hits the general Databricks Genie
    server which searches across the user's accessible UC data + Genie
    Spaces. Pass ``space_id`` to pin to a specific curated space.

    ``obo_token`` is preferred. When absent, we fall back to a service
    principal OAuth token via :func:`resolve_genie_bearer_token` (handy
    for local dev where the ``X-Forwarded-Access-Token`` header isn't
    present). If neither is available the call raises ``RuntimeError``
    with a user-actionable message.

    Returns a normalized dict with ``content`` (joined text from the
    response parts), ``structured`` (the structured payload if the
    server emitted one), ``is_error`` (``True`` when the MCP server
    reported an error), and ``auth_source`` (``"obo"`` / ``"sp"`` so
    callers can warn the user when the SP fallback was used).
    """
    bearer_token, source = resolve_genie_bearer_token(obo_token)
    if not bearer_token:
        if _running_in_databricks_apps():
            raise GenieAuthUnavailableError(
                "No OBO token was forwarded for the Databricks Genie call. "
                "The Databricks Apps platform should inject "
                "X-Forwarded-Access-Token automatically — if it's missing, "
                "the bundle's user_api_scopes likely doesn't include the "
                "Genie / dashboards scope, or the user hasn't re-authorized "
                "the app since that scope was added. (SP fallback is "
                "intentionally disabled inside Databricks Apps so Genie "
                "always runs under the user's identity.)"
            )
        if _is_local_environment():
            raise GenieAuthUnavailableError(
                "No authentication available for Databricks Genie. In local dev, "
                "set DATABRICKS_CLIENT_ID / DATABRICKS_CLIENT_SECRET (the SP must "
                "have access to Genie in this workspace) or paste a user token "
                "into MOCK_USER_TOKEN."
            )
        raise GenieAuthUnavailableError(
            "No OBO token was forwarded for the Databricks Genie call, and "
            f"ENVIRONMENT={settings.ENVIRONMENT!r} is not a local/dev "
            "environment, so the SP fallback is disabled. Either set "
            "ENVIRONMENT to a local-flavored value (development / dev / "
            "local / test / testing) for off-platform dev, or run the app "
            "inside Databricks Apps so the X-Forwarded-Access-Token header "
            "is forwarded."
        )

    url = build_genie_mcp_url(space_id, host=host)
    logger.info(
        "Calling Genie MCP tool %s on %s (auth=%s, args keys=%s)",
        tool_name,
        url,
        source,
        list(arguments.keys()),
    )
    async with open_mcp_session(url=url, bearer_token=bearer_token) as session:
        result = await session.call_tool(tool_name, arguments=arguments)

    text_parts: list[str] = []
    structured: Optional[Dict[str, Any]] = None
    for part in getattr(result, "content", []) or []:
        text = getattr(part, "text", None)
        if isinstance(text, str):
            text_parts.append(text)
    structured_attr = getattr(result, "structuredContent", None)
    if isinstance(structured_attr, dict):
        structured = structured_attr

    return {
        "content": "\n".join(t for t in text_parts if t).strip(),
        "structured": structured,
        "is_error": bool(getattr(result, "isError", False)),
        "auth_source": source,
    }


__all__ = [
    "build_genie_mcp_url",
    "open_mcp_session",
    "call_genie_tool",
    "resolve_genie_bearer_token",
    "GenieAuthUnavailableError",
]
