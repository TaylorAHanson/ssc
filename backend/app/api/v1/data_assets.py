from typing import Dict, List, Optional
import asyncio
import hashlib
import logging
import re
import time
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session
from pydantic import BaseModel, ConfigDict
from app.db.session import get_db
from app.db.data_asset import DataAssetModel
from app.workers.tasks.sync_data_assets import is_metric_view
from app.api.deps import get_current_user
from app.services.data_asset_owner import resolve_data_asset_owner
from app.services.discover_domains import (
    OTHER_DOMAIN,
    OTHER_LABEL,
    Domain,
    load_domain_index,
    membership_fields,
    workspace_entity_memberships,
)
from app.services.discover_visibility import (
    has_hidden_tag,
    hidden_linked_dashboards,
    hidden_workspace_entities,
    load_visibility,
    visible_dashboards,
)
from datetime import datetime
import json

router = APIRouter()

logger = logging.getLogger(__name__)

# Catalog identifiers are interpolated into SQL, so restrict to a safe charset.
_UC_IDENT_RE = re.compile(r"^[A-Za-z0-9_]+$")

class DataQualitySchema(BaseModel):
    freshness: Optional[str] = None
    completeness: Optional[str] = None
    accuracy: Optional[str] = None

class DataAssetResponse(BaseModel):
    id: str
    catalog: str
    schema_name: str
    table_name: str
    type: str
    description: Optional[str] = None
    owner: Optional[str] = None
    # Databricks Discover domain tag keys (a subdomain's members included) and subdomain tag keys.
    domains: List[str] = []
    subdomains: List[str] = []
    tags: List[str] = []
    certified: bool = False
    contract_url: Optional[str] = None
    data_quality: Optional[dict] = None
    certification_violations: Optional[List[str]] = None
    sla: Optional[str] = None
    kpis: Optional[List[dict]] = None
    upstream_tables: Optional[List[dict]] = None
    downstream_dashboards: Optional[List[dict]] = None
    legacy_mappings: Optional[List[dict]] = None
    created_at: Optional[datetime] = None
    last_synced_at: datetime

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

@router.get("", response_model=List[DataAssetResponse])
@router.get("/", response_model=List[DataAssetResponse])
def list_data_assets(
    domain: Optional[str] = None,
    certified: Optional[bool] = None,
    certification_only: Optional[bool] = None,
    limit: Optional[int] = None,
    offset: int = 0,
    db: Session = Depends(get_db)
):
    """
    List cached data assets for discovery. ``domain`` is a Discover domain's tag
    key or id and includes the members of its subdomains.
    """
    query = db.query(DataAssetModel)
    vis = load_visibility(db)
    if vis.hidden_asset_ids:
        query = query.filter(DataAssetModel.id.notin_(vis.hidden_asset_ids))
    index = load_domain_index(db)

    if certified is not None:
        query = query.filter(DataAssetModel.certified == certified)
        
    if certification_only:
        from sqlalchemy import or_
        from app.db.data_contract import DataContractModel
        query = query.filter(
            or_(
                DataAssetModel.contract_url.isnot(None),
                DataAssetModel.certified == True,
                DataAssetModel.data_quality.isnot(None),
                DataAssetModel.id.in_(db.query(DataContractModel.dataset_id))
            )
        )

    if domain:
        # Membership comes from tags, so the domain filter (and the page after it) runs here.
        members = [(a, index.for_asset(a)) for a in vis.visible(query.order_by(DataAssetModel.id).all())]
        members = [(a, m) for a, m in members if index.matches(m, domain=domain)]
        members = members[offset:offset + limit if limit is not None else None]
    else:
        if offset > 0:
            query = query.offset(offset)
        if limit is not None:
            query = query.limit(limit)
        members = [(a, index.for_asset(a)) for a in vis.visible(query.all())]
    hidden_dashboards = hidden_linked_dashboards(a for a, _ in members)

    # Map 'schema' column to 'schema_name' for the Pydantic model since 'schema' is a reserved field name in BaseModel in pydantic sometimes,
    # actually let's just construct the response properly.
    result = []
    for asset, membership in members:
        result.append({
            "id": asset.id,
            "catalog": asset.catalog,
            "schema_name": asset.schema,
            "table_name": asset.table_name,
            "type": asset.type,
            "description": asset.description,
            "owner": resolve_data_asset_owner(asset.owner, asset.tags),
            **membership_fields(membership),
            "tags": asset.tags if asset.tags else [],
            "certified": asset.certified,
            "contract_url": asset.contract_url,
            "data_quality": asset.data_quality,
            "certification_violations": asset.certification_violations if isinstance(asset.certification_violations, list) else (json.loads(asset.certification_violations) if isinstance(asset.certification_violations, str) else None),
            "sla": asset.sla,
            "kpis": asset.kpis,
            "upstream_tables": vis.visible_upstream(asset.upstream_tables),
            "downstream_dashboards": visible_dashboards(asset.downstream_dashboards, hidden_dashboards),
            "legacy_mappings": asset.legacy_mappings,
            "created_at": asset.created_at,
            "last_synced_at": asset.last_synced_at
        })
        
    return result


def _domain_summary(d: Domain) -> dict:
    return {
        "label": d.name,
        "domain_id": d.id,
        "tag_key": d.tag_key,
        "subtitle": d.subtitle,
        "description": d.description,
        "icon": d.icon,
        "technical_owners": list(d.technical_owners),
        "business_owners": list(d.business_owners),
    }


_OTHER_SUMMARY = {
    "label": OTHER_LABEL,
    "domain_id": None,
    "tag_key": None,
    "subtitle": "Data not yet tagged with a Databricks Discover domain",
    "description": None,
    "icon": None,
    "technical_owners": [],
    "business_owners": [],
}


def _domain_entry(key: str, summary: dict, s: dict, subdomains: list) -> dict:
    return {
        "domain": key,
        **summary,
        "subdomain_count": len(subdomains),
        # False when no subdomain has assets, so the UI can open the domain's
        # assets directly instead of an empty subdomain list.
        "has_subdomains": bool(subdomains),
        "metric_view_count": len(s["metric_views"]),
        "table_count": s["tables"],
        "dashboard_count": len(s["dashboards"]),
        "subdomains": subdomains,
    }


@router.get("/domains")
def get_domains_hierarchy(db: Session = Depends(get_db)):
    """Databricks Discover domains holding at least one visible asset, with their subdomains.

    Domains and subdomains are defined in Databricks (Discover → Domains) and
    cached by the data-asset sync. An asset counts under every domain whose tag
    it carries or inherits from its catalog or schema; a subdomain's assets also
    count in its parent. Assets in no domain count under a last ``OTHER_DOMAIN``
    entry, which holds everything until a sync has read the domains.
    """
    index = load_domain_index(db)
    assets = load_visibility(db).visible(db.query(DataAssetModel).all())
    hidden_dashboards = hidden_linked_dashboards(assets)

    stats: dict[str, dict] = {}
    for a in assets:
        membership = index.for_asset(a)
        is_metric = is_metric_view(a.type)
        dashboards = {
            d.get("id") or d.get("name")
            for d in visible_dashboards(a.downstream_dashboards or [], hidden_dashboards)
            if isinstance(d, dict) and (d.get("id") or d.get("name"))
        } if is_metric else set()
        buckets = [index.get(k).id for k in (*membership.domains, *membership.subdomains)] or [OTHER_DOMAIN]
        for bucket in buckets:
            s = stats.setdefault(bucket, {"metric_views": [], "tables": 0, "schemas": set(), "dashboards": set()})
            if is_metric:
                s["metric_views"].append(a.table_name)
                s["dashboards"] |= dashboards
            else:
                s["tables"] += 1
            if a.schema:
                s["schemas"].add(a.schema)

    results = []
    for d in sorted(index.top_level, key=lambda x: x.name.lower()):
        s = stats.get(d.id)
        if not s:
            continue
        subdomains = [
            {
                "name": sub.name,
                **_domain_summary(sub),
                "metric_views_count": len(stats[sub.id]["metric_views"]),
                "tables_count": stats[sub.id]["tables"],
                "metric_views": stats[sub.id]["metric_views"][:8],
                "schemas": sorted(stats[sub.id]["schemas"])[:3],
                "kpis": [],
            }
            for sub in index.children(d.id)
            if sub.id in stats
        ]
        subdomains.sort(key=lambda x: (-x["metric_views_count"], -x["tables_count"], x["name"].lower()))
        results.append(_domain_entry(d.tag_key, _domain_summary(d), s, subdomains))
    if OTHER_DOMAIN in stats:
        results.append(_domain_entry(OTHER_DOMAIN, _OTHER_SUMMARY, stats[OTHER_DOMAIN], []))
    return results


@router.get("/metric_views")
def list_metric_views(
    domain: Optional[str] = None,
    subdomain: Optional[str] = None,
    query: Optional[str] = None,
    certified: Optional[bool] = None,
    db: Session = Depends(get_db),
):
    """Return all governed metric views matching filters.

    ``domain`` / ``subdomain`` are Discover tag keys or ids; ``subdomain`` may
    also be the subdomain's name within ``domain``.
    """
    from sqlalchemy import or_

    q = db.query(DataAssetModel).filter(DataAssetModel.type == "METRIC_VIEW")
    vis = load_visibility(db)
    if vis.hidden_asset_ids:
        q = q.filter(DataAssetModel.id.notin_(vis.hidden_asset_ids))
    index = load_domain_index(db)
    if certified is not None:
        q = q.filter(DataAssetModel.certified == certified)
    if query:
        term = f"%{query}%"
        q = q.filter(
            or_(
                DataAssetModel.table_name.ilike(term),
                DataAssetModel.description.ilike(term),
                DataAssetModel.schema.ilike(term),
            )
        )

    members = [(a, index.for_asset(a)) for a in vis.visible(q.all())]
    if domain or subdomain:
        members = [(a, m) for a, m in members if index.matches(m, domain=domain, subdomain=subdomain)]
    hidden_dashboards = hidden_linked_dashboards(a for a, _ in members)
    results = []
    for a, membership in members:
        results.append({
            "id": a.id,
            "catalog": a.catalog,
            "schema_name": a.schema,
            "table_name": a.table_name,
            "type": "METRIC_VIEW",
            "description": a.description,
            "owner": resolve_data_asset_owner(a.owner, a.tags),
            **membership_fields(membership),
            "tags": a.tags or [],
            "certified": bool(a.certified),
            # None = not loaded: read as the user via /metric_views/definitions.
            "kpis": a.kpis,
            "upstream_tables": vis.visible_upstream(a.upstream_tables),
            "downstream_dashboards": visible_dashboards(a.downstream_dashboards or [], hidden_dashboards),
            "contract_url": a.contract_url,
            "created_at": a.created_at,
            "last_synced_at": a.last_synced_at,
        })
    return results


def _quote_ident(name: str) -> str:
    return "`" + str(name).replace("`", "``") + "`"


def _view_text_from_describe(rows: list) -> Optional[str]:
    """The metric view YAML from a ``DESCRIBE TABLE EXTENDED ... AS JSON`` result."""
    if not rows:
        return None
    raw = rows[0].get("json_metadata")
    try:
        meta = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return None
    return meta.get("view_text") if isinstance(meta, dict) else None


# Metric view definitions read On-Behalf-Of a user, keyed by (user, asset id), so
# the cards and the detail panel don't re-run DESCRIBE for every view the user
# opens. Only successful reads are cached (a fresh grant takes effect at once);
# a revoked grant can show the cached definition until it expires, but live
# values are always queried as the user.
_DEFINITION_TTL_SECONDS = 600
_DEFINITION_CACHE_MAX = 5000
_definition_cache: "dict[tuple[str, str], tuple[float, Optional[str]]]" = {}
# Warehouse statements a single definitions batch runs at once.
_DEFINITION_BATCH_CONCURRENCY = 6
_DEFINITION_BATCH_MAX = 50


def _viewer_key(req: Request) -> str:
    """Who a cached definition was read as: the user's email, else their token."""
    email = (getattr(req.state, "user", None) or {}).get("email")
    if email:
        return email.lower()
    token = getattr(req.state, "token", None)
    return "token:" + hashlib.sha256(token.encode()).hexdigest() if token else "local"


def _unavailable_detail(reason: str, error: Optional[str] = None) -> dict:
    return {"available": False, "reason": reason, "kpis": [], "upstream_tables": [],
            "values": {}, "error": error, "error_kind": None}


def _run_as_user(obo_token: Optional[str]) -> dict:
    from app.core.config import settings

    return dict(warehouse=settings.DATABRICKS_WAREHOUSE_ID, obo_token=obo_token,
                require_obo=True, timeout_seconds=60)


def _asset_fqn(asset: DataAssetModel) -> str:
    return ".".join(_quote_ident(p) for p in (asset.catalog, asset.schema, asset.table_name))


async def load_metric_view_definition(asset: Optional[DataAssetModel], obo_token: Optional[str],
                                      viewer: str = "local") -> dict:
    """KPIs and source tables of a metric view, read from its definition as the user.

    The definition is read with ``DESCRIBE ... AS JSON`` through the SQL warehouse
    (needs only the ``sql`` scope, unlike the UC Tables API), so what's shown
    reflects *their* grants. Only identifiers from the cache are interpolated.

    ``available`` is False with a ``reason`` when nothing can be shown for this
    user: ``not_found``, ``no_warehouse``, ``no_obo`` (no forwarded user token on
    a deployed target), ``permission_denied`` or ``error``.
    """
    from app.core.config import settings
    from app.core.workspaces import get_uc_provider
    from app.providers.databricks_mcp import sp_fallback_allowed
    from app.workers.tasks.sync_data_assets import derive_metric_view_enrichments

    if not asset or not is_metric_view(asset.type):
        return _unavailable_detail("not_found")
    if not settings.DATABRICKS_WAREHOUSE_ID:
        return _unavailable_detail("no_warehouse")
    # Never read as the app SP on a deployed target; only local dev may fall back.
    if not obo_token and not sp_fallback_allowed():
        return _unavailable_detail("no_obo")

    key = (viewer, asset.id)
    cached = _definition_cache.get(key)
    if cached and cached[0] > time.monotonic():
        view_text = cached[1]
    else:
        try:
            described = await get_uc_provider().execute_sql(
                f"DESCRIBE TABLE EXTENDED {_asset_fqn(asset)} AS JSON", **_run_as_user(obo_token)
            )
        except Exception as e:
            logger.info("Metric view definition for %s unavailable to the user: %s", asset.id, e)
            kind = _classify_uc_error(str(e))
            return _unavailable_detail("permission_denied" if kind == "permission_denied" else "error", str(e))
        view_text = _view_text_from_describe(described.get("rows") or [])
        if len(_definition_cache) >= _DEFINITION_CACHE_MAX:
            _definition_cache.clear()
        _definition_cache[key] = (time.monotonic() + _DEFINITION_TTL_SECONDS, view_text)

    enrichments = derive_metric_view_enrichments(asset.type, view_text)
    return {"available": True, "reason": None, "kpis": enrichments.get("kpis") or [],
            "upstream_tables": enrichments.get("upstream_tables") or [],
            "values": {}, "error": None, "error_kind": None}


async def load_metric_view_detail(asset: Optional[DataAssetModel], obo_token: Optional[str],
                                  viewer: str = "local") -> dict:
    """The definition (see ``load_metric_view_definition``) plus current measure values.

    Values are queried live as the user, so they reflect their row filters. When
    the definition loads but the values query fails, ``available`` stays True and
    ``error`` / ``error_kind`` describe the values failure.
    """
    from app.core.workspaces import get_uc_provider

    detail = await load_metric_view_definition(asset, obo_token, viewer)
    measures = [k["measure"] for k in detail["kpis"] if k.get("measure")]
    if not detail["available"] or not measures:
        return detail

    select = ", ".join(f"MEASURE({_quote_ident(m)}) AS {_quote_ident(m)}" for m in measures)
    try:
        result = await get_uc_provider().execute_sql(
            f"SELECT {select} FROM {_asset_fqn(asset)}", **_run_as_user(obo_token)
        )
    except Exception as e:
        logger.info("Metric view values for %s unavailable: %s", asset.id, e)
        detail.update(error=str(e), error_kind=_classify_uc_error(str(e)))
        return detail

    rows = result.get("rows") or []
    detail["values"] = rows[0] if rows else {}
    return detail


@router.get("/metric_views/detail")
async def get_metric_view_detail(asset_id: str, req: Request, db: Session = Depends(get_db)):
    """KPIs, source tables and current values of a metric view, read as the user."""
    asset = db.query(DataAssetModel).filter(DataAssetModel.id == asset_id).first()
    vis = load_visibility(db)
    if vis.is_hidden_asset(asset):
        asset = None
    detail = await load_metric_view_detail(asset, getattr(req.state, "token", None), _viewer_key(req))
    detail["upstream_tables"] = vis.visible_upstream(detail.get("upstream_tables"))
    return detail


class MetricViewDefinitionsRequest(BaseModel):
    asset_ids: List[str]


@router.post("/metric_views/definitions")
async def get_metric_view_definitions(body: MetricViewDefinitionsRequest, req: Request,
                                      db: Session = Depends(get_db)):
    """KPIs and source tables for a batch of metric views (e.g. the cards on screen), read as the user.

    No values are queried — those load when a view is opened. Returns
    ``{"definitions": {asset_id: <definition>}}``; see ``load_metric_view_definition``.
    """
    ids = list(dict.fromkeys(body.asset_ids))[:_DEFINITION_BATCH_MAX]
    vis = load_visibility(db)
    assets = {
        a.id: a for a in vis.visible(db.query(DataAssetModel).filter(DataAssetModel.id.in_(ids)).all())
    } if ids else {}
    token, viewer = getattr(req.state, "token", None), _viewer_key(req)
    sem = asyncio.Semaphore(_DEFINITION_BATCH_CONCURRENCY)

    async def _one(asset_id: str):
        async with sem:
            definition = await load_metric_view_definition(assets.get(asset_id), token, viewer)
            definition["upstream_tables"] = vis.visible_upstream(definition.get("upstream_tables"))
            return asset_id, definition

    return {"definitions": dict(await asyncio.gather(*(_one(i) for i in ids)))}


class AccessibleAssetsResponse(BaseModel):
    available: bool
    mode: str
    accessible_ids: List[str] = []


@router.get("/accessible", response_model=AccessibleAssetsResponse)
async def get_accessible_assets(
    req: Request,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return the IDs of catalog assets the CURRENT USER can actually access.

    Accessibility is computed for real against Unity Catalog: for every catalog
    we hold assets in, we query that catalog's ``information_schema.tables``
    **as the user** (via their On-Behalf-Of token). Unity Catalog only surfaces
    objects the caller is privileged to see, so the result reflects the user's
    effective access (including grants inherited from the catalog/schema) — no
    heuristics or owner-name guessing.

    When the OBO token or a SQL warehouse isn't available (e.g. local dev),
    ``available`` is False and the caller should simply omit the
    "Accessible to me" filter rather than present a fabricated answer.
    """
    from app.core.config import settings
    from app.providers.databricks import DatabricksProvider

    obo_token = getattr(req.state, "token", None)
    warehouse_id = settings.DATABRICKS_WAREHOUSE_ID
    host = settings.DATABRICKS_HOST or settings.DATABRICKS_WORKSPACE_URL

    # Without a user token and a warehouse we cannot honestly answer "what can
    # *you* access", so we say so instead of inventing a result.
    if not obo_token or not warehouse_id or not host:
        return AccessibleAssetsResponse(available=False, mode="unavailable", accessible_ids=[])

    # Only scan catalogs we actually surface assets in — no full-metastore walk.
    catalog_rows = db.query(DataAssetModel.catalog).distinct().all()
    catalogs = [r[0] for r in catalog_rows if r[0] and _UC_IDENT_RE.match(str(r[0]))]
    if not catalogs:
        return AccessibleAssetsResponse(available=True, mode="obo", accessible_ids=[])

    try:
        provider = DatabricksProvider(
            host=host,
            token=settings.DATABRICKS_TOKEN,
            client_id=settings.DATABRICKS_CLIENT_ID,
            client_secret=settings.DATABRICKS_CLIENT_SECRET,
            config={"warehouse_id": warehouse_id},
        )
    except Exception as e:
        logger.warning(f"Accessible-assets: provider init failed: {e}")
        return AccessibleAssetsResponse(available=False, mode="unavailable", accessible_ids=[])

    async def _visible_fqns(catalog: str) -> set:
        # information_schema is per-catalog and is automatically filtered to the
        # objects the querying user can see.
        query = (
            f"SELECT table_schema, table_name "
            f"FROM `{catalog}`.information_schema.tables"
        )
        try:
            result = await provider.execute_sql(
                query,
                warehouse=warehouse_id,
                obo_token=obo_token,
                timeout_seconds=60,
            )
        except Exception as e:
            # Most often: the user lacks USE CATALOG here → nothing visible.
            logger.info(f"Accessible-assets: catalog '{catalog}' skipped: {e}")
            return set()
        fqns = set()
        for row in result.get("rows", []):
            schema = row.get("table_schema")
            table = row.get("table_name")
            if schema and table:
                fqns.add(f"{catalog}.{schema}.{table}".lower())
        return fqns

    per_catalog = await asyncio.gather(*[_visible_fqns(c) for c in catalogs])
    visible: set = set().union(*per_catalog) if per_catalog else set()

    # Map UC visibility back onto our asset IDs by fully-qualified name.
    accessible_ids: List[str] = []
    for asset in load_visibility(db).visible(db.query(DataAssetModel).all()):
        fqn = f"{asset.catalog}.{asset.schema}.{asset.table_name}".lower()
        if fqn in visible:
            accessible_ids.append(asset.id)

    return AccessibleAssetsResponse(available=True, mode="obo", accessible_ids=accessible_ids)


_NO_DOMAINS = {"domains": [], "subdomains": []}


def _entity_domains(db: Session, discover_type: str, ids, client) -> dict:
    """``domains`` / ``subdomains`` per dashboard, app or Genie space id, from its workspace tags."""
    found = workspace_entity_memberships(load_domain_index(db), discover_type, ids, client)
    return {i: membership_fields(m) for i, m in found.items()}


@router.get("/databricks/dashboards")
def get_databricks_dashboards(db: Session = Depends(get_db)):
    """Fetch available Lakeview dashboards from Databricks."""
    from app.providers.databricks import DatabricksProvider
    from app.core.config import settings
    from fastapi import HTTPException
    import logging
    logger = logging.getLogger(__name__)
    
    try:
        provider = DatabricksProvider(
            host=settings.DATABRICKS_HOST or settings.DATABRICKS_WORKSPACE_URL,
            token=settings.DATABRICKS_TOKEN,
            client_id=settings.DATABRICKS_CLIENT_ID,
            client_secret=settings.DATABRICKS_CLIENT_SECRET
        )
        dashboards = list(provider.client.lakeview.list())
        hidden = hidden_workspace_entities("dashboard", [d.dashboard_id for d in dashboards], provider.client)
        dashboards = [d for d in dashboards if d.dashboard_id not in hidden]
        domains = _entity_domains(db, "dashboard", [d.dashboard_id for d in dashboards], provider.client)
        return [
            {"id": d.dashboard_id, "name": d.display_name, "type": "dashboard", "path": d.parent_path,
             **domains.get(d.dashboard_id, _NO_DOMAINS)}
            for d in dashboards
        ]
    except Exception as e:
        logger.error(f"Failed to fetch dashboards: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/databricks/jobs")
def get_databricks_jobs():
    """Fetch available jobs from Databricks."""
    from app.providers.databricks import DatabricksProvider
    from app.core.config import settings
    from fastapi import HTTPException
    import logging
    logger = logging.getLogger(__name__)
    
    try:
        provider = DatabricksProvider(
            host=settings.DATABRICKS_HOST or settings.DATABRICKS_WORKSPACE_URL,
            token=settings.DATABRICKS_TOKEN,
            client_id=settings.DATABRICKS_CLIENT_ID,
            client_secret=settings.DATABRICKS_CLIENT_SECRET
        )
        jobs = provider.client.jobs.list()
        return [
            {"id": str(j.job_id), "name": j.settings.name, "type": "job", "creator": j.creator_user_name}
            for j in jobs if not has_hidden_tag(getattr(j.settings, "tags", None))
        ]
    except Exception as e:
        logger.error(f"Failed to fetch jobs: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/databricks/apps")
def get_databricks_apps(db: Session = Depends(get_db)):
    """Fetch available apps from Databricks."""
    from app.providers.databricks import DatabricksProvider
    from app.core.config import settings
    from fastapi import HTTPException
    import logging
    logger = logging.getLogger(__name__)
    
    try:
        provider = DatabricksProvider(
            host=settings.DATABRICKS_HOST or settings.DATABRICKS_WORKSPACE_URL,
            token=settings.DATABRICKS_TOKEN,
            client_id=settings.DATABRICKS_CLIENT_ID,
            client_secret=settings.DATABRICKS_CLIENT_SECRET
        )
        # Raw REST rather than ``apps.list()``: the SDK's App model doesn't carry
        # ``thumbnail_url`` yet, so it would be silently dropped.
        apps, page_token = [], None
        while True:
            resp = provider.client.api_client.do(
                "GET", "/api/2.0/apps", query={"page_token": page_token} if page_token else None
            ) or {}
            apps.extend(resp.get("apps") or [])
            page_token = resp.get("next_page_token")
            if not page_token:
                break
        hidden = hidden_workspace_entities("app", [a.get("name") for a in apps], provider.client)
        apps = [a for a in apps if a.get("name") and a["name"] not in hidden]
        domains = _entity_domains(db, "app", [a["name"] for a in apps], provider.client)
        return [
            {
                "id": a["name"],
                "name": a["name"],
                "type": "app",
                "creator": a.get("creator"),
                "description": a.get("description") or None,
                "url": a.get("url") or None,
                "thumbnail_url": a.get("thumbnail_url") or None,
                "updated_at": a.get("update_time"),
                **domains.get(a["name"], _NO_DOMAINS),
            }
            for a in apps
        ]
    except Exception as e:
        logger.error(f"Failed to fetch apps: {e}")
        # Return empty list if apps aren't supported in this workspace/SDK yet
        return []

def _user_uc_client(req: Request):
    """WorkspaceClient bound to the signed-in user, for Unity Catalog reads.

    Discover must only show metadata the user can see themselves, so these reads
    run On-Behalf-Of the user (home workspace), never as the app's service
    principal. ``uc_client_for`` refuses the SP fallback on deployed targets.
    """
    from app.core.workspaces import uc_client_for

    return uc_client_for(getattr(req.state, "token", None))[1]


def _classify_uc_error(message: str) -> str:
    """Translate raw UC SDK errors into user-facing strings.

    The Discover modal shows this in an inline banner so users understand
    *why* metadata is missing (most often: SP lacks USE CATALOG / SELECT
    grants on the target object).
    """
    m = (message or "").lower()
    # A view whose source/join table was dropped or renamed — checked before
    # "does not exist" since the message contains that too.
    if "uc_dependency_does_not_exist" in m:
        return "broken_dependency"
    # A view whose source column changed type (e.g. INT -> BIGINT) after the view
    # was created; it fails until its owner recreates it.
    if "cannot_up_cast_datatype" in m or "incompatible_view_schema_change" in m:
        return "schema_changed"
    # The app's sign-in token isn't allowed to call this API at all (Databricks
    # Apps user authorization only grants listed scopes). Checked before the
    # permission match: "token does not have required scopes" isn't the user
    # lacking a grant.
    if "required scope" in m or "insufficient_scope" in m or "invalid scope" in m:
        return "scope_missing"
    if "does not exist" in m:
        return "not_found"
    if ("permission" in m or "not authorized" in m or "access denied" in m or "forbidden" in m
            or "does not have" in m):  # UC: "User does not have SELECT on Table ..."
        return "permission_denied"
    return "error"


def _hidden_table_error(table_name: str) -> dict:
    """What a hidden asset's live endpoints return: indistinguishable from a missing table."""
    return {"error": f"Table {table_name} was not found.", "error_kind": "not_found"}


@router.get("/databricks/table")
def get_databricks_table_details(table_name: str, req: Request, db: Session = Depends(get_db)):
    """Return full Unity Catalog metadata for a single table.

    Always returns HTTP 200 with a payload so frontend can inspect the
    ``error`` field for not-found / permission errors. (Returning 4xx here
    would be intercepted by the app's SPA-fallback 404 handler, masking
    the real reason.)
    """
    from fastapi import HTTPException
    import logging

    logger = logging.getLogger(__name__)

    if not table_name or table_name.count(".") != 2:
        raise HTTPException(
            status_code=400,
            detail="table_name must be a fully qualified name (catalog.schema.table)",
        )

    base_response = {
        "table_name": table_name,
        "comment": None,
        "table_type": None,
        "data_source_format": None,
        "owner": None,
        "created_at": None,
        "updated_at": None,
        "columns": [],
        "tags": {},
        "error": None,
        "error_kind": None,
    }
    vis = load_visibility(db)
    if vis.is_hidden_id(table_name):
        return {**base_response, **_hidden_table_error(table_name)}

    try:
        client = _user_uc_client(req)

        try:
            info = client.tables.get(full_name=table_name)
        except Exception as e:
            msg = str(e)
            logger.warning(f"Failed to fetch table info for {table_name}: {msg}")
            kind = _classify_uc_error(msg)
            return {
                **base_response,
                "error": msg,
                "error_kind": kind,
            }

        columns = []
        for col in (getattr(info, "columns", None) or []):
            columns.append({
                "name": getattr(col, "name", None),
                "type": getattr(col, "type_text", None) or getattr(col, "type_name", None),
                "comment": getattr(col, "comment", None),
                "nullable": getattr(col, "nullable", None),
                "position": getattr(col, "position", None),
            })

        table_type = None
        if getattr(info, "table_type", None) is not None:
            tt = info.table_type
            table_type = str(tt.value) if hasattr(tt, "value") else str(tt)

        tags = {}
        try:
            uc_tags = client.entity_tag_assignments.list(
                entity_type="tables", entity_name=table_name
            )
            for t in uc_tags:
                if getattr(t, "tag_key", None):
                    tags[t.tag_key] = getattr(t, "tag_value", None)
        except Exception:
            pass
        # Live tags can be newer than the last sync.
        if vis.active and has_hidden_tag(tags, vis.keys):
            return {**base_response, **_hidden_table_error(table_name)}

        raw_owner = getattr(info, "owner", None)
        resolved_owner = resolve_data_asset_owner(raw_owner, tags)

        return {
            **base_response,
            "comment": getattr(info, "comment", None),
            "table_type": table_type,
            "data_source_format": getattr(info, "data_source_format", None) and str(info.data_source_format),
            "owner": resolved_owner,
            "created_at": getattr(info, "created_at", None),
            "updated_at": getattr(info, "updated_at", None),
            "columns": columns,
            "tags": tags,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error fetching table {table_name}: {e}")
        return {**base_response, "error": str(e), "error_kind": _classify_uc_error(str(e))}


_PREVIEW_ROW_LIMIT_MAX = 5000


def _preview_row_limit(requested: Optional[int]) -> int:
    """Rows to read: the admin's ``DISCOVER_PREVIEW_ROW_LIMIT`` (1..5000), or fewer if asked."""
    from app.core.config import settings

    try:
        cap = int(getattr(settings, "DISCOVER_PREVIEW_ROW_LIMIT", 500))
    except (TypeError, ValueError):
        cap = 500
    cap = min(max(cap, 1), _PREVIEW_ROW_LIMIT_MAX)
    return cap if requested is None else min(max(requested, 1), cap)


def _preview_error_kind(message: str) -> str:
    # SELECT on a table the user can't see at all reports TABLE_OR_VIEW_NOT_FOUND.
    if "table_or_view_not_found" in (message or "").lower():
        return "not_found"
    return _classify_uc_error(message)


@router.get("/databricks/table/preview")
async def get_databricks_table_preview(table_name: str, req: Request, limit: Optional[int] = None,
                                       db: Session = Depends(get_db)):
    """Sample rows of a table or view, read On-Behalf-Of the user on the SQL warehouse.

    Runs ``SELECT * FROM <fqn> LIMIT n`` as the user, so row filters, column
    masks and missing SELECT grants all apply. ``n`` is the admin-set
    ``DISCOVER_PREVIEW_ROW_LIMIT``; ``limit`` may lower it, never raise it.
    Like the sibling table endpoints, failures return HTTP 200 with ``error`` /
    ``error_kind`` (``permission_denied``, ``not_found``, ``broken_dependency``,
    ``schema_changed``, ``no_warehouse``, ``no_obo`` or ``error``); a malformed name is a 400.
    """
    from fastapi import HTTPException
    from app.core.config import settings
    from app.core.workspaces import get_uc_provider
    from app.providers.databricks_mcp import sp_fallback_allowed

    parts = (table_name or "").split(".")
    if len(parts) != 3 or not all(p.strip() for p in parts):
        raise HTTPException(
            status_code=400,
            detail="table_name must be a fully qualified name (catalog.schema.table)",
        )

    n = _preview_row_limit(limit)
    response = {"table_name": table_name, "columns": [], "rows": [], "row_count": 0,
                "limit": n, "truncated": False, "error": None, "error_kind": None}
    if load_visibility(db).is_hidden_id(table_name):
        return {**response, **_hidden_table_error(table_name)}

    if not settings.DATABRICKS_WAREHOUSE_ID:
        return {**response, "error": "No SQL warehouse is configured for previews.",
                "error_kind": "no_warehouse"}
    obo_token = getattr(req.state, "token", None)
    # Never read rows as the app SP on a deployed target; only local dev may fall back.
    if not obo_token and not sp_fallback_allowed():
        return {**response, "error": "Your sign-in token wasn't forwarded, so rows can't be read as you.",
                "error_kind": "no_obo"}

    fqn = ".".join(_quote_ident(p) for p in parts)
    try:
        result = await get_uc_provider().execute_sql(
            f"SELECT * FROM {fqn} LIMIT {n}", **_run_as_user(obo_token)
        )
    except Exception as e:
        msg = str(e)
        logger.info("Data preview of %s unavailable to the user: %s", table_name, msg)
        return {**response, "error": msg, "error_kind": _preview_error_kind(msg)}

    rows = result.get("rows") or []
    names = list(result.get("schema") or (rows[0].keys() if rows else []))
    types = list(result.get("column_types") or [])
    types += [None] * (len(names) - len(types))
    return {
        **response,
        "columns": [{"name": name, "type": t} for name, t in zip(names, types)],
        "rows": [[r.get(name) for name in names] if isinstance(r, dict) else list(r) for r in rows],
        "row_count": len(rows),
        "truncated": len(rows) >= n,
    }


@router.get("/databricks/lineage")
def get_databricks_table_lineage(table_name: str, req: Request, db: Session = Depends(get_db)):
    """Return immediate (1-hop) upstream/downstream tables for a UC table.

    `table_name` must be a fully qualified name like ``catalog.schema.table``.
    Used by the Discover page Lineage tab to render a click-to-expand graph
    similar to Databricks Catalog Explorer's lineage view.
    """
    from fastapi import HTTPException
    import logging

    logger = logging.getLogger(__name__)

    if not table_name or table_name.count(".") != 2:
        raise HTTPException(
            status_code=400,
            detail="table_name must be a fully qualified name (catalog.schema.table)",
        )

    vis = load_visibility(db)
    if vis.is_hidden_id(table_name):
        return {"table_name": table_name, "upstreams": [], "downstreams": [],
                **_hidden_table_error(table_name)}

    try:
        resp = _user_uc_client(req).api_client.do(
            "GET",
            "/api/2.0/lineage-tracking/table-lineage",
            query={"table_name": table_name, "include_entity_lineage": "true"},
        ) or {}

        def _extract(entries):
            results = []
            seen = set()
            for entry in entries or []:
                info = entry.get("tableInfo") or {}
                fqn = info.get("name")
                if not fqn or fqn in seen:
                    continue
                parts = (info.get("catalog_name"), info.get("schema_name"), info.get("table_name"))
                if vis.is_hidden_id(fqn) or (all(parts) and vis.is_hidden_id(".".join(parts))):
                    continue
                seen.add(fqn)
                results.append(
                    {
                        "name": fqn,
                        "catalog_name": info.get("catalog_name"),
                        "schema_name": info.get("schema_name"),
                        "table_name": info.get("table_name"),
                        "table_type": info.get("table_type"),
                    }
                )
            return results

        return {
            "table_name": table_name,
            "upstreams": _extract(resp.get("upstreams")),
            "downstreams": _extract(resp.get("downstreams")),
            "error": None,
            "error_kind": None,
        }
    except HTTPException:
        raise
    except Exception as e:
        msg = str(e)
        kind = _classify_uc_error(msg)
        logger.warning(f"Failed to fetch lineage for {table_name} ({kind}): {msg}")
        if kind == "scope_missing":
            # The lineage API is outside the app's sign-in scopes; the lineage
            # system table is plain SQL, which the user's token may run.
            from_system = _lineage_from_system_table(req, table_name, vis)
            if from_system is not None:
                return {"table_name": table_name, **from_system, "error": None, "error_kind": None,
                        "source": "system_table", "as_of": None}
        # Return 200 with the error fields so the frontend can render a clear
        # message (the SPA fallback 404 handler would otherwise mask details).
        # What the catalog sync recorded is better than an empty graph.
        cached = _cached_lineage(db, table_name, vis)
        return {
            "table_name": table_name,
            "upstreams": cached["upstreams"],
            "downstreams": cached["downstreams"],
            "error": msg,
            "error_kind": kind,
            "source": "catalog_cache" if cached["upstreams"] or cached["downstreams"] else None,
            "as_of": cached["as_of"],
        }


def _lineage_neighbor(fqn: str, table_type: Optional[str] = None) -> dict:
    parts = fqn.split(".")
    return {
        "name": fqn,
        "catalog_name": parts[0] if len(parts) == 3 else None,
        "schema_name": parts[1] if len(parts) == 3 else None,
        "table_name": parts[2] if len(parts) == 3 else None,
        "table_type": table_type,
    }


def _lineage_from_system_table(req: Request, table_name: str, vis) -> Optional[dict]:
    """One hop of lineage from ``system.access.table_lineage``, queried as the user.

    None when the user can't read the system table (it isn't granted to
    everyone) or no warehouse is configured.
    """
    from databricks.sdk.service.sql import StatementParameterListItem, StatementState
    from app.core.config import settings

    warehouse_id = settings.DATABRICKS_WAREHOUSE_ID
    if not warehouse_id:
        return None
    statement = (
        "SELECT DISTINCT source_table_full_name, source_type, target_table_full_name, target_type "
        "FROM system.access.table_lineage "
        "WHERE (source_table_full_name = :fqn OR target_table_full_name = :fqn) "
        "AND source_table_full_name IS NOT NULL AND target_table_full_name IS NOT NULL "
        "AND source_table_full_name <> target_table_full_name "
        "AND event_date >= date_sub(current_date(), 365) "
        "LIMIT 500"
    )
    try:
        resp = _user_uc_client(req).statement_execution.execute_statement(
            statement=statement,
            warehouse_id=warehouse_id,
            parameters=[StatementParameterListItem(name="fqn", value=table_name)],
            wait_timeout="30s",
        )
    except Exception as e:
        logger.info(f"Lineage system table unavailable for {table_name}: {e}")
        return None
    if not resp.status or resp.status.state != StatementState.SUCCEEDED:
        logger.info(f"Lineage system table query for {table_name} ended {resp.status and resp.status.state}")
        return None
    wanted = table_name.lower()
    upstreams: Dict[str, dict] = {}
    downstreams: Dict[str, dict] = {}
    for src, src_type, tgt, tgt_type in (resp.result.data_array if resp.result else None) or []:
        if not src or not tgt:
            continue
        if tgt.lower() == wanted and not vis.is_hidden_id(src):
            upstreams.setdefault(src.lower(), _lineage_neighbor(src, src_type))
        elif src.lower() == wanted and not vis.is_hidden_id(tgt):
            downstreams.setdefault(tgt.lower(), _lineage_neighbor(tgt, tgt_type))
    return {"upstreams": list(upstreams.values()), "downstreams": list(downstreams.values())}


def _cached_lineage(db: Session, table_name: str, vis) -> dict:
    """Lineage the catalog sync recorded: the sources of each metric view's definition."""
    wanted = table_name.lower()
    upstreams: Dict[str, dict] = {}
    downstreams: Dict[str, dict] = {}
    synced = []
    try:
        assets = db.query(DataAssetModel).filter(DataAssetModel.upstream_tables.isnot(None)).all()
    except Exception as e:
        logger.warning(f"Cached lineage unavailable for {table_name}: {e}")
        assets = []
    for asset in assets:
        sources = [u.get("fqn") for u in asset.upstream_tables if isinstance(u, dict) and u.get("fqn")] \
            if isinstance(asset.upstream_tables, list) else []
        if (asset.id or "").lower() == wanted:
            for fqn in sources:
                if not vis.is_hidden_id(fqn):
                    upstreams.setdefault(fqn.lower(), _lineage_neighbor(fqn))
            synced.append(asset.last_synced_at)
        elif any(fqn.lower() == wanted for fqn in sources) and asset.id and not vis.is_hidden_id(asset.id):
            downstreams.setdefault(asset.id.lower(), _lineage_neighbor(asset.id, "METRIC_VIEW"))
            synced.append(asset.last_synced_at)
    as_of = max((s for s in synced if s), default=None)
    return {
        "upstreams": list(upstreams.values()),
        "downstreams": list(downstreams.values()),
        "as_of": as_of.isoformat() if as_of else None,
    }


@router.get("/databricks/genie_spaces")
def get_databricks_genie_spaces(db: Session = Depends(get_db)):
    """Fetch available Genie Spaces from Databricks."""
    from app.providers.databricks import DatabricksProvider
    from app.core.config import settings
    from fastapi import HTTPException
    import logging
    logger = logging.getLogger(__name__)
    
    try:
        provider = DatabricksProvider(
            host=settings.DATABRICKS_HOST or settings.DATABRICKS_WORKSPACE_URL,
            token=settings.DATABRICKS_TOKEN,
            client_id=settings.DATABRICKS_CLIENT_ID,
            client_secret=settings.DATABRICKS_CLIENT_SECRET
        )
        # Handle potential absence of the genie API in older SDKs or if not configured.
        genie = getattr(provider.client, "genie", None)
        if genie is None or not hasattr(genie, "list_spaces"):
            return []

        # list_spaces() returns a GenieListSpacesResponse (.spaces + .next_page_token);
        # follow pagination so we don't silently truncate the catalog.
        results = []
        page_token = None
        while True:
            resp = genie.list_spaces(page_token=page_token)
            for s in resp.spaces or []:
                results.append(
                    {
                        "id": s.space_id,
                        "name": s.title,
                        "type": "genie_space",
                        "description": s.description,
                    }
                )
            page_token = resp.next_page_token
            if not page_token:
                break
        hidden = hidden_workspace_entities("genie_space", [r["id"] for r in results], provider.client)
        results = [r for r in results if r["id"] not in hidden]
        domains = _entity_domains(db, "genie_space", [r["id"] for r in results], provider.client)
        return [{**r, **domains.get(r["id"], _NO_DOMAINS)} for r in results]
    except Exception as e:
        logger.error(f"Failed to fetch genie spaces: {e}")
        return []
