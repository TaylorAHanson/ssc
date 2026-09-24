from typing import List, Optional
import asyncio
import logging
import re
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session
from pydantic import BaseModel, ConfigDict
from app.db.session import get_db
from app.db.data_asset import DataAssetModel
from app.workers.tasks.sync_data_assets import is_metric_view
from app.api.deps import get_current_user
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
    domain: Optional[str] = None
    subdomain: Optional[str] = None
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
    List cached data assets for discovery.
    """
    query = db.query(DataAssetModel)
    
    if domain:
        query = query.filter(DataAssetModel.domain == domain)
        
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
        
    if offset > 0:
        query = query.offset(offset)
        
    if limit is not None:
        query = query.limit(limit)
        
    assets = query.all()
    
    # Map 'schema' column to 'schema_name' for the Pydantic model since 'schema' is a reserved field name in BaseModel in pydantic sometimes,
    # actually let's just construct the response properly.
    result = []
    for asset in assets:
        result.append({
            "id": asset.id,
            "catalog": asset.catalog,
            "schema_name": asset.schema,
            "table_name": asset.table_name,
            "type": asset.type,
            "description": asset.description,
            "owner": asset.owner,
            "domain": asset.domain,
            "subdomain": asset.subdomain,
            "tags": asset.tags if asset.tags else [],
            "certified": asset.certified,
            "contract_url": asset.contract_url,
            "data_quality": asset.data_quality,
            "certification_violations": asset.certification_violations if isinstance(asset.certification_violations, list) else (json.loads(asset.certification_violations) if isinstance(asset.certification_violations, str) else None),
            "sla": asset.sla,
            "kpis": asset.kpis,
            "upstream_tables": asset.upstream_tables,
            "downstream_dashboards": asset.downstream_dashboards,
            "legacy_mappings": asset.legacy_mappings,
            "created_at": asset.created_at,
            "last_synced_at": asset.last_synced_at
        })
        
    return result


@router.get("/domains")
def get_domains_hierarchy(db: Session = Depends(get_db)):
    """Return hierarchical summary of domains, subdomains, metric views, and headline KPIs."""
    assets = db.query(DataAssetModel).all()
    domain_map: dict[str, dict] = {}

    domain_descriptions = {
        "Supply Chain": "Enterprise supply-chain semantic layer covering planning, procurement, manufacturing, and logistics",
        "Finance": "Core financial models, budget tracking, revenue variance, and OpEx forecasting",
        "Sales & Commercial": "Customer retention, sales pipeline execution, omnichannel demand, and merchandising",
        "Operations & Facilities": "Facility operations, power delivery, data center metrics, and IoT sensor streams",
        "Risk & Compliance": "Governance enforcement, KYC audits, data contract adherence, and fraud detection",
        "Enterprise Data": "Core shared lakehouse infrastructure, master data, and central analytical schemas",
    }

    subdomain_descriptions = {
        "Planning & Forecasting": "Demand planning, wafer capacity, sales forecasts, and supply-demand outlook",
        "Order Management": "Sales orders, order fulfillment, purchase forecasts, and order lifecycle management",
        "Procurement": "Direct and indirect procurement, purchase orders, quotations, supplier lead time, and receipts",
        "Manufacturing & WIP": "Manufacturing operations, WIP tracking, cost posting, cycle times, and bill of materials",
        "Yield & Foundry": "Yield analytics, foundry intelligence, wafer capacity planning, and quality metrics",
        "Inventory & Lot Tracking": "Inventory management, lot genealogy, traceability, and lot movements",
        "Planning & Budgeting": "Financial planning, OpEx budgets, entity plan attainment, and cost center variance",
        "Revenue & Margin": "Top-line revenue, contribution margins, product line profitability, and discount analysis",
        "Cost Management": "Standard vs actual cost variance, cost centers, expense run-rates, and scrap accounting",
        "Customer Analytics": "Customer 360, retention cohorts, subscriber churn, and lifetime value modeling",
        "Retail & Merchandising": "Omnichannel store analytics, catalog pricing rules, inventory sell-through, and promo tracking",
        "Sales Execution": "Deal intelligence, sales order conversion, rep attainment, and pipeline velocity",
        "Governance & Audit": "Access controls, certification violations, data contract compliance, and audit logs",
        "Site & Asset Performance": "Data center MW delivered, energy efficiency, equipment telemetry, and uptime SLAs",
        "General Analytics": "General enterprise analytics, staging tables, and operational views",
    }

    for a in assets:
        d = a.domain or "Enterprise Data"
        sd = a.subdomain or "General Analytics"
        is_metric = is_metric_view(a.type)

        if d not in domain_map:
            domain_map[d] = {
                "domain": d,
                "description": domain_descriptions.get(d, f"Curated semantic layer for {d}"),
                "subdomains": {},
                "metric_view_count": 0,
                "table_count": 0,
                "dashboard_count": 0,
            }

        dom_entry = domain_map[d]
        if is_metric:
            dom_entry["metric_view_count"] += 1
            if a.downstream_dashboards:
                dom_entry["dashboard_count"] += len(a.downstream_dashboards)
        else:
            dom_entry["table_count"] += 1

        if sd not in dom_entry["subdomains"]:
            dom_entry["subdomains"][sd] = {
                "name": sd,
                "description": subdomain_descriptions.get(sd, f"Subdomain covering {sd}"),
                "metric_views_count": 0,
                "tables_count": 0,
                "metric_views": [],
                "schemas": set(),
                "kpis": [],
            }

        sub_entry = dom_entry["subdomains"][sd]
        if not is_metric:
            sub_entry["tables_count"] += 1
        if a.schema:
            sub_entry["schemas"].add(a.schema)
        if is_metric:
            sub_entry["metric_views_count"] += 1
            sub_entry["metric_views"].append(a.table_name)

    results = []
    priority_order = ["Supply Chain", "Finance", "Sales & Commercial", "Operations & Facilities", "Risk & Compliance", "Enterprise Data"]
    sorted_domains = sorted(domain_map.keys(), key=lambda x: priority_order.index(x) if x in priority_order else 99)

    for d in sorted_domains:
        data = domain_map[d]
        subdomains_list = []
        for sd_name, sd_data in data["subdomains"].items():
            subdomains_list.append({
                "name": sd_data["name"],
                "description": sd_data["description"],
                "metric_views_count": max(sd_data["metric_views_count"], len(sd_data["metric_views"])),
                "tables_count": sd_data["tables_count"],
                "metric_views": sd_data["metric_views"][:8],
                "schemas": sorted(list(sd_data["schemas"]))[:3],
                "kpis": sd_data["kpis"],
            })

        subdomains_list.sort(key=lambda x: (x["metric_views_count"], x["tables_count"]), reverse=True)

        results.append({
            "domain": data["domain"],
            "description": data["description"],
            "subdomain_count": len(subdomains_list),
            "metric_view_count": data["metric_view_count"],
            "table_count": data["table_count"],
            "dashboard_count": data["dashboard_count"],
            "subdomains": subdomains_list,
        })

    return results


@router.get("/legacy_mappings")
def get_legacy_mappings(
    domain: Optional[str] = None,
    subdomain: Optional[str] = None,
    status: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Return legacy dashboard mappings with status (Active, Migrating, Deprecated)."""
    query = db.query(DataAssetModel).filter(DataAssetModel.legacy_mappings.isnot(None))
    if domain:
        query = query.filter(DataAssetModel.domain == domain)
    if subdomain:
        query = query.filter(DataAssetModel.subdomain == subdomain)

    assets = query.all()
    mappings = []
    seen = set()

    for a in assets:
        if not a.legacy_mappings:
            continue
        for m in a.legacy_mappings:
            dash_name = m.get("dashboard") or "Untitled Dashboard"
            key = (dash_name, a.domain, a.subdomain)
            if key in seen:
                continue
            seen.add(key)

            m_status = m.get("status", "Active")
            if status and m_status.lower() != status.lower():
                continue

            mappings.append({
                "id": f"{a.id}_{dash_name}".replace(" ", "_"),
                "dashboard": dash_name,
                "status": m_status,
                "owner": m.get("owner", a.owner or "Analytics Team"),
                "metric_view": m.get("metric_view", a.table_name),
                "metric_view_id": a.id,
                "subdomain": m.get("subdomain", a.subdomain or "Planning & Forecasting"),
                "domain": m.get("domain", a.domain or "Supply Chain"),
                "description": m.get("description", a.description or f"Legacy report mapped to {a.table_name}"),
            })

    return mappings


@router.get("/metric_views")
def list_metric_views(
    domain: Optional[str] = None,
    subdomain: Optional[str] = None,
    query: Optional[str] = None,
    certified: Optional[bool] = None,
    db: Session = Depends(get_db),
):
    """Return all governed metric views matching filters."""
    from sqlalchemy import or_

    q = db.query(DataAssetModel).filter(DataAssetModel.type == "METRIC_VIEW")
    if domain:
        q = q.filter(DataAssetModel.domain == domain)
    if subdomain:
        q = q.filter(DataAssetModel.subdomain == subdomain)
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

    assets = q.all()
    results = []
    for a in assets:
        results.append({
            "id": a.id,
            "catalog": a.catalog,
            "schema_name": a.schema,
            "table_name": a.table_name,
            "type": "METRIC_VIEW",
            "description": a.description,
            "owner": a.owner,
            "domain": a.domain,
            "subdomain": a.subdomain,
            "tags": a.tags or [],
            "certified": bool(a.certified),
            "kpis": a.kpis or [],
            "upstream_tables": a.upstream_tables or [],
            "downstream_dashboards": a.downstream_dashboards or [],
            "legacy_mappings": a.legacy_mappings or [],
            "contract_url": a.contract_url,
            "created_at": a.created_at,
            "last_synced_at": a.last_synced_at,
        })
    return results


def _quote_ident(name: str) -> str:
    return "`" + str(name).replace("`", "``") + "`"


@router.get("/metric_views/values")
async def get_metric_view_values(asset_id: str, req: Request, db: Session = Depends(get_db)):
    """Current value of each measure in a cached metric view, queried live.

    Runs On-Behalf-Of the signed-in user, so values reflect their own grants
    (and any row filters). Only the view and measure identifiers from the synced
    definition are used to build the statement — nothing from the request is
    interpolated. Returns ``values`` keyed by measure name, or ``error`` when the
    query can't be run (no warehouse, no access, bad definition).
    """
    from app.core.config import settings
    from app.core.workspaces import get_uc_provider

    asset = db.query(DataAssetModel).filter(DataAssetModel.id == asset_id).first()
    if not asset or not is_metric_view(asset.type):
        return {"values": {}, "error": "Metric view not found in the catalog cache."}
    measures = [k["measure"] for k in (asset.kpis or []) if isinstance(k, dict) and k.get("measure")]
    if not measures:
        return {"values": {}, "error": "No measures available: the sync identity couldn't read this metric view's definition, or it defines none."}
    if not settings.DATABRICKS_WAREHOUSE_ID:
        return {"values": {}, "error": "No SQL warehouse is configured."}

    fqn = ".".join(_quote_ident(p) for p in (asset.catalog, asset.schema, asset.table_name))
    select = ", ".join(f"MEASURE({_quote_ident(m)}) AS {_quote_ident(m)}" for m in measures)
    try:
        result = await get_uc_provider().execute_sql(
            f"SELECT {select} FROM {fqn}",
            warehouse=settings.DATABRICKS_WAREHOUSE_ID,
            obo_token=getattr(req.state, "token", None),
            require_obo=True,
            timeout_seconds=60,
        )
    except Exception as e:
        logger.info("Metric view values for %s unavailable: %s", asset_id, e)
        return {"values": {}, "error": str(e), "error_kind": _classify_uc_error(str(e))}

    rows = result.get("rows") or []
    return {"values": rows[0] if rows else {}, "error": None}


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
    for asset in db.query(DataAssetModel).all():
        fqn = f"{asset.catalog}.{asset.schema}.{asset.table_name}".lower()
        if fqn in visible:
            accessible_ids.append(asset.id)

    return AccessibleAssetsResponse(available=True, mode="obo", accessible_ids=accessible_ids)


@router.get("/databricks/catalogs")
def get_databricks_catalogs():
    """Fetch available catalogs from Databricks Unity Catalog."""
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
        catalogs = provider.client.catalogs.list()
        return [{"name": c.name, "comment": c.comment} for c in catalogs]
    except Exception as e:
        logger.error(f"Failed to fetch catalogs: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/databricks/schemas")
def get_databricks_schemas(catalog: str):
    """Fetch available schemas for a given catalog from Databricks."""
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
        schemas = provider.client.schemas.list(catalog_name=catalog)
        return [{"name": s.name, "comment": s.comment} for s in schemas]
    except Exception as e:
        logger.error(f"Failed to fetch schemas for {catalog}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/databricks/tables")
def get_databricks_tables(catalog: str, schema: str):
    """Fetch available tables and views for a given catalog and schema from Databricks."""
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
        tables = provider.client.tables.list(catalog_name=catalog, schema_name=schema)
        # Filter for actual tables or views (type is often 'MANAGED', 'EXTERNAL', 'VIEW')
        return [{"name": t.name, "type": t.table_type, "comment": t.comment} for t in tables]
    except Exception as e:
        logger.error(f"Failed to fetch tables for {catalog}.{schema}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/databricks/dashboards")
def get_databricks_dashboards():
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
        dashboards = provider.client.lakeview.list()
        return [{"id": d.dashboard_id, "name": d.display_name, "type": "dashboard", "path": d.parent_path} for d in dashboards]
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
        return [{"id": str(j.job_id), "name": j.settings.name, "type": "job", "creator": j.creator_user_name} for j in jobs]
    except Exception as e:
        logger.error(f"Failed to fetch jobs: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/databricks/apps")
def get_databricks_apps():
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
            }
            for a in apps
            if a.get("name")
        ]
    except Exception as e:
        logger.error(f"Failed to fetch apps: {e}")
        # Return empty list if apps aren't supported in this workspace/SDK yet
        return []

def _classify_uc_error(message: str) -> str:
    """Translate raw UC SDK errors into user-facing strings.

    The Discover modal shows this in an inline banner so users understand
    *why* metadata is missing (most often: SP lacks USE CATALOG / SELECT
    grants on the target object).
    """
    m = (message or "").lower()
    if "does not exist" in m:
        return "not_found"
    if "permission" in m or "not authorized" in m or "access denied" in m or "forbidden" in m:
        return "permission_denied"
    return "error"


@router.get("/databricks/table")
def get_databricks_table_details(table_name: str):
    """Return full Unity Catalog metadata for a single table.

    Always returns HTTP 200 with a payload so frontend can inspect the
    ``error`` field for not-found / permission errors. (Returning 4xx here
    would be intercepted by the app's SPA-fallback 404 handler, masking
    the real reason.)
    """
    from app.providers.databricks import DatabricksProvider
    from app.core.config import settings
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

    try:
        provider = DatabricksProvider(
            host=settings.DATABRICKS_HOST or settings.DATABRICKS_WORKSPACE_URL,
            token=settings.DATABRICKS_TOKEN,
            client_id=settings.DATABRICKS_CLIENT_ID,
            client_secret=settings.DATABRICKS_CLIENT_SECRET,
        )

        try:
            info = provider.client.tables.get(full_name=table_name)
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
            uc_tags = provider.client.entity_tag_assignments.list(
                entity_type="tables", entity_name=table_name
            )
            for t in uc_tags:
                if getattr(t, "tag_key", None):
                    tags[t.tag_key] = getattr(t, "tag_value", None)
        except Exception:
            pass

        return {
            **base_response,
            "comment": getattr(info, "comment", None),
            "table_type": table_type,
            "data_source_format": getattr(info, "data_source_format", None) and str(info.data_source_format),
            "owner": getattr(info, "owner", None),
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


@router.get("/databricks/lineage")
def get_databricks_table_lineage(table_name: str):
    """Return immediate (1-hop) upstream/downstream tables for a UC table.

    `table_name` must be a fully qualified name like ``catalog.schema.table``.
    Used by the Discover page Lineage tab to render a click-to-expand graph
    similar to Databricks Catalog Explorer's lineage view.
    """
    from app.providers.databricks import DatabricksProvider
    from app.core.config import settings
    from fastapi import HTTPException
    import logging

    logger = logging.getLogger(__name__)

    if not table_name or table_name.count(".") != 2:
        raise HTTPException(
            status_code=400,
            detail="table_name must be a fully qualified name (catalog.schema.table)",
        )

    try:
        provider = DatabricksProvider(
            host=settings.DATABRICKS_HOST or settings.DATABRICKS_WORKSPACE_URL,
            token=settings.DATABRICKS_TOKEN,
            client_id=settings.DATABRICKS_CLIENT_ID,
            client_secret=settings.DATABRICKS_CLIENT_SECRET,
        )
        resp = provider.client.api_client.do(
            "GET",
            f"/api/2.0/lineage-tracking/table-lineage?table_name={table_name}&include_entity_lineage=true",
        ) or {}

        def _extract(entries):
            results = []
            seen = set()
            for entry in entries or []:
                info = entry.get("tableInfo") or {}
                fqn = info.get("name")
                if not fqn or fqn in seen:
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
        logger.warning(f"Failed to fetch lineage for {table_name}: {msg}")
        # Return 200 with the error fields so the frontend can render a clear
        # message (the SPA fallback 404 handler would otherwise mask details).
        return {
            "table_name": table_name,
            "upstreams": [],
            "downstreams": [],
            "error": msg,
            "error_kind": _classify_uc_error(msg),
        }


@router.get("/databricks/genie_spaces")
def get_databricks_genie_spaces():
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
        return results
    except Exception as e:
        logger.error(f"Failed to fetch genie spaces: {e}")
        return []
