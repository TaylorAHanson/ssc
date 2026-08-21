"""Local-dev user identity: an OAuth bearer from the developer's CLI login.

Off-platform there is no ``X-Forwarded-Access-Token`` header, so every
on-behalf-of path (MCP discovery, Unity Catalog listings, Genie) has no user
token to run under. This resolves one from the developer's own
``databricks auth login`` session, so local OBO behaves like the deployed app
without anyone pasting a personal access token into ``.env`` — a paste that
expires silently and then makes every OBO call fail with ``Invalid access
token``.

Boundaries, deliberately narrow:

* **Local only.** Returns ``None`` inside the Databricks Apps runtime, and
  unless ``ENVIRONMENT`` is local-flavored — the same two-condition gate
  ``providers/databricks_mcp/client.sp_fallback_allowed()`` uses. Deployed
  targets get their user token from the platform header, never from here.
* **Mints nothing.** It reuses whatever session the CLI already has; the SDK
  refreshes that session when the access token nears expiry.
* **Never the Service Principal.** SP credentials authenticate as the *app*, so
  handing them to an on-behalf-of call would make the user identity a lie. M2M
  auth is rejected rather than silently substituted.
* **Host-pinned.** Resolved for the workspace the app is configured against, so
  a CLI login pointed at a different workspace is never handed to it.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Optional, Tuple

from app.core.config import settings

logger = logging.getLogger(__name__)

# Mirrors providers/databricks_mcp/client._LOCAL_ENVIRONMENTS. The default
# ("production") is excluded so a deployed app that forgot to set the variable
# still gets nothing from here.
_LOCAL_ENVIRONMENTS = frozenset({"development", "dev", "local", "test", "testing"})

# How long a resolved token is reused before asking the SDK again. The SDK hands
# back its cached access token and only talks to the network when the token is
# close to expiring, so re-resolving is usually free. U2M tokens live ~1h, so a
# 30-minute ceiling means callers always get one with plenty of life left.
_REFRESH_AFTER_SECONDS = 30 * 60

_lock = threading.Lock()
_cached_token: Optional[str] = None
_cached_reason: Optional[str] = None
_cached_at: float = 0.0


def is_local_dev() -> bool:
    """True only for a true off-platform ``./dev.sh`` run."""
    if os.environ.get("DATABRICKS_APP_PORT") or os.environ.get("DATABRICKS_APP_NAME"):
        return False
    return (settings.ENVIRONMENT or "").strip().lower() in _LOCAL_ENVIRONMENTS


def _configured_host() -> str:
    host = (settings.DATABRICKS_HOST or settings.DATABRICKS_WORKSPACE_URL or "").strip().rstrip("/")
    if host and not host.startswith(("https://", "http://")):
        host = f"https://{host}"
    return host


def _resolve() -> Tuple[Optional[str], Optional[str]]:
    """Ask the SDK for the developer's bearer token. Returns ``(token, reason)``."""
    try:
        from databricks.sdk.core import Config
    except Exception as e:  # noqa: BLE001 - SDK missing/broken is a config problem
        return None, f"Databricks SDK unavailable ({type(e).__name__}: {e})"

    host = _configured_host()
    try:
        cfg = Config(host=host or None)
    except Exception as e:  # noqa: BLE001 - no CLI login, bad profile, etc.
        return None, f"{type(e).__name__}: {e}"

    auth_type = (getattr(cfg, "auth_type", "") or "").strip().lower()
    if "m2m" in auth_type:
        return None, (
            f"your CLI auth resolved to a service principal (auth_type={auth_type!r}), "
            "which is not a user identity"
        )

    try:
        headers = cfg.authenticate() or {}
    except Exception as e:  # noqa: BLE001 - expired/revoked session
        return None, f"{type(e).__name__}: {e}"

    bearer = str(headers.get("Authorization", ""))
    if not bearer.lower().startswith("bearer "):
        return None, f"CLI session (auth_type={auth_type!r}) returned no bearer token"
    token = bearer[7:].strip()
    if not token:
        return None, f"CLI session (auth_type={auth_type!r}) returned an empty bearer token"
    return token, None


def local_user_token() -> Optional[str]:
    """A bearer token representing the developer, or ``None`` if unavailable.

    Cached, so the common path is a timestamp comparison rather than an SDK call.
    Failures are cached too — without a CLI login this would otherwise retry the
    full resolution on every single request.
    """
    if not is_local_dev():
        return None

    global _cached_token, _cached_reason, _cached_at
    now = time.monotonic()
    with _lock:
        if _cached_at and (now - _cached_at) < _REFRESH_AFTER_SECONDS:
            return _cached_token

        token, reason = _resolve()
        _cached_token, _cached_reason, _cached_at = token, reason, now

        if token:
            logger.info("local dev: resolved a user token from your Databricks CLI login")
        else:
            logger.warning(
                "local dev: no user identity available from your Databricks CLI login (%s). "
                "On-behalf-of calls will fail until you run `databricks auth login --host %s`.",
                reason, _configured_host() or "<your-workspace-url>",
            )
        return token


def local_identity_status() -> Tuple[bool, str]:
    """``(ok, detail)`` for the ``dev.sh`` preflight report."""
    if not is_local_dev():
        return False, "not a local run (deployed apps use the forwarded user token)"
    token = local_user_token()
    if token:
        return True, f"your Databricks CLI login for {_configured_host()}"
    return False, _cached_reason or "unavailable"
