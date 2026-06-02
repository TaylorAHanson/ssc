import os
import glob
import yaml
import logging
from typing import List, Dict, Any
from app.providers.databricks.handlers.base import BaseResourceHandler
from app.core.config import settings

logger = logging.getLogger(__name__)

class DatasetResourceHandler(BaseResourceHandler):
    async def discover(self) -> List[Dict[str, Any]]:
        resources = []
        try:
            from app.db.session import get_db
            from app.db.data_contract import DataContractModel
            
            db = next(get_db())
            contracts = db.query(DataContractModel).filter(DataContractModel.is_active == True).all()
            
            contracted_datasets = {}
            for contract in contracts:
                try:
                    dataset_def = yaml.safe_load(contract.yaml_content)
                    full_name = contract.dataset_id
                    contracted_datasets[full_name] = dataset_def or {}
                    contracted_datasets[full_name]["_invalid_yaml"] = False
                except Exception as e:
                    logger.error(f"Failed to parse Data Contract {contract.dataset_id}: {e}")
                    full_name = contract.dataset_id
                    contracted_datasets[full_name] = {"_invalid_yaml": True}
                    
            for dp_name, dataset_def in contracted_datasets.items():
                try:
                    # Initialize an aggregated resource for the data product
                    resource = {
                        "id": dp_name,
                        "dataset_id": dp_name,
                        "type": "data_product",
                        "invalid_yaml": dataset_def.get("_invalid_yaml", False),
                        "assets": []
                    }
                    
                    servers = dataset_def.get("servers", [])
                    default_catalog = servers[0].get("catalog", "") if servers else ""
                    default_schema = servers[0].get("schema", "") if servers else ""
                    
                    schemas = dataset_def.get("schema", [])
                    
                    # We will loop through all physical tables and aggregate metadata
                    for this_schema in schemas:
                        physical_table = this_schema.get("physicalName")
                        if not physical_table:
                            continue
                            
                        table_catalog = this_schema.get("catalog")
                        table_schema = this_schema.get("schema")
                        
                        if table_catalog and table_schema:
                            catalog = table_catalog
                            schema = table_schema
                            # If physical_table already has dots, don't prepend again
                            if "." in physical_table:
                                full_name = physical_table
                            else:
                                full_name = f"{catalog}.{schema}.{physical_table}"
                        elif "." in physical_table and len(physical_table.split(".")) == 3:
                            full_name = physical_table
                            catalog, schema, table = full_name.split(".")
                        else:
                            catalog = default_catalog
                            schema = default_schema
                            if not catalog or not schema:
                                continue
                            full_name = f"{catalog}.{schema}.{physical_table}"
                        
                        asset_info = {
                            "name": full_name,
                            "type": "table",
                            "tags": {},
                            "failed_rule_count": -1,
                            "catalog_description": None,
                            "schema_description": None,
                            "all_columns_have_descriptions": False,
                            "rbac_defined": False,
                            "table_exists": True,
                            "missing_column_descriptions": []
                        }
                        
                        # Get tags for this specific table
                        try:
                            uc_tags = self.workspace_client.entity_tag_assignments.list(entity_type='tables', entity_name=full_name)
                            for tag_assign in uc_tags:
                                if tag_assign.tag_key:
                                    asset_info["tags"][tag_assign.tag_key] = tag_assign.tag_value
                        except Exception as e:
                            logger.error(f"Failed to fetch tags for {full_name}: {e}")
                            
                        reliability_window = asset_info["tags"].get("reliability_window")
                        if reliability_window:
                            try:
                                # Extract just the number from values like "7-days"
                                digits = "".join([c for c in str(reliability_window) if c.isdigit()])
                                window_days = int(digits) if digits else 7
                                if hasattr(settings, "DATABRICKS_WAREHOUSE_ID") and settings.DATABRICKS_WAREHOUSE_ID:
                                    query = f"""
WITH combined AS (
    SELECT assetInfo.assetUid, processed_at, items FROM enterprise_stg.data_quality.adoc_dq_history
    UNION ALL SELECT assetInfo.assetUid, processed_at, items FROM enterprise_stg.data_quality.adoc_freshness_history
    UNION ALL SELECT assetInfo.assetUid, processed_at, items FROM enterprise_stg.data_quality.adoc_data_drift_history
    UNION ALL SELECT assetInfo.assetUid, processed_at, items FROM enterprise_stg.data_quality.adoc_profile_anomaly_history
    --UNION ALL SELECT assetInfo.leftBackingAssetUid, processed_at, items FROM enterprise_stg.data_quality.adoc_reconciliation_history
    UNION ALL SELECT assetInfo.assetUid, processed_at, items FROM enterprise_stg.data_quality.adoc_schema_drift_history
)
SELECT count(1)
FROM combined
LATERAL VIEW explode(items) exploded AS item
WHERE assetUid LIKE '%{full_name}%'
AND cast(processed_at AS date) >= date_sub(current_date(), {window_days})
AND item.resultPercent < item.threshold
"""
                                    logger.info(
                                        f"Fetching failed rule count for asset '{full_name}' "
                                        f"(reliability_window={window_days} days). Query: {query}"
                                    )
                                    response = self.workspace_client.statement_execution.execute_statement(
                                        statement=query,
                                        warehouse_id=settings.DATABRICKS_WAREHOUSE_ID,
                                        wait_timeout="30s"
                                    )
                                    
                                    if response.status.state.value in ("FAILED", "CANCELED", "CLOSED"):
                                        error_msg = response.status.error.message if response.status.error else "Unknown SQL error"
                                        logger.error(f"SQL execution failed when fetching rule count for {full_name}. Query: {query} | Error: {error_msg}")
                                    elif response.result and response.result.data_array and len(response.result.data_array) > 0 and response.result.data_array[0][0] is not None:
                                        asset_info["failed_rule_count"] = int(response.result.data_array[0][0])
                            except Exception as e:
                                logger.error(f"Failed to fetch failed rule count for {full_name} via SQL. Query: {query if 'query' in locals() else 'Unknown'} | Exception: {e}")
                        
                        # Fetch metadata from Unity Catalog
                        try:
                            catalog_info = self.workspace_client.catalogs.get(name=catalog)
                            asset_info["catalog_description"] = catalog_info.comment
                        except Exception:
                            asset_info["catalog_description"] = None
                            
                        try:
                            schema_info = self.workspace_client.schemas.get(full_name=f"{catalog}.{schema}")
                            asset_info["schema_description"] = schema_info.comment
                        except Exception:
                            asset_info["schema_description"] = None
                            
                        try:
                            table_info = self.workspace_client.tables.get(full_name=full_name)
                            if hasattr(table_info, 'table_type') and table_info.table_type:
                                t_type = str(table_info.table_type.value) if hasattr(table_info.table_type, 'value') else str(table_info.table_type)
                                asset_info["type"] = "view" if "VIEW" in t_type.upper() else "table"
                            
                            columns = table_info.columns or []
                            missing_cols = [col.name for col in columns if not col.comment]
                            asset_info["all_columns_have_descriptions"] = len(missing_cols) == 0
                            asset_info["missing_column_descriptions"] = missing_cols
                        except Exception as e:
                            logger.warning(f"Failed to fetch table info for {full_name} from Unity Catalog: {e}")
                            asset_info["all_columns_have_descriptions"] = False
                            asset_info["table_exists"] = False
                            # Fallback convention check
                            if full_name.endswith("_v") or full_name.endswith("_view"):
                                asset_info["type"] = "view"
                            
                        try:
                            grants = self.workspace_client.grants.get(securable_type="table", full_name=full_name)
                            asset_info["rbac_defined"] = len(grants.privilege_assignments or []) > 0
                        except Exception:
                            asset_info["rbac_defined"] = False
                        
                        resource["assets"].append(asset_info)
                        
                    resources.append(resource)
                    
                except Exception as e:
                    logger.error(f"Failed to process dataset {dp_name}: {e}")
                    
        except Exception as e:
            logger.error(f"Failed during dataset discovery: {e}")
            
        finally:
            if 'db' in locals():
                db.close()
                
        return resources
        
    async def certify(self, resource_id: str) -> bool:
        logger.info(f"Certifying data product {resource_id}")
        try:
            if not hasattr(settings, "DATABRICKS_WAREHOUSE_ID") or not settings.DATABRICKS_WAREHOUSE_ID:
                logger.error("No warehouse_id defined, cannot certify dataset via SQL")
                return False
                
            from app.db.session import get_db
            from app.db.data_contract import DataContractModel
            import yaml
            
            db = next(get_db())
            contract = db.query(DataContractModel).filter(
                DataContractModel.dataset_id == resource_id,
                DataContractModel.is_active == True
            ).first()
            db.close()
            
            if not contract:
                logger.error(f"No active contract found for data product {resource_id}")
                return False
                
            dataset_def = yaml.safe_load(contract.yaml_content)
            servers = dataset_def.get("servers", [])
            default_catalog = servers[0].get("catalog", "") if servers else ""
            default_schema = servers[0].get("schema", "") if servers else ""
            
            schemas = dataset_def.get("schema", [])
            success = True
            
            for this_schema in schemas:
                physical_table = this_schema.get("physicalName")
                if not physical_table:
                    continue
                    
                table_catalog = this_schema.get("catalog")
                table_schema = this_schema.get("schema")
                
                if table_catalog and table_schema:
                    catalog = table_catalog
                    schema = table_schema
                    if "." in physical_table:
                        full_name = physical_table
                    else:
                        full_name = f"{catalog}.{schema}.{physical_table}"
                elif "." in physical_table and len(physical_table.split(".")) == 3:
                    full_name = physical_table
                    catalog, schema, table = full_name.split(".")
                else:
                    catalog = default_catalog
                    schema = default_schema
                    if not catalog or not schema:
                        continue
                    full_name = f"{catalog}.{schema}.{physical_table}"
                    
                query = f"ALTER TABLE {full_name} SET TAGS ('system.certification_status' = 'certified')"
                res = self.workspace_client.statement_execution.execute_statement(
                    statement=query,
                    warehouse_id=settings.DATABRICKS_WAREHOUSE_ID,
                    wait_timeout="30s"
                )
                if res.status.state.value in ("FAILED", "CANCELED", "CLOSED"):
                    error_msg = res.status.error.message if res.status.error else "Unknown SQL error"
                    logger.error(f"SQL execution failed to certify {full_name}: {error_msg}")
                    success = False
                    
            return success
        except Exception as e:
            logger.error(f"Failed to certify dataset {resource_id}: {e}")
            return False

    async def uncertify(self, resource_id: str) -> bool:
        logger.info(f"Un-certifying data product {resource_id}")
        try:
            if not hasattr(settings, "DATABRICKS_WAREHOUSE_ID") or not settings.DATABRICKS_WAREHOUSE_ID:
                logger.error("No warehouse_id defined, cannot uncertify dataset via SQL")
                return False
                
            from app.db.session import get_db
            from app.db.data_contract import DataContractModel
            import yaml
            
            db = next(get_db())
            contract = db.query(DataContractModel).filter(
                DataContractModel.dataset_id == resource_id,
                DataContractModel.is_active == True
            ).first()
            db.close()
            
            if not contract:
                logger.error(f"No active contract found for data product {resource_id}")
                return False
                
            dataset_def = yaml.safe_load(contract.yaml_content)
            servers = dataset_def.get("servers", [])
            default_catalog = servers[0].get("catalog", "") if servers else ""
            default_schema = servers[0].get("schema", "") if servers else ""
            
            schemas = dataset_def.get("schema", [])
            success = True
            
            for this_schema in schemas:
                physical_table = this_schema.get("physicalName")
                if not physical_table:
                    continue
                    
                table_catalog = this_schema.get("catalog")
                table_schema = this_schema.get("schema")
                
                if table_catalog and table_schema:
                    catalog = table_catalog
                    schema = table_schema
                    if "." in physical_table:
                        full_name = physical_table
                    else:
                        full_name = f"{catalog}.{schema}.{physical_table}"
                elif "." in physical_table and len(physical_table.split(".")) == 3:
                    full_name = physical_table
                    catalog, schema, table = full_name.split(".")
                else:
                    catalog = default_catalog
                    schema = default_schema
                    if not catalog or not schema:
                        continue
                    full_name = f"{catalog}.{schema}.{physical_table}"
                    
                query = f"ALTER TABLE {full_name} UNSET TAGS ('system.certification_status')"
                res = self.workspace_client.statement_execution.execute_statement(
                    statement=query,
                    warehouse_id=settings.DATABRICKS_WAREHOUSE_ID,
                    wait_timeout="30s"
                )
                if res.status.state.value in ("FAILED", "CANCELED", "CLOSED"):
                    error_msg = res.status.error.message if res.status.error else "Unknown SQL error"
                    logger.error(f"SQL execution failed to uncertify {full_name}: {error_msg}")
                    success = False
                    
            return success
        except Exception as e:
            logger.error(f"Failed to un-certify dataset {resource_id}: {e}")
            return False

    async def kill(self, resource_id: str) -> bool:
        return await self.uncertify(resource_id)

    async def warn(self, resource_id: str, message: str) -> bool:
        logger.info(f"Warning owner of dataset {resource_id}: {message}")
        return True
