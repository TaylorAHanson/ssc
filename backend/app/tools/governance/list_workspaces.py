"""
Tool to list workspaces.
"""
from typing import Dict, Any, Optional, Literal
from pydantic import BaseModel, Field
from app.tools.mcp import tool
from app.providers.databricks import DatabricksProvider
from app.core.config import settings

class ListWorkspacesInput(BaseModel):
    status: Optional[Literal["RUNNING", "PROVISIONING", "FAILED", "BANNED", "NOT_PROVISIONED"]] = Field(None, description="Optional filter to list workspaces only with a specific status.")

@tool(
    name="list_workspaces",
    description="Lists all workspaces in the Databricks account. Returns workspace IDs, names, and regions. Useful for determining which workspaces are available in the environment.",
    args_schema=ListWorkspacesInput
)
async def list_workspaces(status: Optional[str] = None, **kwargs) -> Dict[str, Any]:
    try:
        provider = DatabricksProvider(
            host=settings.DATABRICKS_HOST or settings.DATABRICKS_WORKSPACE_URL,
            token=settings.DATABRICKS_TOKEN,
            client_id=settings.DATABRICKS_CLIENT_ID,
            client_secret=settings.DATABRICKS_CLIENT_SECRET,
            config={"warehouse_id": settings.DATABRICKS_WAREHOUSE_ID}
        )

        where_clause = ""
        if status:
            where_clause = f"WHERE status = '{status}'"
        
        query = f"""
            SELECT 
                account_id,
                workspace_id,
                workspace_name,
                workspace_url,
                create_time,
                status
            FROM system.access.workspaces_latest
            {where_clause}
            ORDER BY workspace_name ASC
        """
        
        # Run as the user (OBO) so their own access to system.access.* applies —
        # the app's service principal typically can't read system tables.
        obo_token = kwargs.get("_obo_token")
        result = await provider.execute_sql(query, timeout_seconds=120, obo_token=obo_token)
        
        return {
            "workspaces": result.get("rows", []),
            "count": len(result.get("rows", [])),
            "query": query
        }
    except Exception as e:
        return {
            "error": f"Failed to list workspaces: {str(e)}",
            "workspaces": []
        }
