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
        if value:
            logger.debug("Secret %s/%s resolved (%d chars).", scope, key, len(value))
        else:
            logger.warning("Secret %s/%s exists but decoded to an EMPTY value.", scope, key)
    except Exception as e:  # noqa: BLE001 - never break workspace resolution on a secret read
        # The two common causes are (a) the key name is wrong / not in the scope,
        # or (b) the app's own service principal lacks READ on the scope. Say both
        # so a single run points straight at the fix.
        logger.warning(
            "Could not read secret %s/%s: %s — verify the key NAME exists in scope "
            "'%s' AND that the app's own service principal has READ on that scope.",
            scope, key, e, scope,
        )
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
    name = ws.get("name", "unknown")
    scope = _target_scope()
    cid_key = ws.get("client_id_key")
    csec_key = ws.get("client_secret_key")

    def _global_default(reason: str):
        """Fall back to the app's own SP / PAT, saying loudly WHY.

        This is the silent failure that burns diagnostic cycles: an operator
        configures a dedicated SP for a target workspace, but a wrong key name /
        missing scope / unreadable secret makes us quietly use the APP's SP
        instead — which is valid only in the app's home account, so it fails auth
        against the target with ``invalid_client``. The operator then chases a
        "bad credentials" ghost when the target SP was never even used.
        """
        app_cid = settings.DATABRICKS_CLIENT_ID or None
        logger.warning(
            "Workspace '%s': falling back to the app's OWN service principal "
            "(global_default) — %s. App client_id=%s. If this workspace needs its "
            "OWN service principal, this will FAIL auth against a different "
            "account/host; set client_id_key/client_secret_key + the secret scope "
            "under Admin -> Settings -> Target Workspaces.",
            name, reason, (app_cid[:4] + "***") if app_cid else "UNSET",
        )
        return (
            app_cid,
            settings.DATABRICKS_CLIENT_SECRET or None,
            settings.DATABRICKS_TOKEN or None,
            "global_default",
        )

    if not scope:
        return _global_default("no TARGET_WORKSPACE_SP_SECRET_SCOPE is configured")
    if not cid_key:
        return _global_default(f"no client_id_key is set for this workspace (scope={scope})")

    cid = _read_secret(scope, cid_key)
    if not cid:
        return _global_default(
            f"secret {scope}/{cid_key} could not be read (wrong key name, or the app's "
            f"SP lacks READ on scope '{scope}') — the target SP was NEVER used"
        )

    csec = _read_secret(scope, csec_key) if csec_key else None
    if not csec_key:
        logger.warning(
            "Workspace '%s': client_id_key is set but client_secret_key is EMPTY — "
            "OAuth requires a secret, so auth will fail.", name,
        )
    elif not csec:
        logger.warning(
            "Workspace '%s': client_id resolved but client_secret %s/%s could not be "
            "read — auth will fail. Check the secret key name.", name, scope, csec_key,
        )

    logger.info(
        "Workspace '%s': resolved a DEDICATED service principal from scope=%s "
        "(client_id_key=%s -> client_id=%s***, client_secret_key=%s -> %s).",
        name, scope, cid_key, cid[:4], csec_key, "present" if csec else "MISSING",
    )
    return cid, csec, None, f"workspace_sp:{scope}"


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


def get_home_workspace_config() -> WorkspaceConfig:
    """The app's LOCAL / home workspace (app service principal + home host).

    Unity Catalog is metastore-global (account-level), so ALL UC metadata reads
    and queries must run against THIS workspace — never a target workspace host.
    A cross-workspace host may be unreachable or fail TLS certificate validation
    from here (the "Cert validation failed" error seen when a UC tool is pointed
    at a target host), and there is no UC benefit since the metastore is shared.
    """
    return WorkspaceConfig(
        name="home",
        host=settings.DATABRICKS_HOST or settings.DATABRICKS_WORKSPACE_URL or "",
        environment=settings.ENVIRONMENT,
        client_id=settings.DATABRICKS_CLIENT_ID or None,
        client_secret=settings.DATABRICKS_CLIENT_SECRET or None,
        token=settings.DATABRICKS_TOKEN or None,
        credential_source="home",
    )


def get_uc_provider():
    """A ``DatabricksProvider`` pinned to the home workspace for Unity Catalog.

    Use this for every UC read (catalog/schema/table/volume/credential listing,
    ``information_schema`` / tag queries). Because UC is account-level, the home
    workspace can see the whole metastore, and we avoid connecting to a target
    host that may be network-unreachable from the app.
    """
    from app.providers.databricks import DatabricksProvider

    home = get_home_workspace_config()
    return DatabricksProvider(
        host=home.host,
        token=home.token,
        client_id=home.client_id,
        client_secret=home.client_secret,
        config={"warehouse_id": settings.DATABRICKS_WAREHOUSE_ID},
    )


def get_governance_uc_provider():
    """A ``DatabricksProvider`` for metastore-global UC reads run as the *app's*
    **governance service principal** — the background data-asset cache sync and
    (optionally) contract discovery.

    Unity Catalog is account-level, so these jobs aren't tied to a workspace, but
    they DO need an identity that holds ``BROWSE`` on the governed catalogs.
    That identity is the same one used for data certification: the SP of the
    ``SENTINEL_DATA_CERT_WORKSPACE`` target workspace. We authenticate as that SP
    but stay pinned to the **home host + home warehouse** (the home workspace can
    see the whole metastore and is where ``DATABRICKS_WAREHOUSE_ID`` lives).

    Falls back to the app's own SP when no certification/governance workspace is
    configured (or it can't be resolved), preserving the previous behavior. The
    governance SP therefore needs ``CAN USE`` on the home warehouse and
    ``BROWSE`` on each governed catalog.
    """
    from app.providers.databricks import DatabricksProvider

    home = get_home_workspace_config()
    client_id, client_secret, token = home.client_id, home.client_secret, home.token
    source = "home_sp"

    cert_name = (getattr(settings, "SENTINEL_DATA_CERT_WORKSPACE", "") or "").strip()
    if cert_name:
        cfg = get_workspace_config(cert_name)
        if cfg is not None and (cfg.client_id or cfg.token):
            client_id, client_secret, token = cfg.client_id, cfg.client_secret, cfg.token
            source = f"cert_workspace:{cfg.name} ({cfg.credential_source})"
        else:
            logger.warning(
                "Governance UC provider: SENTINEL_DATA_CERT_WORKSPACE=%r did not "
                "resolve to a usable service principal; using the app's own SP.",
                cert_name,
            )

    logger.info(
        "Governance UC provider: authenticating as %s against home host %s.",
        source, home.host,
    )
    return DatabricksProvider(
        host=home.host,
        token=token,
        client_id=client_id,
        client_secret=client_secret,
        config={"warehouse_id": settings.DATABRICKS_WAREHOUSE_ID},
    )


def catalogs_to_scan(client) -> Tuple[List[str], List[str]]:
    """Resolve which catalogs a governance scan should walk.

    Returns ``(catalogs, missing)``. When ``SCAN_CATALOGS`` is configured we scan
    exactly that allowlist, and ``missing`` names the configured catalogs the
    scanning principal cannot see — almost always a missing ``BROWSE`` grant or a
    typo, and the single most common cause of a "found nothing" run. When the
    allowlist is blank we fall back to every visible catalog (minus
    system/samples) and ``missing`` is empty.

    Every governance discovery path (contract sync, tag discovery) must go
    through this so the allowlist means the same thing everywhere.
    """
    from app.core.config import get_scan_catalogs

    try:
        visible = [c.name for c in client.catalogs.list() if c.name not in ("system", "samples")]
    except Exception as e:  # noqa: BLE001 - degrade to the configured list
        logger.warning("Could not list catalogs for the scanning principal: %s", e)
        visible = []

    configured = get_scan_catalogs()
    if not configured:
        logger.info("SCAN_CATALOGS blank — scanning all %d visible catalog(s).", len(visible))
        return visible, []

    missing = [c for c in configured if visible and c not in visible]
    if missing:
        logger.warning(
            "Configured catalogs not visible to the scanning principal: %s. "
            "Visible catalogs: %s. This is normally a missing BROWSE grant or a "
            "name typo in SCAN_CATALOGS.",
            missing, visible or "(none)",
        )
    logger.info("Scanning configured catalogs (SCAN_CATALOGS): %s", configured)
    return configured, missing


def uc_client_for(obo_token: Optional[str]):
    """Resolve ``(provider, client)`` for Unity Catalog reads, pinned to home.

    UC discovery/metadata tools must run **On-Behalf-Of the signed-in user** so
    the result reflects the USER's own grants — this is how the app confirms a
    user does (or does not) have access to a catalog/schema/table/volume. Only
    the local data-asset cache (``search_data_assets``) is trusted to the app
    SP's broad BROWSE; every live listing tool is OBO.

    Returns the home-workspace :class:`DatabricksProvider` (for ``execute_sql``)
    plus a ``WorkspaceClient`` bound to the user's identity. Falls back to the
    app SP ONLY in local dev; on any deployed target a missing user token raises
    rather than silently answering an access question under the wrong identity.
    """
    provider = get_uc_provider()
    if obo_token:
        return provider, provider.get_workspace_client(token=obo_token)

    from app.providers.databricks_mcp import sp_fallback_allowed

    if sp_fallback_allowed():
        return provider, provider.client

    raise PermissionError(
        "This Unity Catalog lookup must run as the signed-in user, but no user "
        "token was forwarded (X-Forwarded-Access-Token). Refusing to fall back "
        "to the app service principal outside local dev — it would answer the "
        "access question under the wrong identity."
    )


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
