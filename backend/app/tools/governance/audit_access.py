from typing import Dict, Any, Optional
from app.tools.base import BaseTool
from app.providers.databricks import DatabricksProvider
from app.core.config import settings
from app.core.exceptions import RetryableError

class AuditUserAccessTool(BaseTool):
    """Tool to audit all effective permissions for a specific user."""
    
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
        return "audit_user_access"

    @property
    def description(self) -> str:
        return "Lists all Unity Catalog permissions granted to a specific user (direct and via groups)."

    @property
    def required_role(self) -> Optional[str]:
        return "governance_admin"

    @property
    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "user_email": {
                    "type": "string",
                    "description": "Email of the user to audit"
                },
                "catalog": {
                    "type": "string",
                    "description": "The specific catalog to audit permissions within"
                }
            },
            "required": ["user_email", "catalog"]
        }

    async def execute(self, user_email: str, catalog: str) -> Dict[str, Any]:
        try:
            # We need to query system.information_schema tables for grants to this user OR groups they belong to.
            # Strategy: Query a few key views in `system.information_schema`.
            # Filtering by grantee = user_email AND the specific catalog.
            
            queries = {
                "catalog": f"SELECT * FROM system.information_schema.catalog_privileges WHERE grantee = '{user_email}' AND catalog_name = '{catalog}'",
                "schemas": f"SELECT * FROM system.information_schema.schema_privileges WHERE grantee = '{user_email}' AND catalog_name = '{catalog}'",
                "tables": f"SELECT * FROM system.information_schema.table_privileges WHERE grantee = '{user_email}' AND table_catalog = '{catalog}'"
            }
            
            final_results = {}
            for key, q in queries.items():
                try:
                    res = await self.provider.execute_sql(q, timeout_seconds=120)
                    final_results[key] = res.get("rows", [])
                except Exception as e:
                    final_results[key] = f"Error: {str(e)}"

            return {
                "user_email": user_email,
                "catalog": catalog,
                "direct_grants": final_results,
                "note": "Shows DIRECT grants only within the specified catalog."
            }
                
        except Exception as e:
            raise RetryableError(f"Failed to audit user access: {str(e)}")
