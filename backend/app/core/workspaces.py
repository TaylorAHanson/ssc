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
    # "workspace_sp:<scope>" | "global_default" | "none". Lets
    # `ping_workspaces`/logs show which SP a workspace resolved to without
    # exposing the secret.
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


def _target_scope() -> str:
    """The single, install-wide secret scope holding every target-workspace SP.

    Sourced from ``settings.TARGET_WORKSPACE_SP_SECRET_SCOPE`` — set in
    databricks.yml or edited live under Admin -> Settings -> Target Workspaces.
    The app's own SP must have READ on this scope.
    """
    return getattr(settings, "TARGET_WORKSPACE_SP_SECRET_SCOPE", "") or ""


def _resolve_credentials(
    ws: Dict[str, Any]
) -> Tuple[Optional[str], Optional[str], Optional[str], str]:
    """Resolve ``(client_id, client_secret, token, source)`` for one workspace.

    Model: one secret scope per installation, and each workspace names the
    secret KEYS of the service principal it uses inline (``client_id_key`` /
    ``client_secret_key``). Workspaces that share an SP reference the same key
    names. A workspace that leaves them blank falls back to the app's own
    SP / PAT (``settings.DATABRICKS_*``).
    """
    scope = _target_scope()
    cid_key = ws.get("client_id_key")
    csec_key = ws.get("client_secret_key")

    if scope and cid_key:
        cid = _read_secret(scope, cid_key)
        csec = _read_secret(scope, csec_key) if csec_key else None
        if cid:
            return cid, csec, None, f"workspace_sp:{scope}"

    # Fall back to the app's own SP / PAT.
    return (
        settings.DATABRICKS_CLIENT_ID or None,
        settings.DATABRICKS_CLIENT_SECRET or None,
        settings.DATABRICKS_TOKEN or None,
        "global_default",
    )


def get_target_workspaces() -> List[WorkspaceConfig]:
    """
    Get the configured target workspaces (Admin -> Settings -> Target Workspaces,
    seeded by default_config.target_workspaces). Falls back to the default
    workspace in settings if none are configured.

    Each workspace's credentials are resolved via :func:`_resolve_credentials`
    using the install-wide secret scope + the workspace's inline SP key names.
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
