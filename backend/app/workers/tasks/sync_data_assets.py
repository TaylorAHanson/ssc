import logging
import asyncio
from datetime import datetime, timezone
from croniter import croniter, CroniterBadCronError
from app.providers.databricks.client import DatabricksProvider
from app.db.session import get_db
from app.db.data_asset import DataAssetModel
from app.core.config import settings
from app.core.exceptions import AuthenticationError

logger = logging.getLogger(__name__)

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
            return # Too soon to sync again
            
    logger.info("Starting data assets sync...")
    
    # Calculate next time for the future
    if cron_expr:
        try:
            iter = croniter(cron_expr, now)
            _next_sync_time = iter.get_next(datetime)
        except CroniterBadCronError:
            pass
    
    try:
        host = settings.DATABRICKS_HOST
        token = settings.DATABRICKS_TOKEN
        client_id = settings.DATABRICKS_CLIENT_ID
        client_secret = settings.DATABRICKS_CLIENT_SECRET
        
        provider = DatabricksProvider(
            host=host,
            token=token,
            client_id=client_id,
            client_secret=client_secret
        )
        
        # Query information schema for tables and their tags
        query = """
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
            WHERE t.table_catalog NOT IN ('system', 'samples')
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
                    
                    # For demo purposes, we can mock domain/certified status here based on tags or randomly
                    # Or just leave them empty for now. We'll set some defaults.
                    domain = "Core" if "Core" in tags else "Analytics"
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
                db.query(DataAssetModel).filter(DataAssetModel.id.notin_(synced_ids), DataAssetModel.type != 'DATA_PRODUCT').delete()
                
                db.commit()
                logger.info(f"Successfully synced {len(rows)} data assets to Lakebase")
            except Exception as e:
                db.rollback()
                logger.error(f"Database error during data asset sync: {e}", exc_info=True)
            finally:
                db.close()
        else:
            logger.warning("No data assets fetched from Databricks")
            _last_sync_time = now
            
    except AuthenticationError as e:
        # Expected environmental condition (e.g. the workspace IP access list is
        # blocking this host's egress IP, or the SP lost a grant). Retrying won't
        # help, so log a concise warning instead of a full stack trace every run.
        logger.warning(f"Data asset sync skipped — Databricks access denied: {e}")
    except Exception as e:
        logger.error(f"Error during data asset sync task: {e}", exc_info=True)
