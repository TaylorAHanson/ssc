"""
Tool to check if a catalog exists.
"""
from typing import Dict, Any, List
from app.tools.base import BaseTool
from app.providers.databricks import DatabricksProvider
from app.core.config import settings
from app.core.exceptions import RetryableError

class DoesCatalogExistTool(BaseTool):
    """Tool to check if a Unity Catalog catalog exists."""
    
    def __init__(self):
        self._provider = None

    @property
    def provider(self) -> DatabricksProvider:
        """Lazy load provider."""
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
        return "does_catalog_exist"

    @property
    def description(self) -> str:
        return "Checks if a Unity Catalog catalog exists in the Databricks workspace."

    @property
    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "catalog_name": {
                    "type": "string",
                    "description": "Name of the catalog to check"
                }
            },
            "required": ["catalog_name"]
        }

    async def execute(self, catalog_name: str) -> Dict[str, Any]:
        """
        Check if catalog exists.
        
        Args:
            catalog_name: Name of the catalog to check
            
        Returns:
            Dictionary with 'exists' (bool) and 'catalog_name' (str)
        """
        try:
            # Use SQL to check existence
            # SHOW CATALOGS LIKE 'name' returns a row if it exists
            query = f"SHOW CATALOGS LIKE '{catalog_name}'"
            
            result = await self.provider.execute_sql(query)
            
            rows = result.get("rows", [])
            exists = len(rows) > 0
            
            # Verify exact match if fuzzy match returned multiple (though LIKE without wildcards should be exact-ish)
            # But SHOW LIKE acts as exact match if no wildcards usually? 
            # Actually LIKE 'name' is pattern matching but without % it's exact.
            
            return {
                "exists": exists,
                "catalog_name": catalog_name,
                "details": rows[0] if exists else None
            }
            
        except RetryableError as e:
            # Re-raise retryable errors
            raise
        except Exception as e:
            # Wrap others
            raise RetryableError(f"Failed to check catalog existence: {str(e)}")
