import os
import base64
import logging
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel

from app.core.config import _yaml_config, settings

logger = logging.getLogger(__name__)

# Cache for secret-scope-resolved values, keyed by (scope, key). Service
# principal secrets don't rotate within a process lifetime, so a tiny cache
# avoids a Databricks `secrets.get_secret` round-trip on every workspace build.
_secret_cache: Dict[Tuple[str, str], Optional[str]] = {}


class WorkspaceConfig(BaseModel):
    name: str
    host: str
    environment: str
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    token: Optional[str] = None
    # Where the credentials came from (diagnostic only, never a secret value):
    # "workspace_env" | "workspace_secret:<scope>" | "environment_sp:<env>" |
    # "global_default" | "none". Lets `ping_workspaces`/logs show which SP a
    # workspace resolved to without exposing the secret.
    credential_source: Optional[str] = None


def _read_secret(scope: Optional[str], key: Optional[str]) -> Optional[str]:
    """Read one Databricks secret value (base64-decoded), cached; None on miss.

    Mirrors how the app already reads SES / GitHub secrets at runtime
    (`WorkspaceClient().secrets.get_secret`) rather than injecting them as
    plaintext env vars. Fail-soft: any error (no scope grant, missing key)
    returns None so credential resolution falls through to the next tier.
    """
    if not scope or not key:
        return None
    cache_key = (scope, key)
    if cache_key in _secret_cache:
        return _secret_cache[cache_key]

    value: Optional[str] = None
    try:
        from databricks.sdk import WorkspaceClient

        secret = WorkspaceClient().secrets.get_secret(scope=scope, key=key)
        if secret and secret.value:
            value = base64.b64decode(secret.value).decode("utf-8").strip() or None
    except Exception as e:  # noqa: BLE001 - never break workspace resolution on a secret read
        logger.warning("Could not read secret %s/%s: %s", scope, key, e)
        value = None

    _secret_cache[cache_key] = value
    return value


def _service_principals_config() -> Dict[str, Any]:
    """The optional ``service_principals`` block from configuration.yaml."""
    return _yaml_config.get("service_principals", {}) or {}


def _environment_sp_keys(environment: str) -> Dict[str, str]:
    """Per-environment SP secret coordinates (scope + key names).

    Key names come from configuration.yaml (a constant naming convention)::

        service_principals:
          environments:
            dev:
              client_id_key: "sp_dbxgrc_dev_client_id"
              client_secret_key: "sp_dbxgrc_dev_client_secret"

    The scope is the one *environment-specific* piece, so it is sourced
    per-deployment from databricks.yml via ``TARGET_WORKSPACE_SP_SECRET_SCOPE``.
    Scope precedence (highest first):
      1. ``environments[<env>].secret_scope``      (explicit per-env, yaml)
      2. ``TARGET_WORKSPACE_SP_SECRET_SCOPE``       (databricks.yml, per-target)
      3. ``service_principals.secret_scope``        (configuration.yaml default)

    Returns empty strings when nothing is configured (so the caller skips this
    tier and falls back to the app's own SP).
    """
    cfg = _service_principals_config()
    env_map = (cfg.get("environments", {}) or {}).get((environment or "").strip().lower(), {}) or {}
    scope = (
        env_map.get("secret_scope")
        or getattr(settings, "TARGET_WORKSPACE_SP_SECRET_SCOPE", "")
        or cfg.get("secret_scope", "")
        or ""
    )
    return {
        "secret_scope": scope,
        "client_id_key": env_map.get("client_id_key", "") or "",
        "client_secret_key": env_map.get("client_secret_key", "") or "",
        "token_key": env_map.get("token_key", "") or "",
    }


def _resolve_credentials(
    ws: Dict[str, Any]
) -> Tuple[Optional[str], Optional[str], Optional[str], str]:
    """Resolve ``(client_id, client_secret, token, source)`` for one workspace.

    Precedence is highest-first, so the SP strategy can change (or go hybrid)
    purely through configuration — no code change:

      1. **Per-workspace env vars** — ``client_id_env`` / ``client_secret_env`` /
         ``token_env`` (back-compat; also a valid override).
      2. **Per-workspace secret keys** — ``client_id_secret`` /
         ``client_secret_secret`` / ``token_secret`` (+ optional ``secret_scope``),
         read from a Databricks secret scope. This is the hybrid escape hatch:
         a single dev workspace can point at a *different* SP than the rest.
      3. **Per-environment SP** — the shared SP for the workspace's environment
         (``service_principals.environments[<env>]``), so e.g. every dev
         workspace inherits ``sp_dbxgrc_dev`` automatically.
      4. **Global default** — ``settings.DATABRICKS_*`` (the app's own SP / PAT).
    """
    # 1. Per-workspace env vars.
    cid = os.getenv(ws["client_id_env"]) if ws.get("client_id_env") else None
    csec = os.getenv(ws["client_secret_env"]) if ws.get("client_secret_env") else None
    tok = os.getenv(ws["token_env"]) if ws.get("token_env") else None
    if cid or tok:
        return cid, csec, tok, "workspace_env"

    env_keys = _environment_sp_keys(ws.get("environment", ""))

    # 2. Per-workspace secret keys (override a specific workspace's SP).
    ws_cid_key = ws.get("client_id_secret")
    ws_csec_key = ws.get("client_secret_secret")
    ws_tok_key = ws.get("token_secret")
    if ws_cid_key or ws_tok_key:
        ws_scope = ws.get("secret_scope") or env_keys["secret_scope"]
        cid = _read_secret(ws_scope, ws_cid_key) if ws_cid_key else None
        csec = _read_secret(ws_scope, ws_csec_key) if ws_csec_key else None
        tok = _read_secret(ws_scope, ws_tok_key) if ws_tok_key else None
        if cid or tok:
            return cid, csec, tok, f"workspace_secret:{ws_scope}"

    # 3. Per-environment shared SP (the default for most workspaces).
    if env_keys["client_id_key"] or env_keys["token_key"]:
        scope = env_keys["secret_scope"]
        cid = _read_secret(scope, env_keys["client_id_key"]) if env_keys["client_id_key"] else None
        csec = _read_secret(scope, env_keys["client_secret_key"]) if env_keys["client_secret_key"] else None
        tok = _read_secret(scope, env_keys["token_key"]) if env_keys["token_key"] else None
        if cid or tok:
            return cid, csec, tok, f"environment_sp:{(ws.get('environment') or '').strip().lower()}"

    # 4. Global default SP / PAT.
    return (
        settings.DATABRICKS_CLIENT_ID or None,
        settings.DATABRICKS_CLIENT_SECRET or None,
        settings.DATABRICKS_TOKEN or None,
        "global_default",
    )


def get_target_workspaces() -> List[WorkspaceConfig]:
    """
    Get the list of target workspaces configured in configuration.yaml.
    Falls back to the default workspace in settings if none are configured.

    Each workspace's credentials are resolved via :func:`_resolve_credentials`,
    which supports a per-environment shared SP with per-workspace overrides (see
    that function's docstring for precedence).
    """
    workspaces_config = _yaml_config.get("target_workspaces", []) or []

    workspaces: List[WorkspaceConfig] = []
    for ws in workspaces_config:
        client_id, client_secret, token, source = _resolve_credentials(ws)
        name = ws.get("name", "unknown")
        environment = ws.get("environment", "unknown")
        logger.debug(
            "Workspace %s (%s) resolved credentials from %s", name, environment, source
        )
        workspaces.append(
            WorkspaceConfig(
                name=name,
                host=ws.get("host", ""),
                environment=environment,
                client_id=client_id,
                client_secret=client_secret,
                token=token,
                credential_source=source,
            )
        )

    # If no workspaces configured, add the default one.
    if not workspaces and (settings.DATABRICKS_HOST or settings.DATABRICKS_WORKSPACE_URL):
        workspaces.append(
            WorkspaceConfig(
                name="default",
                host=settings.DATABRICKS_HOST or settings.DATABRICKS_WORKSPACE_URL,
                environment=settings.ENVIRONMENT,
                client_id=settings.DATABRICKS_CLIENT_ID or None,
                client_secret=settings.DATABRICKS_CLIENT_SECRET or None,
                token=settings.DATABRICKS_TOKEN or None,
                credential_source="global_default",
            )
        )

    return workspaces


def get_workspace_config(host_or_name: str) -> Optional[WorkspaceConfig]:
    """
    Get the configuration for a specific workspace by host URL or name.
    """
    workspaces = get_target_workspaces()

    for ws in workspaces:
        if ws.host == host_or_name or ws.name == host_or_name:
            return ws

    # If not found, but we have a host URL, create a fallback config using default credentials
    if host_or_name.startswith("https://"):
        logger.warning(f"Workspace {host_or_name} not found in config. Falling back to default credentials.")
        return WorkspaceConfig(
            name="fallback",
            host=host_or_name,
            environment="unknown",
            client_id=settings.DATABRICKS_CLIENT_ID or None,
            client_secret=settings.DATABRICKS_CLIENT_SECRET or None,
            token=settings.DATABRICKS_TOKEN or None,
            credential_source="global_default",
        )

    return None
