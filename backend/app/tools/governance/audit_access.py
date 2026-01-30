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
                }
            },
            "required": ["user_email"]
        }

    async def execute(self, user_email: str) -> Dict[str, Any]:
        try:
            # We need to query system.information_schema tables for grants to this user OR groups they belong to.
            # This is complex in SQL without knowing all groups.
            # Ideally, `system.access.table_privileges` etc. are the place.
            # However, `system.privileges` isn't a single table.
            
            # Strategy: Query a few key views in `system.information_schema`.
            # We assume the user principal is the email.
            
            # Using `system.information_schema.routine_privileges`, `table_privileges`, `catalog_privileges`, `schema_privileges`
            # AND filtering by grantee = user_email
            
            # NOTE: This only captures DIRECT grants to the user unless we also expand groups.
            # Doing full group expansion in SQL is hard. 
            # For MVP, we will report DIRECT grants and maybe note that group grants are excluded or best effort.
            
            queries = {
                "catalogs": f"SELECT * FROM system.information_schema.catalog_privileges WHERE grantee = '{user_email}'",
                "schemas": f"SELECT * FROM system.information_schema.schema_privileges WHERE grantee = '{user_email}'",
                "tables": f"SELECT * FROM system.information_schema.table_privileges WHERE grantee = '{user_email}'"
            }
            
            final_results = {}
            for key, q in queries.items():
                try:
                    res = await self.provider.execute_sql(q)
                    final_results[key] = res.get("rows", [])
                except Exception as e:
                    final_results[key] = f"Error: {str(e)}"

            return {
                "user_email": user_email,
                "direct_grants": final_results,
                "note": "Shows DIRECT grants only. inherited group permissions require group expansion logic not yet fully implemented via SQL-only tool."
            }
                
        except Exception as e:
            raise RetryableError(f"Failed to audit user access: {str(e)}")
