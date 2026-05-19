from typing import Dict, Any, Optional, List
from pydantic import BaseModel, Field
from app.tools.mcp import tool
from app.providers.terraform.volume_provider import VolumeGitOpsProvider
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)


class CheckResourceAccessInput(BaseModel):
    target_host: str = Field(..., description="The host URL of the target Databricks workspace.")
    resource_name: str = Field(..., description="Name of the resource (schema or catalog)")
    principal: Optional[str] = Field(None, description="Optional: specific user, group, or service principal to check")


@tool(
    name="check_resource_access",
    description="Check existing access grants on a Unity Catalog resource (schema or catalog) in a specific workspace. Use this BEFORE granting or revoking access to verify current state.",
    args_schema=CheckResourceAccessInput
)
async def check_resource_access(target_host: str, resource_name: str, principal: Optional[str] = None, **kwargs) -> Dict[str, Any]:
    """
    Check what access grants exist on a resource.
    
    Returns:
        - exists: Whether the resource exists in our system
        - grants: List of all grants on the resource
        - principal_grants: Privileges for the specific principal (if provided)
    """
    try:
        volume_path = settings.GITOPS_VOLUME_PATH
        if not volume_path:
            return {
                "success": False,
                "error": "GITOPS_VOLUME_PATH not configured"
            }
        
        provider = VolumeGitOpsProvider(
            volume_path=volume_path,
            config={
                "environment": settings.DEFAULT_ENVIRONMENT or "dev",
                "git_username": settings.GIT_USERNAME,
                "git_email": settings.GIT_EMAIL,
            }
        )
        
        result = provider.check_access(resource_name, principal)
        
        if not result.get("exists"):
            return {
                "success": True,
                "exists": False,
                "message": f"Resource '{resource_name}' not found in our GitOps configuration. It may not be managed by ATLAS yet.",
                "grants": []
            }
        
        # Format the response for the agent
        response = {
            "success": True,
            "exists": True,
            "resource_name": resource_name,
            "catalog": result.get("catalog", "unknown"),
            "grants": result.get("grants", [])
        }
        
        if principal:
            principal_privs = result.get("principal_grants", [])
            if principal_privs:
                response["principal"] = principal
                response["principal_privileges"] = principal_privs
                response["message"] = f"{principal} has the following privileges on {resource_name}: {', '.join(principal_privs)}"
            else:
                response["principal"] = principal
                response["principal_privileges"] = []
                response["message"] = f"{principal} has no grants on {resource_name}"
        else:
            if result.get("grants"):
                grant_summary = [f"{g.get('principal')}: {g.get('privileges')}" for g in result.get("grants", [])]
                response["message"] = f"Grants on {resource_name}: {'; '.join(grant_summary)}"
            else:
                response["message"] = f"No grants found on {resource_name}"
        
        return response
        
    except Exception as e:
        logger.error(f"Error checking resource access: {e}")
        return {
            "success": False,
            "error": str(e)
        }
