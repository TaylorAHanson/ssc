"""
Tool to audit user access.
"""
from typing import Dict, Any
from pydantic import BaseModel, Field
from app.tools.mcp import tool
from app.providers.databricks import DatabricksProvider
from app.core.config import settings
from app.core.exceptions import RetryableError

class AuditUserAccessInput(BaseModel):
    user_email: str = Field(..., description="Email of the user to audit")
    catalog: str = Field(..., description="The specific catalog to audit permissions within")

@tool(
    name="audit_user_access",
    description="Lists all Unity Catalog permissions granted to a specific user (direct and via groups).",
    required_role="governance_admin",
    args_schema=AuditUserAccessInput
)
async def audit_user_access(user_email: str, catalog: str, **kwargs) -> Dict[str, Any]:
    try:
        # Read-only access audit runs as the calling user (OBO) when available;
        # falls back to the service principal otherwise.
        obo_token = kwargs.get("_obo_token")
        provider = DatabricksProvider(
            host=settings.DATABRICKS_HOST or settings.DATABRICKS_WORKSPACE_URL,
            token=settings.DATABRICKS_TOKEN,
            client_id=settings.DATABRICKS_CLIENT_ID,
            client_secret=settings.DATABRICKS_CLIENT_SECRET,
            config={"warehouse_id": settings.DATABRICKS_WAREHOUSE_ID}
        )
        
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
                res = await provider.execute_sql(q, timeout_seconds=120, obo_token=obo_token)
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
