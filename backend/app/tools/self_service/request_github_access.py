from typing import Dict, Any, Optional
from pydantic import BaseModel, Field
from app.tools.mcp import tool
from app.providers.github.client import GitHubProvider
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)


class RequestGitHubAccessInput(BaseModel):
    target_type: str = Field(default="repo", description="What to request access to: 'repo' or 'team'.")
    target: str = Field(..., description="Repository name (for repo) or team name/slug (for team).")
    permission: Optional[str] = Field(
        default=None,
        description="Desired permission/role, advisory only (e.g. pull/push/admin for a repo, "
                    "member/maintainer for a team). GitHub controls the actual grant.",
    )


@tool(
    name="request_github_access",
    description=("Looks up an existing GitHub repo or team and returns the GitHub-native request "
                 "link the user should follow to request access. This app does NOT grant access "
                 "and imposes no approval gate — a repo/team owner approves the request inside "
                 "GitHub. Use for 'request access to repo X' or 'join team Y'."),
    args_schema=RequestGitHubAccessInput,
)
async def request_github_access(target_type: str = "repo", target: str = "",
                                permission: Optional[str] = None) -> Dict[str, Any]:
    """Execute the tool."""
    try:
        token = settings.GITHUB_TOKEN
        if not token and hasattr(settings, "get_git_token"):
            token = settings.get_git_token()

        org = settings.GITHUB_ORG
        web = (settings.GITHUB_WEB_BASE_URL or "https://github.com").rstrip("/")
        if not token:
            return {"status": "error", "message": "GitHub token not configured"}
        if not org:
            return {"status": "error", "message": "GITHUB_ORG is not configured"}
        if not target or not target.strip():
            return {"status": "error", "message": "A target (repo name or team name/slug) is required."}

        target = target.strip()
        ttype = (target_type or "repo").lower()

        async with GitHubProvider(token=token, org=org) as github:
            if ttype == "team":
                team = await github.get_team(target)
                if not team:
                    # Fall back to a case-insensitive name match against the team list.
                    teams = await github.list_teams()
                    match = next(
                        (t for t in teams
                         if (t.get("slug") == target)
                         or (t.get("name") or "").lower() == target.lower()),
                        None,
                    )
                    team = match
                if not team:
                    return {
                        "status": "completed",
                        "found": False,
                        "target_type": "team",
                        "target": target,
                        "org": org,
                        "message": (
                            f"No team matching '{target}' was found in '{org}'. "
                            f"Use list_github_teams to see available teams."
                        ),
                    }
                slug = team.get("slug")
                url = f"{web}/orgs/{org}/teams/{slug}/members"
                instructions = (
                    f"Open {url} and choose **Request to join** the '{team.get('name') or slug}' team. "
                    f"A team maintainer approves the request in GitHub."
                )
                return {
                    "status": "completed",
                    "found": True,
                    "target_type": "team",
                    "target": slug,
                    "team_name": team.get("name"),
                    "org": org,
                    "requested_role": permission,
                    "request_url": url,
                    "instructions": instructions,
                    "message": (
                        f"Access to team '{team.get('name') or slug}' is granted by a team maintainer "
                        f"in GitHub, not by this app. {instructions}"
                    ),
                }

            # repo
            exists = await github.check_repo_exists(target)
            url = f"{web}/{org}/{target}"
            if not exists:
                return {
                    "status": "completed",
                    "found": False,
                    "target_type": "repo",
                    "target": target,
                    "org": org,
                    "message": (
                        f"Repository '{org}/{target}' was not found (or isn't visible to this app's "
                        f"token). Confirm the name; if it's private you can still try {url}."
                    ),
                }
            instructions = (
                f"Open {url} and click **Request access** near the top of the repo page. "
                f"A repository admin approves the request in GitHub."
            )
            return {
                "status": "completed",
                "found": True,
                "target_type": "repo",
                "target": target,
                "org": org,
                "requested_permission": permission,
                "request_url": url,
                "instructions": instructions,
                "message": (
                    f"Access to repo '{org}/{target}' is granted by a repo admin in GitHub, not by "
                    f"this app. {instructions}"
                ),
            }
    except Exception as e:
        logger.error(f"Error building GitHub access request: {str(e)}")
        return {"status": "error", "message": str(e)}
