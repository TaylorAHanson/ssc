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
    description="Lists available reusable GitHub repository templates in the organization. Use this to help users discover boilarplate projects or examples for their new repositories.",
    args_schema=ListGitHubTemplatesInput
)
async def list_github_templates(description_hint: Optional[str] = None) -> Dict[str, Any]:
    """Execute the tool."""
    try:
        org = settings.GITHUB_ORG
        if not (settings.GITHUB_APP_ID and org):
            return {"status": "error", "message": "GitHub App not configured (GITHUB_APP_ID/GITHUB_ORG)"}

        async with GitHubProvider.from_settings() as github:
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
