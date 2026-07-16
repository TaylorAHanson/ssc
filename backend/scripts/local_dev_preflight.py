#!/usr/bin/env python3
"""Local-dev preflight — IDE tooling only, invoked by ``./dev.sh``.

Two jobs, both strictly local:

1. **Validate** the config the backend needs for local work and print a concise,
   grouped report of anything missing. Warn-only — it never blocks startup.
2. **Resolve Databricks credentials** when they're absent from ``backend/.env``,
   using the developer's *own* Databricks CLI auth (``databricks auth login`` /
   a ``.databrickscfg`` profile / ``DATABRICKS_*`` env), and emit ``export``
   lines on stdout that ``./dev.sh`` evaluates — so the backend runs as *you*
   without hand-copying a PAT into ``.env`` (the #1 missed-config foot-gun).

Guarantees (why this is safe):
  * **Nothing deployed is affected.** It hard-exits before doing anything
    credential-related if it detects the Databricks Apps runtime, and ``dev.sh``
    is the only caller. Deployed apps use the ambient service principal.
  * **Writes nothing to disk and mints no new tokens.** It reuses whatever auth
    your Databricks CLI already has. It prefers long-lived creds (SP / PAT) and
    only falls back to a short-lived OAuth (U2M) bearer, about which it warns.

stdout is reserved for ``export`` lines so ``dev.sh`` can ``eval`` them; all
human-readable output goes to stderr.

Usage:
    python scripts/local_dev_preflight.py            # print the report only
    python scripts/local_dev_preflight.py --export   # + emit `export KEY=...`
"""
from __future__ import annotations

import os
import shlex
import sys

# Make the backend package importable regardless of the caller's cwd (this file
# lives in backend/scripts/, so backend/ is its parent's parent).
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

# Windows Git Bash consoles often default to a non-UTF-8 code page (cp1252),
# which would make printing any non-ASCII char raise UnicodeEncodeError. All
# output below is intentionally ASCII-only; this guard is defensive so a stray
# non-ASCII char can never crash the preflight (and take the report with it).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")  # type: ignore[union-attr]
    except Exception:
        pass


def _say(msg: str = "") -> None:
    """Human-readable output — stderr, so it never pollutes the eval'd stdout."""
    print(msg, file=sys.stderr)


def _running_on_platform() -> bool:
    """True inside the Databricks Apps runtime (any deployed bundle target).

    Mirrors ``app.providers.databricks_mcp._running_in_databricks_apps`` — the
    platform injects ``DATABRICKS_APP_PORT`` into every app process — so this
    stays consistent with the rest of the codebase's local/deployed gating.
    """
    return bool(os.environ.get("DATABRICKS_APP_PORT") or os.environ.get("DATABRICKS_APP_NAME"))


def _resolve_from_cli(want_host: bool, want_creds: bool) -> dict:
    """Resolve Databricks auth from the developer's CLI/profile via the SDK.

    Returns only the pieces that were requested (i.e. missing from ``.env``).
    Prefers non-expiring credentials (service principal, then PAT) and only
    falls back to a short-lived OAuth bearer, warning when it does.
    """
    try:
        from databricks.sdk.core import Config
    except Exception as e:  # SDK missing / import error
        _say(f"  ! Databricks SDK unavailable for auto-auth: {e}")
        return {}

    try:
        cfg = Config()  # unified auth chain: env -> .databrickscfg -> OAuth (U2M)
    except Exception as e:
        _say(f"  ! Could not resolve Databricks auth from your CLI/profile: {e}")
        _say("    Fix: run `databricks auth login --host <workspace-url>`")
        _say("         or set DATABRICKS_HOST + DATABRICKS_TOKEN in backend/.env")
        return {}

    out: dict = {}

    if want_host and getattr(cfg, "host", None):
        out["DATABRICKS_HOST"] = cfg.host

    if want_creds:
        client_id = getattr(cfg, "client_id", None)
        client_secret = getattr(cfg, "client_secret", None)
        token = getattr(cfg, "token", None)
        if client_id and client_secret:
            out["DATABRICKS_CLIENT_ID"] = client_id
            out["DATABRICKS_CLIENT_SECRET"] = client_secret
            _say("  [OK] Resolved Databricks service-principal creds from your CLI profile (no expiry).")
        elif token:
            out["DATABRICKS_TOKEN"] = token
            _say("  [OK] Resolved a Databricks PAT from your CLI profile.")
        else:
            # OAuth U2M: no static token attr - pull a bearer from the session.
            try:
                headers = cfg.authenticate() or {}
                bearer = str(headers.get("Authorization", ""))
                if bearer.lower().startswith("bearer "):
                    out["DATABRICKS_TOKEN"] = bearer[7:].strip()
                    _say("  [OK] Resolved a short-lived OAuth token from `databricks auth login`.")
                    _say("    NOTE: OAuth (U2M) tokens expire (~1h). If the backend starts")
                    _say("          returning 401s during a long session, just restart ./dev.sh.")
                else:
                    _say("  ! Your CLI session returned no usable bearer token.")
            except Exception as e:
                _say(f"  ! Could not obtain a token from your OAuth session: {e}")

    return out


def _status(ok: bool) -> str:
    return "[OK]" if ok else "[!!]"


def _report(settings, host: str, has_auth: bool) -> None:
    """Print a concise, grouped view of local-dev config health (warn-only)."""
    _say("")
    _say("-- Local dev preflight ----------------------")

    _say(f"  {_status(bool(host))} Databricks host      {host or '(missing)'}")
    _say(f"  {_status(has_auth)} Databricks auth      {'resolved' if has_auth else '(missing - run `databricks auth login`)'}")

    warehouse = (getattr(settings, 'DATABRICKS_WAREHOUSE_ID', '') or '').strip()
    _say(f"  {_status(bool(warehouse))} SQL warehouse id     {warehouse or '(unset - SQL tools/run_sql will be unavailable)'}")

    agent_ep = (getattr(settings, 'AI_GATEWAY_ENDPOINT', '') or getattr(settings, 'MODEL_SERVING_AGENT_LLM_ENDPOINT', '') or '').strip()
    _say(f"  {_status(bool(agent_ep))} Agent LLM endpoint   {agent_ep or '(unset - the chat agent will not respond)'}")

    # Local dev defaults to SQLite; only note Postgres if partially configured.
    db_url = (getattr(settings, 'DATABASE_URL', '') or '').strip()
    db_pw = (getattr(settings, 'DATABASE_PASSWORD', '') or '').strip()
    if db_url or db_pw:
        _say("  [OK] Database            Lakebase/Postgres configured")
    else:
        _say("  [OK] Database            SQLite (local default: backend/app_hub.db)")

    if not host or not has_auth:
        _say("")
        _say("  Missing Databricks config. Easiest fix:")
        _say("    databricks auth login --host https://<your-workspace>.databricks.com")
        _say("  ...then re-run ./dev.sh (no need to paste a token into .env).")
    _say("---------------------------------------------")
    _say("")


def main() -> int:
    export_mode = "--export" in sys.argv[1:]

    # Hard safety boundary: never touch credentials on a deployed runtime.
    if _running_on_platform():
        _say("local_dev_preflight: Databricks Apps runtime detected - skipping (deployed envs are untouched).")
        return 0

    try:
        from app.core.config import settings
    except Exception as e:
        _say(f"local_dev_preflight: could not import app config ({e}); skipping.")
        return 0

    host = (getattr(settings, "DATABRICKS_HOST", "") or getattr(settings, "DATABRICKS_WORKSPACE_URL", "") or "").strip()
    has_token = bool((getattr(settings, "DATABRICKS_TOKEN", "") or "").strip())
    has_sp = bool(
        (getattr(settings, "DATABRICKS_CLIENT_ID", "") or "").strip()
        and (getattr(settings, "DATABRICKS_CLIENT_SECRET", "") or "").strip()
    )

    exports: dict = {}
    if not host or not (has_token or has_sp):
        _say("  Databricks creds not fully set in backend/.env - trying your Databricks CLI login...")
        exports = _resolve_from_cli(want_host=not host, want_creds=not (has_token or has_sp))

    resolved_host = exports.get("DATABRICKS_HOST", host)
    resolved_auth = (
        has_token
        or has_sp
        or "DATABRICKS_TOKEN" in exports
        or ("DATABRICKS_CLIENT_ID" in exports and "DATABRICKS_CLIENT_SECRET" in exports)
    )

    _report(settings, host=resolved_host, has_auth=bool(resolved_auth))

    if export_mode:
        for key, value in exports.items():
            if value:
                # Single-quote-safe so dev.sh can `eval` it verbatim.
                sys.stdout.write(f"export {key}={shlex.quote(str(value))}\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
