from typing import Dict, Any, Optional, List
from app.tools.base import BaseTool
from app.providers.databricks import DatabricksProvider
from app.core.config import settings

class ListWorkspacesTool(BaseTool):
    """Tool to list all workspaces in the account using system tables."""
    
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
        return "list_workspaces"

    @property
    def description(self) -> str:
        return "Lists all workspaces in the Databricks account. Returns workspace IDs, names, and regions. Useful for determining which workspaces are available in the environment."

    @property
    def required_role(self) -> Optional[str]:
        # No role limit as requested
        return None

    @property
    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["RUNNING", "PROVISIONING", "FAILED", "BANNED", "NOT_PROVISIONED"],
                    "description": "Optional filter to list workspaces only with a specific status."
                }
            }
        }

    async def execute(self, status: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        try:
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
            
            result = await self.provider.execute_sql(query, timeout_seconds=120)
            
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
