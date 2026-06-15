"""
Tool to check tagging compliance.
"""
from typing import Dict, Any, List
from pydantic import BaseModel, Field
from app.tools.mcp import tool
from app.tools.sql_safety import quote_literal
from app.providers.databricks import DatabricksProvider
from app.core.config import settings
from app.core.exceptions import RetryableError

class CheckTaggingInput(BaseModel):
    required_tags: List[str] = Field(..., description="List of tag keys that must be present (e.g., ['CostCenter', 'Project'])")

@tool(
    name="check_tagging_compliance",
    description="Identifies resources (clusters, warehouses) that are missing required tags defined by policy.",
    required_role="finance_admin",
    args_schema=CheckTaggingInput
)
async def check_tagging_compliance(required_tags: List[str], **kwargs) -> Dict[str, Any]:
    try:
        # Read-only system-table query runs as the calling user (OBO) when
        # available; falls back to the service principal otherwise.
        obo_token = kwargs.get("_obo_token")
        provider = DatabricksProvider(
            host=settings.DATABRICKS_HOST or settings.DATABRICKS_WORKSPACE_URL,
            token=settings.DATABRICKS_TOKEN,
            client_id=settings.DATABRICKS_CLIENT_ID,
            client_secret=settings.DATABRICKS_CLIENT_SECRET,
            config={"warehouse_id": settings.DATABRICKS_WAREHOUSE_ID}
        )
        
        # We can check system.compute.clusters for tags
        # The custom_tags column is a Map(String, String) or similar structure.
        # In SQL, we can check if keys exist.
        
        # Construct a WHERE clause that checks if ANY required tag is missing
        # Logic: IF NOT (map_contains_key(custom_tags, 'Tag1') AND map_contains_key(custom_tags, 'Tag2'))
        
        tag_checks = []
        for tag in required_tags:
            # Based on user documentation, 'tags' is the map column.
            tag_checks.append(f"NOT map_contains_key(tags, {quote_literal(tag)})")
        
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
        
        result = await provider.execute_sql(query, obo_token=obo_token, require_obo=True)
        
        return {
            "non_compliant_resources": result.get("rows", []),
            "checked_tags": required_tags,
            "note": "Currently checking active Clusters."
        }
            
    except Exception as e:
        raise RetryableError(f"Failed to check tagging compliance: {str(e)}")
