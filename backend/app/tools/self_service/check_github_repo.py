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
    description="Checks if a GitHub repository exists in the organization. Use this to verify repo name availability before creation.",
    args_schema=CheckGitHubRepoInput
)
async def check_github_repo(repo_name: str) -> Dict[str, Any]:
    """Execute the tool."""
    try:
        org = settings.GITHUB_ORG
        if not (settings.GITHUB_APP_ID and org):
            return {"status": "error", "message": "GitHub App not configured (GITHUB_APP_ID/GITHUB_ORG)"}

        async with GitHubProvider.from_settings() as github:
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
