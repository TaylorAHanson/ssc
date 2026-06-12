"""
Tool to check if a service principal exists.
"""
from typing import Dict, Any
from pydantic import BaseModel, Field
from app.tools.mcp import tool
from app.providers.databricks import DatabricksProvider
from app.core.config import settings
from app.core.exceptions import RetryableError

class DoesServicePrincipalExistInput(BaseModel):
    target_host: str = Field(..., description="The host URL of the target Databricks workspace.")
    name: str = Field(..., description="The display name of the service principal to check.")

@tool(
    name="does_service_principal_exist",
    description="Checks if a service principal exists in the specified Databricks account/workspace by its display name. Useful for avoiding duplicate creation requests.",
    args_schema=DoesServicePrincipalExistInput
)
def does_service_principal_exist(target_host: str, name: str) -> Dict[str, Any]:
    """
    Check if a service principal exists.
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
        
        # Use workspace client to search for service principals
        # Using get_workspace_client() which returns the databricks WorkspaceClient
        client = provider.get_workspace_client()
        
        # The filter syntax for SCIM API: displayName eq "something"
        # We can also just iterate if filter is not fully supported, but filter is better
        try:
            sp_list = client.service_principals.list(filter=f"displayName eq '{name}'")
            # Convert iterator to list safely
            sps = list(sp_list)
            exists = len(sps) > 0
            
            return {
                "exists": exists,
                "name": name,
                "details": sps[0].as_dict() if exists else None
            }
        except Exception as e:
            # Fallback to fetching all and filtering in memory if API filter fails
            sp_list = client.service_principals.list()
            for sp in sp_list:
                if sp.display_name == name:
                    return {
                        "exists": True,
                        "name": name,
                        "details": sp.as_dict()
                    }
            
            return {
                "exists": False,
                "name": name,
                "details": None
            }
            
    except RetryableError:
        raise
    except Exception as e:
        raise RetryableError(f"Failed to check service principal existence: {str(e)}")
