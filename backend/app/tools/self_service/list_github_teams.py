from typing import Dict, Any, Optional
from pydantic import BaseModel, Field
from app.tools.mcp import tool
from app.providers.github.client import GitHubProvider
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)


class ListGitHubTeamsInput(BaseModel):
    name_hint: Optional[str] = Field(
        None, description="Optional keyword to filter teams by name or description."
    )


@tool(
    name="list_github_teams",
    description=("Lists GitHub org teams (name, slug, description) so a user can pick which team "
                 "to request access to. Use before request_github_access when the user names a team."),
    args_schema=ListGitHubTeamsInput,
)
async def list_github_teams(name_hint: Optional[str] = None) -> Dict[str, Any]:
    """Execute the tool."""
    try:
        token = settings.GITHUB_TOKEN
        if not token and hasattr(settings, "get_git_token"):
            token = settings.get_git_token()

        org = settings.GITHUB_ORG
        if not token:
            return {"status": "error", "message": "GitHub token not configured"}
        if not org:
            return {"status": "error", "message": "GITHUB_ORG is not configured"}

        async with GitHubProvider(token=token, org=org) as github:
            teams = await github.list_teams()

            if name_hint:
                hint = name_hint.lower()
                teams = [
                    t for t in teams
                    if hint in (t.get("name") or "").lower()
                    or hint in (t.get("description") or "").lower()
                ]

            return {
                "status": "completed",
                "count": len(teams),
                "teams": teams,
                "org": org,
                "message": f"Found {len(teams)} teams in organization '{org}'",
            }
    except Exception as e:
        logger.error(f"Error listing GitHub teams: {str(e)}")
        return {"status": "error", "message": str(e)}
