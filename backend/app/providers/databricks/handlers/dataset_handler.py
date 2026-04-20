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
                    
            for full_name in contracted_datasets.keys():
                try:
                    parts = full_name.split(".")
                    if len(parts) != 3:
                        continue
                    catalog, schema, physical_table = parts
                    
                    dataset_def = contracted_datasets.get(full_name, {})
                    
                    data_product = dataset_def.get("dataProduct", physical_table)
                    schemas = dataset_def.get("schema", [])
                    
                    # Find the schema definition for THIS specific dataset
                    this_schema = next((s for s in schemas if s.get("physicalName") == physical_table), schemas[0] if schemas else {})
                    
                    root_custom_props = {prop.get("property"): prop.get("value") for prop in dataset_def.get("customProperties", [])}
                    schema_custom_props = {prop.get("property"): prop.get("value") for prop in this_schema.get("customProperties", [])}
                    
                    # Extract quality rules from this specific schema definition
                    quality_rules = this_schema.get("quality", [])
                    tdq_threshold = 100
                    bdq_threshold = 100
                    for rule in quality_rules:
                        if rule.get("id") == "technical_dq_threshold":
                            tdq_threshold = rule.get("mustBe", 100)
                        elif rule.get("id") == "business_dq_threshold":
                            bdq_threshold = rule.get("mustBe", 100)
                    
                    resource = {
                        "id": data_product,
                        "dataset_id": full_name,
                        "type": "table",
                        "has_contract": True,
                        "tdq_threshold": tdq_threshold,
                        "bdq_threshold": bdq_threshold,
                        "abac_needed": schema_custom_props.get("abac_required", False),
                        "abac_defined": False,
                        "data_classification": schema_custom_props.get("classification", ""),
                        "tags": {}
                    }
                    
                    # Fetch TDQ and BDQ scores
                    resource["tdq_score"] = 0
                    resource["bdq_score"] = 0
                    try:
                        if hasattr(settings, "DATABRICKS_WAREHOUSE_ID") and settings.DATABRICKS_WAREHOUSE_ID:
                            query = f"SELECT tdq_score, bdq_score FROM {settings.DATABRICKS_DATA_QUALITY_TABLE} WHERE dataset_id = '{full_name}' ORDER BY run_date DESC LIMIT 1"
                            response = self.workspace_client.statement_execution.execute_statement(
                                statement=query,
                                warehouse_id=settings.DATABRICKS_WAREHOUSE_ID,
                                wait_timeout="30s"
                            )
                            if response.result and response.result.data_array and len(response.result.data_array) > 0:
                                resource["tdq_score"] = float(response.result.data_array[0][0])
                                resource["bdq_score"] = float(response.result.data_array[0][1])
                    except Exception as e:
                        logger.error(f"Failed to fetch TDQ/BDQ scores for {full_name} via SQL: {e}")
                    
                    # Fetch metadata from Unity Catalog
                    try:
                        catalog_info = self.workspace_client.catalogs.get(name=catalog)
                        resource["catalog_description"] = catalog_info.comment
                    except Exception:
                        resource["catalog_description"] = None
                        
                    try:
                        schema_info = self.workspace_client.schemas.get(full_name=f"{catalog}.{schema}")
                        resource["schema_description"] = schema_info.comment
                    except Exception:
                        resource["schema_description"] = None
                        
                    try:
                        table_info = self.workspace_client.tables.get(full_name=full_name)
                        columns = table_info.columns or []
                        resource["all_columns_have_descriptions"] = all(bool(col.comment) for col in columns) if columns else False
                    except Exception:
                        resource["all_columns_have_descriptions"] = False
                        
                    try:
                        grants = self.workspace_client.grants.get(securable_type="table", full_name=full_name)
                        resource["rbac_defined"] = len(grants.privilege_assignments or []) > 0
                    except Exception:
                        resource["rbac_defined"] = False
                        
                    try:
                        uc_tags = self.workspace_client.entity_tag_assignments.list(entity_type='tables', entity_name=full_name)
                        # Replace the "known" tags from the contract entirely if they are present in Unity Catalog
                        for tag_assign in uc_tags:
                            if tag_assign.tag_key:
                                resource["tags"][tag_assign.tag_key] = tag_assign.tag_value
                    except Exception:
                        pass
                        
                    # Fallback for data_classification if not in contract but present in tags
                    if not resource["data_classification"] and "data_classification" in resource["tags"]:
                        resource["data_classification"] = resource["tags"]["data_classification"]
                        
                    if root_custom_props.get("is_mock") is True:
                        resource["tdq_score"] = tdq_threshold
                        resource["bdq_score"] = bdq_threshold
                        resource["catalog_description"] = resource["catalog_description"] or "Mock Catalog Description"
                        resource["schema_description"] = resource["schema_description"] or "Mock Schema Description"
                        resource["all_columns_have_descriptions"] = True
                        resource["rbac_defined"] = True
                        if resource["abac_needed"]:
                            resource["abac_defined"] = True
                        
                    resources.append(resource)
                    
                except Exception as e:
                    logger.error(f"Failed to process dataset {full_name}: {e}")
                    
        except Exception as e:
            logger.error(f"Failed during dataset discovery: {e}")
            
        finally:
            if 'db' in locals():
                db.close()
                
        return resources
        
    async def certify(self, resource_id: str) -> bool:
        logger.info(f"Certifying dataset {resource_id}")
        try:
            if hasattr(settings, "DATABRICKS_WAREHOUSE_ID") and settings.DATABRICKS_WAREHOUSE_ID:
                query = f"ALTER TABLE {resource_id} SET TAGS ('system.certification_status' = 'certified')"
                res = self.workspace_client.statement_execution.execute_statement(
                    statement=query,
                    warehouse_id=settings.DATABRICKS_WAREHOUSE_ID,
                    wait_timeout="30s"
                )
                if res.status.state.value in ("FAILED", "CANCELED", "CLOSED"):
                    error_msg = res.status.error.message if res.status.error else "Unknown SQL error"
                    logger.error(f"SQL execution failed to certify dataset: {error_msg}")
                    return False
                return True
            else:
                logger.error("No warehouse_id defined, cannot certify dataset via SQL")
                return False
        except Exception as e:
            logger.error(f"Failed to certify dataset {resource_id}: {e}")
            return False

    async def uncertify(self, resource_id: str) -> bool:
        logger.info(f"Un-certifying dataset {resource_id}")
        try:
            if hasattr(settings, "DATABRICKS_WAREHOUSE_ID") and settings.DATABRICKS_WAREHOUSE_ID:
                query = f"ALTER TABLE {resource_id} UNSET TAGS ('system.certification_status')"
                res = self.workspace_client.statement_execution.execute_statement(
                    statement=query,
                    warehouse_id=settings.DATABRICKS_WAREHOUSE_ID,
                    wait_timeout="30s"
                )
                if res.status.state.value in ("FAILED", "CANCELED", "CLOSED"):
                    error_msg = res.status.error.message if res.status.error else "Unknown SQL error"
                    logger.error(f"SQL execution failed to uncertify dataset: {error_msg}")
                    return False
                return True
            else:
                logger.error("No warehouse_id defined, cannot uncertify dataset via SQL")
                return False
        except Exception as e:
            logger.error(f"Failed to un-certify dataset {resource_id}: {e}")
            return False

    async def kill(self, resource_id: str) -> bool:
        return await self.uncertify(resource_id)

    async def warn(self, resource_id: str, message: str) -> bool:
        logger.info(f"Warning owner of dataset {resource_id}: {message}")
        return True
