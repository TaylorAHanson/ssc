"""
Tool to list available target workspaces for operations.
"""
from typing import Dict, Any
from pydantic import BaseModel
from app.tools.mcp import tool
from app.core.workspaces import get_target_workspace_info as fetch_workspaces

class GetTargetWorkspacesInput(BaseModel):
    pass

@tool(
    name="get_target_workspaces",
    description=(
        "List configured target Databricks workspace host URLs for cross-workspace operations. "
        "Instant (reads app settings, with workspace_id/status when known)."
    ),
    args_schema=GetTargetWorkspacesInput
)
def get_target_workspaces() -> Dict[str, Any]:
    """
    Fetch the list of configured target workspaces (no credential resolution).
    """
    workspaces = fetch_workspaces()
    
    workspace_list = []
    for ws in workspaces:
        entry = {
            "name": ws.name,
            "host": ws.host,
            "environment": ws.environment
        }
        if ws.workspace_id:
            entry["workspace_id"] = ws.workspace_id
        if ws.status:
            entry["status"] = ws.status
        workspace_list.append(entry)
        
    return {
        "count": len(workspace_list),
        "workspaces": workspace_list
    }
