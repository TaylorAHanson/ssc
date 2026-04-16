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
            # Temporary concept: Read OCDS yaml files
            ocds_dir = os.path.join(os.getcwd(), "ocds")
            if not os.path.exists(ocds_dir):
                logger.warning(f"OCDS directory not found at {ocds_dir}")
                return []
                
            yaml_files = glob.glob(os.path.join(ocds_dir, "*.yaml")) + glob.glob(os.path.join(ocds_dir, "*.yml"))
            
            for file_path in yaml_files:
                try:
                    with open(file_path, "r") as f:
                        dataset_def = yaml.safe_load(f)
                        
                    catalog = dataset_def.get("catalog", "main")
                    schema = dataset_def.get("schema", "default")
                    table_name = dataset_def.get("table", "unknown")
                    full_name = f"{catalog}.{schema}.{table_name}"
                    
                    # Base properties from OCDS definition
                    resource = {
                        "id": dataset_def.get("dataset_id", full_name),
                        "type": "table",
                        "certification_eligible": dataset_def.get("certification_eligible", False),
                        "tdq_threshold": dataset_def.get("tdq_threshold", 100),
                        "bdq_threshold": dataset_def.get("bdq_threshold", 100),
                        "abac_needed": dataset_def.get("abac_needed", False),
                        "abac_defined": dataset_def.get("abac_defined", False),
                        "data_classification": dataset_def.get("data_classification", ""),
                        "tags": dataset_def.get("tags", {})
                    }
                    
                    # Fetch TDQ and BDQ scores from Databricks Data Quality results table
                    resource["tdq_score"] = 0
                    resource["bdq_score"] = 0
                    try:
                        if hasattr(settings, "DATABRICKS_WAREHOUSE_ID") and settings.DATABRICKS_WAREHOUSE_ID:
                            # In reality, this would be a real query against the DQ results table.
                            # For the purpose of this demonstration without relying on a real table to exist,
                            # we're using a dummy SELECT statement that returns 95 for TDQ and 100 for BDQ.
                            # Real query would be: f"SELECT tdq_score, bdq_score FROM {settings.DATABRICKS_DATA_QUALITY_TABLE} WHERE dataset_id = '{full_name}' ORDER BY run_date DESC LIMIT 1"
                            query = f"SELECT tdq_score, bdq_score FROM {settings.DATABRICKS_DATA_QUALITY_TABLE} WHERE dataset_id = '{full_name}' ORDER BY run_date DESC LIMIT 1"
                            
                            logger.info(f"Querying TDQ/BDQ scores for {full_name} via SQL execution")
                            response = self.workspace_client.statement_execution.execute_statement(
                                statement=query,
                                warehouse_id=settings.DATABRICKS_WAREHOUSE_ID,
                                wait_timeout="30s"
                            )
                            
                            if response.result and response.result.data_array and len(response.result.data_array) > 0:
                                resource["tdq_score"] = float(response.result.data_array[0][0])
                                resource["bdq_score"] = float(response.result.data_array[0][1])
                        else:
                            logger.warning(f"No warehouse_id defined in settings. Cannot query TDQ/BDQ scores for {full_name}")
                    except Exception as e:
                        logger.error(f"Failed to fetch TDQ/BDQ scores for {full_name} via SQL: {e}")
                    
                    # Fetch actual metadata from Databricks Unity Catalog
                    try:
                        catalog_info = self.workspace_client.catalogs.get(name=catalog)
                        resource["catalog_description"] = catalog_info.comment
                    except Exception as e:
                        logger.debug(f"Failed to get catalog {catalog}: {e}")
                        resource["catalog_description"] = None
                        
                    try:
                        schema_info = self.workspace_client.schemas.get(full_name=f"{catalog}.{schema}")
                        resource["schema_description"] = schema_info.comment
                    except Exception as e:
                        logger.debug(f"Failed to get schema {catalog}.{schema}: {e}")
                        resource["schema_description"] = None
                        
                    try:
                        table_info = self.workspace_client.tables.get(full_name=full_name)
                        columns = table_info.columns or []
                        # Check if all columns have a comment
                        all_have_desc = all(bool(col.comment) for col in columns) if columns else False
                        resource["all_columns_have_descriptions"] = all_have_desc
                    except Exception as e:
                        logger.debug(f"Failed to get table {full_name}: {e}")
                        resource["all_columns_have_descriptions"] = False
                        
                    try:
                        grants = self.workspace_client.grants.get(securable_type="table", full_name=full_name)
                        # If privileges exist, we consider RBAC defined
                        resource["rbac_defined"] = len(grants.privilege_assignments or []) > 0
                    except Exception as e:
                        logger.debug(f"Failed to get grants for table {full_name}: {e}")
                        resource["rbac_defined"] = False
                        
                    try:
                        uc_tags = self.workspace_client.entity_tag_assignments.list(entity_type='table', entity_name=full_name)
                        for tag_assign in uc_tags:
                            if tag_assign.tag_key:
                                # Overwrite or append OCDS tags with Unity Catalog tags
                                resource["tags"][tag_assign.tag_key] = tag_assign.tag_value
                    except Exception as e:
                        logger.debug(f"Failed to get tags for table {full_name}: {e}")
                        
                    resources.append(resource)
                    
                except Exception as e:
                    logger.error(f"Failed to parse OCDS file {file_path}: {e}")
                    
        except Exception as e:
            logger.error(f"Failed during dataset discovery: {e}")
            
        return resources
        
    async def certify(self, resource_id: str) -> bool:
        """Apply the system.certification_status = 'certified' tag"""
        logger.info(f"Certifying dataset {resource_id}")
        try:
            from databricks.sdk.service.catalog import EntityTagAssignment
            self.workspace_client.entity_tag_assignments.update(
                entity_type='table',
                entity_name=resource_id,
                tag_key='system.certification_status',
                tag_assignment=EntityTagAssignment(
                    entity_name=resource_id,
                    entity_type='table',
                    tag_key='system.certification_status',
                    tag_value='certified'
                ),
                update_mask='tag_value'
            )
            return True
        except Exception as e:
            logger.error(f"Failed to certify dataset {resource_id}: {e}")
            return False

    async def uncertify(self, resource_id: str) -> bool:
        """Remove or deprecate the certification status"""
        logger.info(f"Un-certifying dataset {resource_id}")
        try:
            # We can either delete the tag assignment or set it to 'deprecated'.
            # Deleting the tag assignment entirely is usually what 'uncertify' means.
            self.workspace_client.entity_tag_assignments.delete(
                entity_type='table',
                entity_name=resource_id,
                tag_key='system.certification_status'
            )
            return True
        except Exception as e:
            logger.error(f"Failed to un-certify dataset {resource_id}: {e}")
            return False

    async def kill(self, resource_id: str) -> bool:
        # Fallback if state machine still calls kill
        return await self.uncertify(resource_id)

    async def warn(self, resource_id: str, message: str) -> bool:
        logger.info(f"Warning owner of dataset {resource_id}: {message}")
        return True
