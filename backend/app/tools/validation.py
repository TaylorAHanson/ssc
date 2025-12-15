"""
Validation tools.
"""
from typing import Dict, Any, Optional
from app.tools.base import BaseTool
from app.providers.databricks import DatabricksProvider
from app.core.config import settings


class CheckExistsTool(BaseTool):
    """Check if a resource exists."""
    
    def __init__(self):
        self.databricks = DatabricksProvider(
            host=settings.DATABRICKS_HOST,
            token=settings.DATABRICKS_TOKEN
        )
    
    async def execute(
        self,
        resource_type: str,
        resource_name: str,
        parent_catalog: Optional[str] = None,
        parent_schema: Optional[str] = None,
        fuzzy_match: bool = True
    ) -> Dict[str, Any]:
        """Check if resource exists."""
        # Build SQL query based on resource type
        query = self._build_query(resource_type, resource_name, parent_catalog, parent_schema)
        
        # Use Databricks provider to execute SQL
        result = await self.databricks.execute_sql(query)
        
        # Process and return
        return {
            "exists": len(result.get("rows", [])) > 0,
            "exact_match": True,  # TODO: Implement exact match logic
            "similar_names": [] if not fuzzy_match else []  # TODO: Implement fuzzy matching
        }
    
    def _build_query(self, resource_type: str, resource_name: str, parent_catalog: Optional[str], parent_schema: Optional[str]) -> str:
        """Build SQL query to check resource existence."""
        if resource_type == "catalog":
            return f"SHOW CATALOGS LIKE '{resource_name}'"
        elif resource_type == "schema":
            if parent_catalog:
                return f"SHOW SCHEMAS IN {parent_catalog} LIKE '{resource_name}'"
        elif resource_type == "table":
            if parent_catalog and parent_schema:
                return f"SHOW TABLES IN {parent_catalog}.{parent_schema} LIKE '{resource_name}'"
        return ""

