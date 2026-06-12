"""
Tool to list storage credentials.
"""
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field
from app.tools.mcp import tool
from app.providers.databricks import DatabricksProvider
from app.core.config import settings
from app.core.exceptions import RetryableError
import fnmatch

class GetCredentialListInput(BaseModel):
    target_host: str = Field(..., description="The host URL of the target Databricks workspace.")
    name_pattern: Optional[str] = Field(None, description="Optional. Exact name or glob pattern (e.g. '*dev*') to filter for a specific storage credential.")

@tool(
    name="get_credential_list",
    description="Lists all storage credentials in Unity Catalog for a specific workspace. You can optionally filter by a specific name or pattern to check if a credential exists.",
    args_schema=GetCredentialListInput
)
def get_credential_list(target_host: str, name_pattern: Optional[str] = None) -> Dict[str, Any]:
    """
    Fetch the list of storage credentials.
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
        
        credentials = provider.client.storage_credentials.list()
        
        credential_list = []
        for cred in credentials:
            if name_pattern and not fnmatch.fnmatch(cred.name.lower(), name_pattern.lower()):
                continue
                
            credential_list.append({
                "name": cred.name,
                "comment": cred.comment or "No description provided",
                "owner": cred.owner,
                "read_only": cred.read_only
            })
        
        return {
            "count": len(credential_list),
            "credentials": credential_list
        }
        
    except Exception as e:
        raise RetryableError(f"Failed to fetch storage credential list: {str(e)}")
