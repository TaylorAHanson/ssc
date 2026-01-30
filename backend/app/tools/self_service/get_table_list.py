from typing import Dict, Any, List
from app.tools.base import BaseTool
from app.providers.databricks import DatabricksProvider
from app.core.config import settings
from app.core.exceptions import RetryableError

class GetTableListTool(BaseTool):
    """Tool to list tables within a specific Unity Catalog schema."""
    
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
        return "get_table_list"

    @property
    def description(self) -> str:
        return "Lists all tables within a specified catalog and schema in Unity Catalog, including their descriptions/comments. Use this to help users discover specific datasets. NEXT STEP: If the user needs access, proceed to the 'Request Data Access' workflow."

    @property
    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "catalog_name": {
                    "type": "string",
                    "description": "Name of the parent catalog"
                },
                "schema_name": {
                    "type": "string",
                    "description": "Name of the schema to list tables for"
                }
            },
            "required": ["catalog_name", "schema_name"]
        }

    async def execute(self, catalog_name: str, schema_name: str) -> Dict[str, Any]:
        """
        Fetch the list of tables for a schema along with their descriptions.
        """
        try:
            tables = self.provider.client.tables.list(catalog_name=catalog_name, schema_name=schema_name)
            
            table_list = []
            for table in tables:
                table_list.append({
                    "name": table.name,
                    "catalog_name": table.catalog_name,
                    "schema_name": table.schema_name,
                    "table_type": table.table_type.value if hasattr(table.table_type, 'value') else str(table.table_type),
                    "comment": table.comment or "No description provided",
                    "owner": table.owner
                })
            
            return {
                "catalog_name": catalog_name,
                "schema_name": schema_name,
                "count": len(table_list),
                "tables": table_list
            }
            
        except Exception as e:
            raise RetryableError(f"Failed to fetch table list for {catalog_name}.{schema_name}: {str(e)}")
