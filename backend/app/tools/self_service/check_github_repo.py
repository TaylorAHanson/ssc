from typing import Dict, Any, Optional
from app.tools.base import BaseTool
from app.providers.github.client import GitHubProvider
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)

class CheckGitHubRepoTool(BaseTool):
    """Tool to check if a GitHub repository exists."""
    
    @property
    def name(self) -> str:
        return "check_github_repo"

    @property
    def description(self) -> str:
        return "Checks if a GitHub repository exists in the organization. Use this to verify repo name availability before creation."

    @property
    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "repo_name": {
                    "type": "string",
                    "description": "The name of the repository to check (without org prefix)."
                }
            },
            "required": ["repo_name"]
        }

    async def execute(self, repo_name: str, **kwargs) -> Dict[str, Any]:
        """Execute the tool."""
        try:
            token = settings.GITHUB_TOKEN or settings.get_git_token()
            org = settings.GITHUB_ORG
            
            if not token:
                return {"status": "error", "message": "GitHub token not configured"}
                
            async with GitHubProvider(token=token, org=org) as github:
                exists = await github.check_repo_exists(repo_name)
                
                return {
                    "status": "completed",
                    "exists": exists,
                    "repo_name": repo_name,
                    "org": org,
                    "message": f"Repository '{repo_name}' {'exists' if exists else 'does not exist'} in organization '{org}'"
                }
        except Exception as e:
            logger.error(f"Error checking GitHub repo: {str(e)}")
            return {"status": "error", "message": str(e)}
