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
    # "enterprise" | "domain". The OmniGuard .rego policies read it as
    # ``input.workspace.type``.
    workspace_type: str = "domain"


class TargetWorkspaceInfo(BaseModel):
    """A configured target workspace without credentials, for listing and pickers.

    Has no credential fields, so it can't be handed to a client by mistake;
    resolve a :class:`WorkspaceConfig` with :func:`get_workspace_config` to
    connect. ``workspace_id`` / ``status`` come from the cached account
    workspace list when its URL matches ``host``.
    """
    name: str
    host: str
    environment: str
    workspace_type: str = "domain"
    workspace_id: Optional[str] = None
    status: Optional[str] = None


def resolve_workspace_type(name: Optional[str], explicit: Optional[str] = None) -> str:
    """The admin-set type if any, else enterprise only when the name says so."""
    explicit = (explicit or "").strip().lower()
    if explicit in ("enterprise", "domain"):
        return explicit
    return "enterprise" if "enterprise" in (name or "") else "domain"


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


def _configured_target_workspaces() -> List[Dict[str, Any]]:
    return _yaml_config.get("target_workspaces", []) or []


def _build_workspace_config(ws: Dict[str, Any]) -> WorkspaceConfig:
    """One configured workspace with its credentials resolved (may read secrets)."""
    client_id, client_secret, token, source = _resolve_credentials(ws)
    name = ws.get("name", "unknown")
    environment = ws.get("environment", "unknown")
    logger.debug(
        "Workspace %s (%s) resolved credentials from %s", name, environment, source
    )
    return WorkspaceConfig(
        name=name,
        host=ws.get("host", ""),
        environment=environment,
        client_id=client_id,
        client_secret=client_secret,
        token=token,
        credential_source=source,
        workspace_type=resolve_workspace_type(name, ws.get("type")),
    )


def _default_workspace_config() -> Optional[WorkspaceConfig]:
    """The home workspace under the name ``default``, used when none are configured."""
    host = settings.DATABRICKS_HOST or settings.DATABRICKS_WORKSPACE_URL
    if not host:
        return None
    return WorkspaceConfig(
        name="default",
        host=host,
        environment=settings.ENVIRONMENT,
        client_id=settings.DATABRICKS_CLIENT_ID or None,
        client_secret=settings.DATABRICKS_CLIENT_SECRET or None,
        token=settings.DATABRICKS_TOKEN or None,
        credential_source="global_default",
    )


def get_target_workspaces() -> List[WorkspaceConfig]:
    """
    Get the configured target workspaces (Admin -> Settings -> Target Workspaces,
    seeded by default_config.target_workspaces). Falls back to the default
    workspace in settings if none are configured.

    Each workspace's credentials are resolved via :func:`_resolve_credentials`
    using the install-wide secret scope + the workspace's inline SP key names.
    Only call this when you're about to connect to every workspace; to list or
    pick one, use :func:`get_target_workspace_info`, which reads no secrets.
    """
    workspaces = [_build_workspace_config(ws) for ws in _configured_target_workspaces()]

    # If no workspaces configured, add the default one.
    if not workspaces:
        default = _default_workspace_config()
        if default is not None:
            workspaces.append(default)

    return workspaces


def get_target_workspace_info(enrich: bool = True) -> List[TargetWorkspaceInfo]:
    """The configured target workspaces' names and hosts, without credentials.

    Reads config only, never the secret scope, so it costs no network calls.
    Same list and fallback as :func:`get_target_workspaces`. With ``enrich``,
    adds ``workspace_id`` / ``status`` from the cached account workspace list
    (one local DB read; skipped quietly if the cache is off or unreadable).
    """
    infos = [
        TargetWorkspaceInfo(
            name=ws.get("name", "unknown"),
            host=ws.get("host", ""),
            environment=ws.get("environment", "unknown"),
            workspace_type=resolve_workspace_type(ws.get("name", "unknown"), ws.get("type")),
        )
        for ws in _configured_target_workspaces()
    ]
    if not infos:
        default = _default_workspace_config()
        if default is not None:
            infos.append(
                TargetWorkspaceInfo(
                    name=default.name, host=default.host, environment=default.environment,
                )
            )
    if enrich and infos:
        _enrich_from_workspace_cache(infos)
    return infos


def _enrich_from_workspace_cache(infos: List[TargetWorkspaceInfo]) -> None:
    from app.services.workspace_cache import cached_by_host, normalize_host

    try:
        by_host = cached_by_host()
    except Exception as e:  # noqa: BLE001 - enrichment is optional
        logger.debug("Workspace cache unavailable for target-workspace enrichment: %s", e)
        return
    for info in infos:
        hit = by_host.get(normalize_host(info.host))
        if hit:
            info.workspace_id = hit.get("workspace_id")
            info.status = hit.get("status")


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

    Resolves credentials for the matching workspace only, not every configured one.
    """
    wanted_host = host_or_name.rstrip("/")
    configured = _configured_target_workspaces()
    for ws in configured:
        if (ws.get("host") or "").rstrip("/") == wanted_host or ws.get("name", "unknown") == host_or_name:
            return _build_workspace_config(ws)
    if not configured:
        default = _default_workspace_config()
        if default is not None and (
            default.host.rstrip("/") == wanted_host or default.name == host_or_name
        ):
            return default

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
