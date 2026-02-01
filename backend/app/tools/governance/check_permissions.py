from typing import Dict, Any, Optional
from app.tools.base import BaseTool
from app.providers.databricks import DatabricksProvider
from app.core.config import settings
from app.core.exceptions import RetryableError

class CheckObjectPermissionsTool(BaseTool):
    """Tool to check permissions (grants) on a specific Unity Catalog object."""
    
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
        return "check_object_permissions"

    @property
    def description(self) -> str:
        return "Lists all grants and permissions on a specific Unity Catalog object (catalog, schema, table, etc.)."

    @property
    def required_role(self) -> Optional[str]:
        return "governance_admin"

    @property
    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "object_type": {
                    "type": "string",
                    "enum": ["CATALOG", "SCHEMA", "TABLE", "VOLUME", "FUNCTION", "MODEL"],
                    "description": "Type of the object (e.g., CATALOG, TABLE)"
                },
                "object_name": {
                    "type": "string",
                    "description": "Full name of the object (e.g., main.default.mytable)"
                }
            },
            "required": ["object_type", "object_name"]
        }

    async def execute(self, object_type: str, object_name: str, **kwargs) -> Dict[str, Any]:
        try:
            # UC System tables: system.information_schema.table_privileges etc.
            # But SHOW GRANTS is often simpler and supported via SQL execution.
            # However, for agentic use, querying system tables is often more robust if we have access.
            # privileges tables: system.information_schema.catalog_privileges, schema_privileges, table_privileges...
            
            # Let's try to use SQL `SHOW GRANTS` first as it's universal for UC objects mostly.
            # Syntax: SHOW GRANTS ON [TYPE] [NAME]
            
            query = f"SHOW GRANTS ON {object_type} {object_name}"
            
            result = await self.provider.execute_sql(query, timeout_seconds=300)
            
            # Also get owner
            # DESCRIBE [TYPE] [NAME] usually shows owner? Or use system.information_schema
            owner = "Unknown" 
            try:
                # Need specific query per type to find owner easily via SQL without parsing text output of DESCRIBE
                # Using system.information_schema might be better if we have access.
                pass
            except:
                pass

            return {
                "object_type": object_type,
                "object_name": object_name,
                "grants": result.get("rows", []),
                "note": "Output from SHOW GRANTS"
            }
                
        except Exception as e:
            raise RetryableError(f"Failed to check permissions: {str(e)}")
