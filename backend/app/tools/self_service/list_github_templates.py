from typing import Dict, Any, Optional
from pydantic import BaseModel, Field
from app.tools.mcp import tool
from app.providers.github.client import GitHubProvider
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)

class ListGitHubTemplatesInput(BaseModel):
    description_hint: Optional[str] = Field(None, description="Optional keyword to filter templates by description or name (e.g., 'python', 'databricks').")

@tool(
    name="list_github_templates",
    description="List available repository templates in the organization, optionally filtered by keyword.",
    args_schema=ListGitHubTemplatesInput
)
async def list_github_templates(description_hint: Optional[str] = None) -> Dict[str, Any]:
    """Execute the tool."""
    try:
        # settings.get_git_token() might be needed if GITHUB_TOKEN is not set
        token = settings.GITHUB_TOKEN 
        if not token and hasattr(settings, 'get_git_token'):
            token = settings.get_git_token()
            
        org = settings.GITHUB_ORG
        
        if not token:
            return {"status": "error", "message": "GitHub token not configured"}
            
        async with GitHubProvider(token=token, org=org) as github:
            templates = await github.list_templates()
            
            # Filter by hint if provided
            if description_hint:
                hint = description_hint.lower()
                templates = [
                    t for t in templates 
                    if hint in t["name"].lower() or hint in t["description"].lower()
                ]
            
            return {
                "status": "completed",
                "count": len(templates),
                "templates": templates,
                "org": org,
                "message": f"Found {len(templates)} templates in organization '{org}'"
            }
    except Exception as e:
        logger.error(f"Error listing GitHub templates: {str(e)}")
        return {"status": "error", "message": str(e)}
