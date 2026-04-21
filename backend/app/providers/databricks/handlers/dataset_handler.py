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
            from app.db.session import get_lakebase_session
            from app.db.data_contract import DataContractModel
            
            db = get_lakebase_session()
            contracts = db.query(DataContractModel).filter(DataContractModel.is_active == True).all()
            
            contracted_datasets = {}
            for contract in contracts:
                try:
                    dataset_def = yaml.safe_load(contract.yaml_content)
                    full_name = contract.dataset_id
                    contracted_datasets[full_name] = dataset_def
                except Exception as e:
                    logger.error(f"Failed to parse Data Contract {contract.dataset_id}: {e}")
                    
            for dp_name, dataset_def in contracted_datasets.items():
                try:
                    # Initialize an aggregated resource for the data product
                    resource = {
                        "id": dp_name,
                        "dataset_id": dp_name,
                        "type": "data_product",
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
                            
                        if "." in physical_table and len(physical_table.split(".")) == 3:
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
                            "rbac_defined": False
                        }
                        
                        # Get tags for this specific table
                        try:
                            uc_tags = self.workspace_client.entity_tag_assignments.list(entity_type='tables', entity_name=full_name)
                            for tag_assign in uc_tags:
                                if tag_assign.tag_key:
                                    asset_info["tags"][tag_assign.tag_key] = tag_assign.tag_value
                        except Exception:
                            pass
                            
                        reliability_window = asset_info["tags"].get("reliability_window")
                        if reliability_window:
                            try:
                                if hasattr(settings, "DATABRICKS_WAREHOUSE_ID") and settings.DATABRICKS_WAREHOUSE_ID:
                                    query = f"SELECT COUNT(1) FROM {settings.DATABRICKS_ADOC_HISTORY_TABLE} LATERAL VIEW explode(items) as item WHERE assetInfo.assetUid = '{full_name}' AND cast(processed_at as date) >= date_sub(current_date(), {int(reliability_window)}) AND item.resultPercent < item.threshold"
                                    response = self.workspace_client.statement_execution.execute_statement(
                                        statement=query,
                                        warehouse_id=settings.DATABRICKS_WAREHOUSE_ID,
                                        wait_timeout="30s"
                                    )
                                    if response.result and response.result.data_array and len(response.result.data_array) > 0 and response.result.data_array[0][0] is not None:
                                        asset_info["failed_rule_count"] = int(response.result.data_array[0][0])
                            except Exception as e:
                                logger.error(f"Failed to fetch failed rule count for {full_name} via SQL: {e}")
                        
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
                            asset_info["all_columns_have_descriptions"] = all(bool(col.comment) for col in columns) if columns else False
                        except Exception:
                            asset_info["all_columns_have_descriptions"] = False
                            
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
                
            from app.db.session import get_lakebase_session
            from app.db.data_contract import DataContractModel
            import yaml
            
            db = get_lakebase_session()
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
                    
                if "." in physical_table and len(physical_table.split(".")) == 3:
                    full_name = physical_table
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
                
            from app.db.session import get_lakebase_session
            from app.db.data_contract import DataContractModel
            import yaml
            
            db = get_lakebase_session()
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
                    
                if "." in physical_table and len(physical_table.split(".")) == 3:
                    full_name = physical_table
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
