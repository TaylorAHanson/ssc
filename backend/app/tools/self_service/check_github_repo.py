from typing import Dict, Any
from pydantic import BaseModel, Field
from app.tools.mcp import tool
from app.providers.github.client import GitHubProvider
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)

class CheckGitHubRepoInput(BaseModel):
    repo_name: str = Field(..., description="The name of the repository to check (without org prefix).")

@tool(
    name="check_github_repo",
    description="Check whether a repository name already exists in the configured GitHub organization.",
    args_schema=CheckGitHubRepoInput
)
async def check_github_repo(repo_name: str) -> Dict[str, Any]:
    """Execute the tool."""
    try:
        # settings.get_git_token() might be needed if GITHUB_TOKEN is not set
        # But settings usually has properties. Let's assume settings.GITHUB_TOKEN or separate method access.
        # The original code had: settings.GITHUB_TOKEN or settings.get_git_token()
        # I'll preserve that.
        token = settings.GITHUB_TOKEN 
        if not token and hasattr(settings, 'get_git_token'):
            token = settings.get_git_token()
            
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
