import json
import logging
import asyncio
import re

import dateutil.parser
import yaml
from datetime import datetime, timedelta, timezone
from croniter import croniter, CroniterBadCronError
from sqlalchemy import func
from app.db.session import get_lakebase_session
from app.db.data_asset import DataAssetModel
from app.core.config import settings
from app.core.exceptions import AuthenticationError

logger = logging.getLogger(__name__)

def infer_domain_and_subdomain(catalog: str, schema: str, table_name: str, tags: list[str]) -> tuple[str, str]:
    """Extract or infer domain and subdomain for a data asset."""
    domain = None
    subdomain = None

    # 1. Parse from tags if present (e.g., 'domain=Supply Chain', 'subdomain=Planning & Forecasting')
    for t in (tags or []):
        if not isinstance(t, str):
            continue
        lower_t = t.lower()
        if lower_t.startswith("domain="):
            domain = t.split("=", 1)[1].strip()
        elif lower_t.startswith("domain:"):
            domain = t.split(":", 1)[1].strip()
        elif lower_t.startswith("subdomain="):
            subdomain = t.split("=", 1)[1].strip()
        elif lower_t.startswith("subdomain:"):
            subdomain = t.split(":", 1)[1].strip()

    if domain and subdomain:
        return domain, subdomain

    # 2. Heuristic inference based on catalog, schema, table_name
    text = f"{catalog} {schema} {table_name}".lower()

    if any(k in text for k in ["supply", "lumentum", "uct", "sandisk", "procure", "demand", "leadtime", "wafer", "foundry", "wip", "yield", "lot_"]):
        inferred_domain = "Supply Chain"
        if any(k in text for k in ["demand", "forecast", "planning"]):
            inferred_subdomain = "Planning & Forecasting"
        elif any(k in text for k in ["order", "fulfillment", "po_"]):
            inferred_subdomain = "Order Management"
        elif any(k in text for k in ["procure", "purchase", "vendor", "supplier", "quotation", "leadtime"]):
            inferred_subdomain = "Procurement"
        elif any(k in text for k in ["manufactur", "wip", "scrap", "workcenter", "chipwip", "cost_posting"]):
            inferred_subdomain = "Manufacturing & WIP"
        elif any(k in text for k in ["yield", "foundry", "wafer_capacity", "rf360"]):
            inferred_subdomain = "Yield & Foundry"
        elif any(k in text for k in ["inventory", "lot", "stock", "genealogy", "traceability"]):
            inferred_subdomain = "Inventory & Lot Tracking"
        else:
            inferred_subdomain = "Planning & Forecasting"
    elif any(k in text for k in ["finance", "budget", "billing", "revenue", "opex", "cost", "fiscal", "actuals"]):
        inferred_domain = "Finance"
        if any(k in text for k in ["plan", "budget", "forecast", "attainment"]):
            inferred_subdomain = "Planning & Budgeting"
        elif any(k in text for k in ["revenue", "margin", "topline"]):
            inferred_subdomain = "Revenue & Margin"
        elif any(k in text for k in ["cost", "opex", "expense"]):
            inferred_subdomain = "Cost Management"
        else:
            inferred_subdomain = "Financial Reporting"
    elif any(k in text for k in ["sales", "gtm", "retail", "customer", "marketing", "merch", "sephora"]):
        inferred_domain = "Sales & Commercial"
        if any(k in text for k in ["customer", "c360", "retention", "churn"]):
            inferred_subdomain = "Customer Analytics"
        elif any(k in text for k in ["retail", "store", "merch", "omnichannel"]):
            inferred_subdomain = "Retail & Merchandising"
        else:
            inferred_subdomain = "Sales Execution"
    elif any(k in text for k in ["risk", "kyc", "compliance", "audit", "fraud"]):
        inferred_domain = "Risk & Compliance"
        inferred_subdomain = "Governance & Audit"
    elif any(k in text for k in ["iot", "grid", "facility", "facilities", "rosendin", "data_center"]):
        inferred_domain = "Operations & Facilities"
        inferred_subdomain = "Site & Asset Performance"
    else:
        inferred_domain = "Enterprise Data"
        inferred_subdomain = "General Analytics"

    return domain or inferred_domain, subdomain or inferred_subdomain


def is_metric_view(asset_type: str | None) -> bool:
    """A UC metric view is identified by its table type — never by its name."""
    return str(asset_type or "").upper() == "METRIC_VIEW"


def is_system_managed_table(table_name: str | None) -> bool:
    """Hidden, Databricks-managed tables (e.g. metric view materializations,
    ``__materialization_mat_*`` / ``__<uuid>_metric_view_mat_*``) are prefixed
    with ``__`` and aren't user-facing assets."""
    return (table_name or "").startswith("__")


_FQN_RE = re.compile(r"^`?[\w-]+`?\.`?[\w-]+`?\.`?[\w-]+`?$")
_AGG_RE = re.compile(r"^\s*([A-Za-z_]+)\s*\(")


def _infer_aggregation(expr: str) -> str | None:
    """Best-effort label for a measure expression: RATIO for a division of
    aggregates, else the leading aggregate function (SUM, COUNT, AVG, ...)."""
    if not expr:
        return None
    if "/" in expr:
        return "RATIO"
    m = _AGG_RE.match(expr)
    return m.group(1).upper() if m else None


def _source_tables(spec: dict) -> list[str]:
    """Table FQNs referenced as the metric view's ``source`` or join sources.
    SQL-query sources are skipped — they aren't a single table."""
    sources = []
    stack = [spec]
    while stack:
        node = stack.pop()
        src = node.get("source")
        if isinstance(src, str) and _FQN_RE.match(src.strip()):
            sources.append(src.strip().replace("`", ""))
        stack.extend(j for j in (node.get("joins") or []) if isinstance(j, dict))
    return list(dict.fromkeys(sources))


def derive_metric_view_enrichments(asset_type: str, view_definition: str | None) -> dict:
    """Derive KPIs and upstream source tables from a metric view's YAML definition.

    Returns ``{}`` for anything that isn't a metric view. KPI values are left
    unset — they're queried live, as the user, when the view is opened. Downstream
    dashboards come from UC lineage (filled in by the sync), and legacy mappings
    have no real source, so both start empty rather than invented.
    """
    if not is_metric_view(asset_type):
        return {}

    enrichments = {"kpis": [], "upstream_tables": [], "downstream_dashboards": [], "legacy_mappings": []}
    if not view_definition:
        return enrichments

    try:
        spec = yaml.safe_load(view_definition)
    except yaml.YAMLError as e:
        logger.warning("Could not parse metric view definition: %s", e)
        return enrichments
    if not isinstance(spec, dict):
        return enrichments

    dimensions = [
        d.get("display_name") or d.get("name")
        for d in (spec.get("dimensions") or [])
        if isinstance(d, dict) and (d.get("display_name") or d.get("name"))
    ]
    for m in spec.get("measures") or []:
        if not isinstance(m, dict) or not m.get("name"):
            continue
        expr = str(m.get("expr") or "")
        enrichments["kpis"].append({
            "name": m.get("display_name") or m["name"],
            # The identifier to pass to MEASURE() when querying live values.
            "measure": m["name"],
            "format": m.get("format") if isinstance(m.get("format"), dict) else None,
            "formula": expr,
            "aggregation": _infer_aggregation(expr),
            "value": None,
            "trend": None,
            "dimensions": dimensions,
            "description": m.get("comment"),
        })

    for fqn in _source_tables(spec):
        parts = fqn.split(".")
        enrichments["upstream_tables"].append({
            "name": parts[2],
            "fqn": fqn,
            "schema": parts[1],
            "type": "source",
            "description": None,
        })

    return enrichments


async def _fetch_metric_view_definitions(provider, fqns: list[str]) -> dict[str, str | None]:
    """Fetch the YAML ``view_definition`` for each metric view via the UC Tables API."""
    sem = asyncio.Semaphore(8)

    async def _one(fqn: str):
        async with sem:
            try:
                info = await asyncio.to_thread(provider.client.tables.get, fqn)
                return fqn, getattr(info, "view_definition", None)
            except Exception as e:  # noqa: BLE001 - one bad view mustn't fail the sync
                logger.warning("Could not fetch metric view definition for %s: %s", fqn, e)
                return fqn, None

    return dict(await asyncio.gather(*(_one(f) for f in fqns)))


async def _fetch_downstream_dashboards(provider, fqns: list[str]) -> dict[str, list[dict]]:
    """Dashboards that read each metric view, from Unity Catalog lineage.

    Names come from the home workspace's Lakeview dashboards; a dashboard that
    lineage reports but this workspace can't see (e.g. it lives in another
    workspace) is still listed, labelled by id, with no link.
    """
    sem = asyncio.Semaphore(8)

    async def _one(fqn: str):
        async with sem:
            try:
                resp = await asyncio.to_thread(
                    provider.client.api_client.do,
                    "GET",
                    "/api/2.0/lineage-tracking/table-lineage",
                    query={"table_name": fqn, "include_entity_lineage": "true"},
                )
            except Exception as e:  # noqa: BLE001 - one bad view mustn't fail the sync
                logger.warning("Could not fetch lineage for %s: %s", fqn, e)
                return fqn, []
        infos = []
        for entry in (resp or {}).get("downstreams") or []:
            infos.extend(entry.get("dashboardV3Infos") or [])
        return fqn, infos

    lineage = dict(await asyncio.gather(*(_one(f) for f in fqns)))
    if not any(lineage.values()):
        return {f: [] for f in fqns}

    try:
        known = {d.dashboard_id: d for d in await asyncio.to_thread(lambda: list(provider.client.lakeview.list()))}
    except Exception as e:  # noqa: BLE001 - fall back to id-only entries
        logger.warning("Could not list Lakeview dashboards to resolve lineage names: %s", e)
        known = {}
    host = (provider.client.config.host or "").rstrip("/")

    out: dict[str, list[dict]] = {}
    for fqn, infos in lineage.items():
        seen, dashboards = set(), []
        for info in infos:
            dash_id = info.get("dashboard_id")
            if not dash_id or dash_id in seen:
                continue
            seen.add(dash_id)
            d = known.get(dash_id)
            dashboards.append({
                "id": dash_id,
                "name": d.display_name if d else f"Dashboard {dash_id}",
                "type": "dashboard",
                "description": None if d else f"In workspace {info.get('workspace_id')}, not visible from this one",
                "url": f"{host}/dashboardsv3/{dash_id}/published" if d and host else None,
                "updated_at": info.get("lineage_timestamp"),
            })
        out[fqn] = dashboards
    return out


# Track next sync time
_next_sync_time = None
# The first poll after boot syncs immediately unless the cache is this fresh —
# enough to absorb dev auto-reloads without re-syncing on every file save.
_BOOT_SYNC_MIN_AGE = timedelta(minutes=10)
_boot_checked = False


def _cache_is_fresh(now: datetime) -> bool:
    db = get_lakebase_session()
    try:
        # DATA_PRODUCT rows aren't owned by this sync (Sentinel scans stamp them),
        # so they mustn't make the cache look fresh.
        last = (
            db.query(func.max(DataAssetModel.last_synced_at))
            .filter(DataAssetModel.type != "DATA_PRODUCT")
            .scalar()
        )
    finally:
        db.close()
    if last is None:
        return False
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return now - last < _BOOT_SYNC_MIN_AGE

async def sync_data_assets_task(force: bool = False):
    """
    Task to sync data assets from Databricks Information Schema into local Lakebase cache.
    Designed to be called periodically from the poller.
    """
    global _next_sync_time, _boot_checked
    now = datetime.now(timezone.utc)
    
    # Check if we should sync based on cron
    cron_expr = getattr(settings, 'DATA_ASSET_SYNC_CRON', '0 * * * *')
    if not force and cron_expr and not _boot_checked:
        _boot_checked = True
        if not _cache_is_fresh(now):
            force = True
            logger.info("Data asset cache is stale or empty at boot; syncing now.")
    if not force:
        if not cron_expr:
            return # Disabled
            
        if _next_sync_time is None:
            try:
                iter = croniter(cron_expr, now)
                _next_sync_time = iter.get_next(datetime)
            except CroniterBadCronError:
                logger.error(f"Invalid DATA_ASSET_SYNC_CRON expression: {cron_expr}")
                return
                
        if now < _next_sync_time:
            logger.debug(
                "Data asset sync skipped — next scheduled run at %s (cron=%s).",
                _next_sync_time.isoformat(), cron_expr,
            )
            return # Too soon to sync again
            
    logger.info("Starting data assets sync%s...", " (forced)" if force else "")
    
    # Calculate next time for the future
    if cron_expr:
        try:
            iter = croniter(cron_expr, now)
            _next_sync_time = iter.get_next(datetime)
        except CroniterBadCronError:
            pass
    
    try:
        # Run as the governance SP (the SENTINEL_DATA_CERT_WORKSPACE target
        # workspace's service principal) rather than the app's own SP. Unity
        # Catalog is metastore-global, so this is the identity that holds BROWSE
        # on the governed catalogs; the app's own SP typically does not. Falls
        # back to the app SP when no governance workspace is configured.
        from app.core.workspaces import get_governance_uc_provider
        provider = get_governance_uc_provider()

        # Log the identity we sync as — like the contract sync, what shows up in
        # system.information_schema.tables is filtered to this principal's grants,
        # so a "missing assets" complaint traces back to this SP's BROWSE grants.
        try:
            _me = provider.client.current_user.me()
            _identity = getattr(_me, "user_name", None) or getattr(_me, "display_name", None) or "unknown"
            logger.info(f"Data asset sync running as identity: {_identity}")
        except Exception as _e:  # noqa: BLE001 - diagnostic only
            logger.warning(f"Could not resolve data asset sync identity: {_e}")
        
        # Restrict to configured catalogs when SCAN_CATALOGS is set; otherwise
        # scan every catalog (minus system/samples) as before.
        from app.core.config import get_scan_catalogs
        _catalogs = get_scan_catalogs()
        if _catalogs:
            _in_list = ", ".join("'" + c.replace("'", "''") + "'" for c in _catalogs)
            catalog_filter = f"t.table_catalog IN ({_in_list})"
            logger.info("Data asset sync restricted to configured catalogs: %s", _catalogs)
        else:
            catalog_filter = "t.table_catalog NOT IN ('system', 'samples')"

        # Query information schema for tables and their tags
        query = f"""
            SELECT 
                t.table_catalog as catalog,
                t.table_schema as schema,
                t.table_name,
                t.table_type as type,
                t.comment as description,
                t.table_owner as owner,
                t.created as created_at,
                collect_list(tt.tag_name) as tags
            FROM system.information_schema.tables t
            LEFT JOIN system.information_schema.table_tags tt 
              ON t.table_catalog = tt.catalog_name 
             AND t.table_schema = tt.schema_name 
             AND t.table_name = tt.table_name
            WHERE {catalog_filter}
            GROUP BY 1, 2, 3, 4, 5, 6, 7
        """
        
        result = await provider.execute_sql(query, warehouse=settings.DATABRICKS_WAREHOUSE_ID)
        # Skip hidden Databricks-managed tables (metric view materializations);
        # they then fall out of the cache via the stale-asset delete below.
        rows = [r for r in result.get("rows", []) if not is_system_managed_table(r.get("table_name"))]

        metric_view_fqns = [
            f"{r.get('catalog')}.{r.get('schema')}.{r.get('table_name')}"
            for r in rows if is_metric_view(r.get("type"))
        ]
        definitions, dashboards_by_mv = (
            await asyncio.gather(
                _fetch_metric_view_definitions(provider, metric_view_fqns),
                _fetch_downstream_dashboards(provider, metric_view_fqns),
            )
            if metric_view_fqns else ({}, {})
        )
        missing = sum(1 for v in definitions.values() if not v)
        if missing:
            logger.warning(
                "%d of %d metric view definition(s) unavailable to the sync identity; "
                "their KPIs will be empty until it can read them.",
                missing, len(metric_view_fqns),
            )

        if rows:
            db = get_lakebase_session()
            try:
                # Upsert records into local SQLite
                # We'll just update existing and insert new
                synced_ids = set()
                for row in rows:
                    asset_id = f"{row.get('catalog')}.{row.get('schema')}.{row.get('table_name')}"
                    synced_ids.add(asset_id)
                    
                    tags = row.get("tags")
                    if isinstance(tags, str): # sometimes returns as stringified array
                        try:
                            tags = json.loads(tags)
                        except ValueError:
                            tags = []
                    if not tags:
                        tags = []
                    
                    domain, subdomain = infer_domain_and_subdomain(
                        row.get("catalog", ""),
                        row.get("schema", ""),
                        row.get("table_name", ""),
                        tags,
                    )
                    certified = "Certified" in tags or "certified" in tags or "system.certification_status" in tags or "certification_status" in tags

                    asset = db.query(DataAssetModel).filter(DataAssetModel.id == asset_id).first()
                    if not asset:
                        asset = DataAssetModel(
                            id=asset_id,
                            catalog=row.get("catalog"),
                            schema=row.get("schema"),
                            table_name=row.get("table_name"),
                            type=row.get("type", "TABLE"),
                        )
                        db.add(asset)

                    asset.description = row.get("description")
                    asset.owner = row.get("owner")
                    asset.tags = tags
                    asset.domain = domain
                    asset.subdomain = subdomain

                    # Enrich metric views from their YAML definition. Always assign so
                    # non-metric-views are cleared of any previously cached enrichment.
                    enrichments = derive_metric_view_enrichments(
                        row.get("type", "TABLE"), definitions.get(asset_id)
                    )
                    asset.kpis = enrichments.get("kpis")
                    asset.upstream_tables = enrichments.get("upstream_tables")
                    asset.downstream_dashboards = (
                        dashboards_by_mv.get(asset_id, []) if enrichments else None
                    )
                    asset.legacy_mappings = enrichments.get("legacy_mappings")

                    if certified:
                        asset.certified = True
                        if asset.contract_url and asset.contract_url.startswith("/requests/"):
                            asset.contract_url = None
                    elif asset.contract_url and asset.contract_url.startswith("/requests/"):
                        # Keep it as is; it might be a pending request or lag in Databricks Information Schema
                        pass
                    else:
                        asset.certified = False
                    
                    created_at_str = row.get("created_at")
                    if created_at_str:
                        try:
                            # Databricks usually returns ISO 8601 timestamps
                            # e.g., '2023-10-24T12:00:00.000Z'
                            asset.created_at = dateutil.parser.isoparse(created_at_str)
                        except Exception as e:
                            logger.warning(f"Could not parse created_at {created_at_str}: {e}")
                            
                    asset.last_synced_at = now
                    
                # Delete physical table assets that no longer exist in Databricks
                deleted = db.query(DataAssetModel).filter(
                    DataAssetModel.id.notin_(synced_ids), DataAssetModel.type != 'DATA_PRODUCT'
                ).delete(synchronize_session=False)
                
                db.commit()
                logger.info(
                    "Successfully synced %d data assets to Lakebase (removed %d stale asset(s)). "
                    "Next scheduled sync: %s.",
                    len(rows), deleted or 0,
                    _next_sync_time.isoformat() if _next_sync_time else "n/a",
                )
            except Exception as e:
                db.rollback()
                logger.error(f"Database error during data asset sync: {e}", exc_info=True)
            finally:
                db.close()
        else:
            logger.warning("No data assets fetched from Databricks")
            # When restricted to specific catalogs and we got nothing, log the
            # catalogs THIS identity can actually see so a zero-row run tells you
            # whether it's a name mismatch in SCAN_CATALOGS or a missing BROWSE
            # grant for the governance SP — instead of a silent empty result.
            if _catalogs:
                try:
                    diag = await provider.execute_sql(
                        "SELECT DISTINCT table_catalog FROM system.information_schema.tables ORDER BY 1",
                        warehouse=settings.DATABRICKS_WAREHOUSE_ID,
                    )
                    visible = [r.get("table_catalog") for r in diag.get("rows", [])]
                    logger.warning(
                        "Data asset sync saw 0 rows for configured catalogs %s. "
                        "Catalogs visible to the sync identity: %s. If your catalog "
                        "isn't listed, it's either a name mismatch in SCAN_CATALOGS "
                        "or the sync SP lacks BROWSE on it.",
                        _catalogs, visible or "(none)",
                    )
                except Exception as _diag_e:  # noqa: BLE001 - diagnostic only
                    logger.warning(
                        "Data asset sync diagnostic (visible catalogs) failed: %s", _diag_e
                    )
            
    except AuthenticationError as e:
        # Expected environmental condition (e.g. the workspace IP access list is
        # blocking this host's egress IP, or the SP lost a grant). Retrying won't
        # help, so log a concise warning instead of a full stack trace every run.
        logger.warning(f"Data asset sync skipped — Databricks access denied: {e}")
    except Exception as e:
        logger.error(f"Error during data asset sync task: {e}", exc_info=True)
