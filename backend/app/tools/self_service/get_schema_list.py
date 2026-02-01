from typing import Dict, Any, List
from app.tools.base import BaseTool
from app.providers.databricks import DatabricksProvider
from app.core.config import settings
from app.core.exceptions import RetryableError

class GetSchemaListTool(BaseTool):
    """Tool to list schemas within a specific Unity Catalog catalog."""
    
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
        return "get_schema_list"

    @property
    def description(self) -> str:
        return "Lists all schemas within a specified catalog in Unity Catalog, including their descriptions/comments. Use this to explore the structure of a catalog. NEXT STEP: Use 'get_table_list' to find datasets within a schema."

    @property
    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "catalog_name": {
                    "type": "string",
                    "description": "Name of the catalog to list schemas for"
                }
            },
            "required": ["catalog_name"]
        }

    async def execute(self, catalog_name: str, **kwargs) -> Dict[str, Any]:
        """
        Fetch the list of schemas for a catalog along with their descriptions.
        """
        try:
            schemas = self.provider.client.schemas.list(catalog_name=catalog_name)
            
            schema_list = []
            for schema in schemas:
                schema_list.append({
                    "name": schema.name,
                    "catalog_name": schema.catalog_name,
                    "comment": schema.comment or "No description provided",
                    "owner": schema.owner,
                    "properties": schema.properties or {}
                })
            
            return {
                "catalog_name": catalog_name,
                "count": len(schema_list),
                "schemas": schema_list
            }
            
        except Exception as e:
            raise RetryableError(f"Failed to fetch schema list for catalog '{catalog_name}': {str(e)}")
