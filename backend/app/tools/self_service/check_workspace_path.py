"""
Tool to check if a workspace path exists.
"""
from typing import Dict, Any
from pydantic import BaseModel, Field
from app.tools.mcp import tool
from app.providers.databricks import DatabricksProvider
from app.core.config import settings
from app.core.exceptions import RetryableError

class CheckWorkspacePathInput(BaseModel):
    target_host: str = Field(..., description="The host URL of the target Databricks workspace.")
    path: str = Field(..., description="The absolute workspace path to check (e.g., '/Shared/Projects')")

@tool(
    name="check_workspace_path",
    description="Checks if a specific path exists in the Databricks Workspace file system for a specific workspace.",
    args_schema=CheckWorkspacePathInput
)
async def check_workspace_path(target_host: str, path: str) -> Dict[str, Any]:
    """
    Check if a workspace path exists.
    """
    try:
        from app.core.workspaces import get_workspace_config
        ws_config = get_workspace_config(target_host)
        if not ws_config:
            raise ValueError(f"Target host {target_host} not found in configuration.")
            
        provider = DatabricksProvider(
            host=ws_config.host,
            token=ws_config.token,
            client_id=ws_config.client_id,
            client_secret=ws_config.client_secret,
            config={"warehouse_id": settings.DATABRICKS_WAREHOUSE_ID}
        )
        
        try:
            status = provider.client.workspace.get_status(path)
            return {
                "exists": True,
                "path": path,
                "object_type": status.object_type.value if hasattr(status.object_type, 'value') else str(status.object_type),
                "object_id": status.object_id
            }
        except Exception as e:
            if "RESOURCE_DOES_NOT_EXIST" in str(e) or "404" in str(e):
                return {
                    "exists": False,
                    "path": path,
                    "message": "Path does not exist."
                }
            raise e
            
    except Exception as e:
        raise RetryableError(f"Failed to check workspace path '{path}': {str(e)}")
