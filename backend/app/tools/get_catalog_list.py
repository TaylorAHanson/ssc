from typing import Dict, Any, List
from app.tools.base import BaseTool
from app.providers.databricks import DatabricksProvider
from app.core.config import settings
from app.core.exceptions import RetryableError

class GetCatalogListTool(BaseTool):
    """Tool to list catalogs in the Databricks Unity Catalog."""
    
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
        return "get_catalog_list"

    @property
    def description(self) -> str:
        return "Lists all available catalogs in the Databricks Unity Catalog, including their descriptions and comments. Use this to discover available catalogs or to find similar catalog names if a specific one is not found."

    @property
    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {},
            "required": []
        }

    async def execute(self) -> Dict[str, Any]:
        """
        Fetch the list of catalogs along with their descriptions.
        """
        try:
            catalogs = self.provider.client.catalogs.list()
            
            catalog_list = []
            for catalog in catalogs:
                catalog_list.append({
                    "name": catalog.name,
                    "comment": catalog.comment or "No description provided",
                    "catalog_type": catalog.catalog_type.value if hasattr(catalog.catalog_type, 'value') else str(catalog.catalog_type),
                    "owner": catalog.owner
                })
            
            return {
                "count": len(catalog_list),
                "catalogs": catalog_list
            }
            
        except Exception as e:
            raise RetryableError(f"Failed to fetch catalog list: {str(e)}")
