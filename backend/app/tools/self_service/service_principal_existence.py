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
    name: str = Field(..., description="The display name of the service principal to check.")

@tool(
    name="does_service_principal_exist",
    description="Checks if a service principal exists in the Databricks account/workspace by its display name. Useful for avoiding duplicate creation requests.",
    args_schema=DoesServicePrincipalExistInput
)
async def does_service_principal_exist(name: str) -> Dict[str, Any]:
    """
    Check if a service principal exists.
    """
    try:
        provider = DatabricksProvider(
            host=settings.DATABRICKS_HOST or settings.DATABRICKS_WORKSPACE_URL,
            token=settings.DATABRICKS_TOKEN,
            client_id=settings.DATABRICKS_CLIENT_ID,
            client_secret=settings.DATABRICKS_CLIENT_SECRET,
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
