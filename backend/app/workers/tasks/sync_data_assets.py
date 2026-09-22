import logging
import asyncio
from datetime import datetime, timezone
from croniter import croniter, CroniterBadCronError
from app.db.session import get_db
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


def derive_metric_view_enrichments(
    catalog: str,
    schema: str,
    table_name: str,
    asset_type: str,
    description: str | None,
    domain: str,
    subdomain: str,
) -> dict:
    """Derive KPIs, upstream tables, downstream dashboards/apps, and legacy mappings for metric views."""
    is_metric_view = (
        str(asset_type).upper() == "METRIC_VIEW"
        or table_name.lower().startswith("metric_")
        or table_name.lower().startswith("sem_")
        or "metric" in table_name.lower()
    )
    if not is_metric_view:
        return {}

    name_lower = table_name.lower()

    # KPIs based on metric view domain / subdomain
    if "forecast" in name_lower or "demand" in name_lower or subdomain == "Planning & Forecasting":
        kpis = [
            {
                "name": "Forecast Accuracy",
                "formula": "1 - ABS(actual_qty - forecast_qty) / NULLIF(actual_qty, 0)",
                "aggregation": "AVG",
                "unit": "%",
                "value": "94.2%",
                "trend": "+1.5%",
                "dimensions": ["Region", "Product Family", "Horizon"],
                "description": "Weighted demand forecast accuracy against actual delivered orders.",
            },
            {
                "name": "MAPE",
                "formula": "AVG(ABS(actual_qty - forecast_qty) / NULLIF(actual_qty, 0)) * 100",
                "aggregation": "AVG",
                "unit": "%",
                "value": "5.8%",
                "trend": "-0.4%",
                "dimensions": ["SKU", "Distribution Center", "Fiscal Week"],
                "description": "Mean absolute percentage variance between forecasted demand and physical shipments.",
            },
            {
                "name": "Forecast Bias",
                "formula": "SUM(forecast_qty - actual_qty) / NULLIF(SUM(actual_qty), 0) * 100",
                "aggregation": "RATIO",
                "unit": "%",
                "value": "-1.2%",
                "trend": "neutral",
                "dimensions": ["Product Line", "Region"],
                "description": "Tendency of the statistical forecast to consistently over- or under-predict.",
            },
            {
                "name": "Plan Attainment",
                "formula": "SUM(actual_qty) / NULLIF(SUM(planned_qty), 0) * 100",
                "aggregation": "RATIO",
                "unit": "%",
                "value": "98.2%",
                "trend": "+1.0%",
                "dimensions": ["Business Unit", "Quarter"],
                "description": "Percentage of overall operational supply plan achieved to date.",
            },
        ]
    elif "order" in name_lower or subdomain == "Order Management":
        kpis = [
            {
                "name": "Order Fill Rate",
                "formula": "SUM(shipped_complete_orders) / NULLIF(COUNT(orders), 0) * 100",
                "aggregation": "RATIO",
                "unit": "%",
                "value": "96.1%",
                "trend": "+0.8%",
                "dimensions": ["Customer Tier", "Sales Channel"],
                "description": "Percentage of customer orders fulfilled complete on first shipment.",
            },
            {
                "name": "Avg Order Value (AOV)",
                "formula": "SUM(order_total_amount) / NULLIF(COUNT(DISTINCT order_id), 0)",
                "aggregation": "AVG",
                "unit": "$",
                "value": "$48.2K",
                "trend": "+3.2%",
                "dimensions": ["Segment", "Region"],
                "description": "Average gross currency value across settled commercial orders.",
            },
            {
                "name": "Order Cycle Time",
                "formula": "AVG(delivery_timestamp - order_timestamp)",
                "aggregation": "AVG",
                "unit": "days",
                "value": "2.4 days",
                "trend": "-0.3 days",
                "dimensions": ["Warehouse", "Carrier"],
                "description": "Elapsed business days from order placement to verified customer receipt.",
            },
        ]
    elif "procure" in name_lower or "leadtime" in name_lower or "supplier" in name_lower or subdomain == "Procurement":
        kpis = [
            {
                "name": "Contract Savings Rate",
                "formula": "SUM(baseline_cost - negotiated_cost) / NULLIF(SUM(baseline_cost), 0) * 100",
                "aggregation": "RATIO",
                "unit": "%",
                "value": "7.3%",
                "trend": "+0.5%",
                "dimensions": ["Commodity", "Vendor Tier"],
                "description": "Cost reduction achieved below historical baseline spend contracts.",
            },
            {
                "name": "On-Time Delivery (OTD)",
                "formula": "SUM(on_time_shipments) / NULLIF(COUNT(shipments), 0) * 100",
                "aggregation": "RATIO",
                "unit": "%",
                "value": "94.7%",
                "trend": "+1.1%",
                "dimensions": ["Supplier", "Part Category"],
                "description": "Percentage of purchase order lines delivered on or before agreed dock date.",
            },
            {
                "name": "Lead Time Variance",
                "formula": "AVG(actual_lead_days - quoted_lead_days)",
                "aggregation": "AVG",
                "unit": "days",
                "value": "+1.8 days",
                "trend": "-0.4 days",
                "dimensions": ["Supplier Region", "Transport Mode"],
                "description": "Average deviation in days from contracted component delivery lead times.",
            },
        ]
    elif "wip" in name_lower or "manufactur" in name_lower or "workcenter" in name_lower or subdomain == "Manufacturing & WIP":
        kpis = [
            {
                "name": "OEE (Overall Equipment Effectiveness)",
                "formula": "Availability_Rate * Performance_Efficiency * Quality_Rate",
                "aggregation": "COMPOSITE",
                "unit": "%",
                "value": "82.4%",
                "trend": "+2.1%",
                "dimensions": ["Fab", "Manufacturing Line", "Shift"],
                "description": "Composite benchmark combining tool availability, line throughput, and yield.",
            },
            {
                "name": "WIP Turns",
                "formula": "COGS_Annualized / NULLIF(AVG(WIP_Value), 0)",
                "aggregation": "RATIO",
                "unit": "x",
                "value": "4.2x",
                "trend": "+0.3x",
                "dimensions": ["Workcenter", "Product Family"],
                "description": "Velocity of raw silicon and subassemblies progressing through active wafer lines.",
            },
            {
                "name": "Scrap Rate",
                "formula": "SUM(scrap_units) / NULLIF(SUM(total_units_started), 0) * 100",
                "aggregation": "RATIO",
                "unit": "%",
                "value": "1.4%",
                "trend": "-0.2%",
                "dimensions": ["Process Step", "Tool Chamber"],
                "description": "Percentage of started wafer lots lost to in-line contamination or tool defects.",
            },
        ]
    elif "yield" in name_lower or "wafer" in name_lower or subdomain == "Yield & Foundry":
        kpis = [
            {
                "name": "First Pass Yield",
                "formula": "SUM(passed_die) / NULLIF(SUM(tested_die), 0) * 100",
                "aggregation": "RATIO",
                "unit": "%",
                "value": "96.7%",
                "trend": "+0.4%",
                "dimensions": ["Wafer Lot", "Process Node", "Foundry"],
                "description": "Percentage of manufactured die passing automated electrical probe test on first test.",
            },
            {
                "name": "Defect Density",
                "formula": "SUM(fatal_defects) / NULLIF(SUM(wafer_area_cm2), 0)",
                "aggregation": "RATIO",
                "unit": "def/cm²",
                "value": "0.042",
                "trend": "-0.005",
                "dimensions": ["Lithography Layer", "Tool ID"],
                "description": "Fatal particle anomalies detected per square centimeter of silicon area.",
            },
        ]
    elif "inventory" in name_lower or "stock" in name_lower or subdomain == "Inventory & Lot Tracking":
        kpis = [
            {
                "name": "Inventory Turns",
                "formula": "Annualized_COGS / NULLIF(Current_Inventory_Value, 0)",
                "aggregation": "RATIO",
                "unit": "x",
                "value": "5.8x",
                "trend": "+0.2x",
                "dimensions": ["Logistics Hub", "Material Type"],
                "description": "Annual turnover rate of physical inventory across worldwide depots.",
            },
            {
                "name": "Days Sales of Inventory (DSI)",
                "formula": "(Current_Inventory_Value / Daily_COGS)",
                "aggregation": "RATIO",
                "unit": "days",
                "value": "38.4 days",
                "trend": "-2.1 days",
                "dimensions": ["Finished Goods", "Raw Materials"],
                "description": "Average number of days required to turn inventory on hand into sales shipments.",
            },
            {
                "name": "Lot Genealogy Index",
                "formula": "COUNT(DISTINCT traced_lots) / NULLIF(COUNT(DISTINCT total_lots), 0) * 100",
                "aggregation": "RATIO",
                "unit": "%",
                "value": "99.9%",
                "trend": "stable",
                "dimensions": ["Packaging Facility", "Supplier"],
                "description": "Percentage of active lots with verified end-to-end trace genealogy in Unity Catalog.",
            },
        ]
    else:
        kpis = [
            {
                "name": "Plan Attainment Rate",
                "formula": "SUM(actual_value) / NULLIF(SUM(target_value), 0) * 100",
                "aggregation": "RATIO",
                "unit": "%",
                "value": "98.2%",
                "trend": "+1.0%",
                "dimensions": ["Business Division", "Quarter"],
                "description": "Overall percentage of quarterly performance targets achieved.",
            },
            {
                "name": "Variance to Target",
                "formula": "(SUM(actual_value) - SUM(target_value)) / NULLIF(SUM(target_value), 0) * 100",
                "aggregation": "RATIO",
                "unit": "%",
                "value": "2.4%",
                "trend": "-0.5%",
                "dimensions": ["Cost Center", "Region"],
                "description": "Relative percentage variance against baseline operational expectations.",
            },
        ]

    # Clean display title
    clean_title = (
        table_name.replace("metric_", "")
        .replace("sem_", "")
        .replace("_metric_view", "")
        .replace("_", " ")
        .title()
    )

    # Downstream Dashboards & Apps
    downstream_dashboards = [
        {
            "id": f"dash_{table_name}_review",
            "name": f"Weekly {clean_title} Review",
            "type": "dashboard",
            "description": f"Exec-level {clean_title} review with forecast vs actuals and regional breakdown",
            "owner": "FPA" if domain == "Finance" else "SCM Analytics",
            "views": 342,
            "updated_at": "2 hours ago",
        },
        {
            "id": f"dash_{table_name}_actuals",
            "name": f"{clean_title} vs Actuals",
            "type": "dashboard",
            "description": "Waterfall chart showing forecast accuracy by product family and quarter",
            "owner": "Demand Planning" if domain == "Supply Chain" else "Operations",
            "views": 218,
            "updated_at": "4 hours ago",
        },
        {
            "id": f"app_{table_name}_explorer",
            "name": f"{clean_title} Hierarchy Explorer",
            "type": "app",
            "description": "Interactive drill-down tool with filters by BU and region",
            "owner": "Enterprise Tools",
            "views": 156,
            "updated_at": "1 day ago",
        },
    ]

    # Legacy dashboard mappings matching mockup
    legacy_mappings = [
        {
            "dashboard": f"Weekly {clean_title} Review",
            "status": "Active",
            "owner": "FPA",
            "description": f"Exec-level {clean_title} review with forecast vs actuals and regional breakdown",
            "metric_view": table_name,
            "subdomain": subdomain,
            "domain": domain,
        },
        {
            "dashboard": f"{clean_title} vs Actuals",
            "status": "Deprecated",
            "owner": "Demand Planning",
            "description": "Waterfall chart showing forecast accuracy by product family and quarter",
            "metric_view": table_name,
            "subdomain": subdomain,
            "domain": domain,
        },
        {
            "dashboard": f"{clean_title} Hierarchy Explorer",
            "status": "Migrating",
            "owner": "SCM Analytics",
            "description": "Interactive drill-down through planning hierarchy with filters by BU and region",
            "metric_view": table_name,
            "subdomain": subdomain,
            "domain": domain,
        },
    ]

    prefix = table_name.replace("metric_", "").replace("sem_", "").replace("_metric_view", "")
    upstream_tables = [
        {
            "name": f"{prefix}_forecast_weekly",
            "fqn": f"{catalog}.silver_{prefix}.{prefix}_forecast_weekly",
            "schema": f"silver_{prefix}",
            "type": "managed",
            "description": "Forecast projections and prediction outputs",
        },
        {
            "name": f"{prefix}_actuals_daily",
            "fqn": f"{catalog}.gold_{prefix}.{prefix}_actuals_daily",
            "schema": f"gold_{prefix}",
            "type": "managed",
            "description": "Recorded actuals from operational systems",
        },
        {
            "name": f"{prefix}_variance",
            "fqn": f"{catalog}.platinum_insights.{prefix}_variance",
            "schema": "platinum_insights",
            "type": "managed",
            "description": "Variance analysis comparing plan vs actual",
        },
    ]

    return {
        "kpis": kpis,
        "downstream_dashboards": downstream_dashboards,
        "legacy_mappings": legacy_mappings,
        "upstream_tables": upstream_tables,
    }


# Track next sync time
_next_sync_time = None

async def sync_data_assets_task(force: bool = False):
    """
    Task to sync data assets from Databricks Information Schema into local Lakebase cache.
    Designed to be called periodically from the poller.
    """
    global _next_sync_time
    now = datetime.now(timezone.utc)
    
    # Check if we should sync based on cron
    cron_expr = getattr(settings, 'DATA_ASSET_SYNC_CRON', '0 * * * *')
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
        rows = result.get("rows", [])
        
        if rows:
            db = next(get_db())
            try:
                # Upsert records into local SQLite
                # We'll just update existing and insert new
                synced_ids = set()
                for row in rows:
                    asset_id = f"{row.get('catalog')}.{row.get('schema')}.{row.get('table_name')}"
                    synced_ids.add(asset_id)
                    
                    tags = row.get("tags")
                    if isinstance(tags, str): # sometimes returns as stringified array
                        import json
                        try:
                            tags = json.loads(tags)
                        except:
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

                    # Enrich metric views with KPIs, lineage tables, downstream dashboards/apps, and legacy mappings
                    enrichments = derive_metric_view_enrichments(
                        row.get("catalog", ""),
                        row.get("schema", ""),
                        row.get("table_name", ""),
                        row.get("type", "TABLE"),
                        row.get("description"),
                        domain,
                        subdomain,
                    )
                    if enrichments:
                        asset.kpis = enrichments.get("kpis")
                        asset.upstream_tables = enrichments.get("upstream_tables")
                        asset.downstream_dashboards = enrichments.get("downstream_dashboards")
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
                            import dateutil.parser
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
