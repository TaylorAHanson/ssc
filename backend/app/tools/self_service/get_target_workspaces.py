"""
Tool to list available target workspaces for operations.
"""
from typing import Dict, Any
from pydantic import BaseModel
from app.tools.mcp import tool
from app.core.workspaces import get_target_workspaces as fetch_workspaces

class GetTargetWorkspacesInput(BaseModel):
    pass

@tool(
    name="get_target_workspaces",
    description="Lists the authoritative target Databricks workspaces available for operations (like creating catalogs, schemas, volumes, etc.). Use this to find the correct 'target_host' URL when a workflow requires it.",
    args_schema=GetTargetWorkspacesInput
)
def get_target_workspaces() -> Dict[str, Any]:
    """
    Fetch the list of configured target workspaces.
    """
    workspaces = fetch_workspaces()
    
    workspace_list = []
    for ws in workspaces:
        workspace_list.append({
            "name": ws.name,
            "host": ws.host,
            "environment": ws.environment
        })
        
    return {
        "count": len(workspace_list),
        "workspaces": workspace_list
    }
