"""
Tool to list storage credentials.
"""
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field
from app.tools.mcp import tool
from app.core.exceptions import RetryableError
import fnmatch

class GetCredentialListInput(BaseModel):
    target_host: str = Field(..., description="Workspace host for context only. Unity Catalog is account-global, so storage credentials are always read from the local workspace regardless of this value.")
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
        # Unity Catalog is metastore-global (account-level), so always read it
        # from the LOCAL/home workspace — never the target host, which may be
        # network-unreachable / fail cert validation from here. target_host is
        # accepted for context but intentionally not used to pick the connection.
        from app.core.workspaces import get_uc_provider
        provider = get_uc_provider()
        
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
