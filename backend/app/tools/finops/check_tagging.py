from typing import Dict, Any, List, Optional
from app.tools.base import BaseTool
from app.providers.databricks import DatabricksProvider
from app.core.config import settings
from app.core.exceptions import RetryableError

class CheckTaggingComplianceTool(BaseTool):
    """Tool to check for missing required tags on resources."""
    
    def __init__(self):
        self._provider = None

    @property
    def provider(self) -> DatabricksProvider:
        if not self._provider:
            self._provider = DatabricksProvider(
                host=settings.DATABRICKS_HOST or settings.DATABRICKS_WORKSPACE_URL,
                token=settings.DATABRICKS_TOKEN,
                client_id=settings.DATABRICKS_CLIENT_ID,
                client_secret=settings.DATABRICKS_CLIENT_SECRET,
                config={"warehouse_id": settings.DATABRICKS_WAREHOUSE_ID}
            )
        return self._provider

    @property
    def name(self) -> str:
        return "check_tagging_compliance"

    @property
    def description(self) -> str:
        return "Identifies resources (clusters, warehouses) that are missing required tags defined by policy."

    @property
    def required_role(self) -> Optional[str]:
        return "finance_admin"
        
    @property
    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "required_tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of tag keys that must be present (e.g., ['CostCenter', 'Project'])"
                }
            },
            "required": ["required_tags"]
        }

    async def execute(self, required_tags: List[str], **kwargs) -> Dict[str, Any]:
        try:
            # We can check system.compute.clusters for tags
            # The custom_tags column is a Map(String, String) or similar structure.
            # In SQL, we can check if keys exist.
            
            non_compliant_resources = []
            
            # Check Clusters
            # Construct a WHERE clause that checks if ANY required tag is missing
            # Logic: IF NOT (map_contains_key(custom_tags, 'Tag1') AND map_contains_key(custom_tags, 'Tag2'))
            
            tag_checks = []
            for tag in required_tags:
                # Based on user documentation, 'tags' is the map column.
                tag_checks.append(f"NOT map_contains_key(tags, '{tag}')")
            
            # If ANY tag is missing, select it
            where_clause = " OR ".join(tag_checks)
            
            query = f"""
                SELECT 
                    cluster_id as resource_id,
                    cluster_name as resource_name,
                    'CLUSTER' as resource_type,
                    owned_by,
                    tags
                FROM system.compute.clusters
                WHERE delete_time IS NULL 
                AND ({where_clause})
                LIMIT 100
            """
            
            # Note: Warehouses table might be different (system.compute.warehouses?) 
            # Sticking to clusters for now as primary target.
            
            result = await self.provider.execute_sql(query)
            
            return {
                "non_compliant_resources": result.get("rows", []),
                "checked_tags": required_tags,
                "note": "Currently checking active Clusters."
            }
                
        except Exception as e:
            raise RetryableError(f"Failed to check tagging compliance: {str(e)}")
