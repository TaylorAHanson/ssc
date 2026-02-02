from typing import Dict, Any, Optional
from app.tools.base import BaseTool
from app.providers.github.client import GitHubProvider
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)

class ListGitHubTemplatesTool(BaseTool):
    """Tool to search and list GitHub templates in the organization."""
    
    @property
    def name(self) -> str:
        return "list_github_templates"

    @property
    def description(self) -> str:
        return "Lists available reusable GitHub repository templates in the organization. Use this to help users discover boilarplate projects or examples for their new repositories."

    @property
    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "description_hint": {
                    "type": "string",
                    "description": "Optional keyword to filter templates by description or name (e.g., 'python', 'databricks')."
                }
            }
        }

    async def execute(self, description_hint: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        """Execute the tool."""
        try:
            token = settings.GITHUB_TOKEN or settings.get_git_token()
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
