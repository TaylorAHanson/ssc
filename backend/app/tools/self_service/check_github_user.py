from typing import Dict, Any, List
from pydantic import BaseModel, Field
from app.tools.mcp import tool
from app.providers.github.client import GitHubProvider
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)


class CheckGitHubUserInput(BaseModel):
    usernames: List[str] = Field(
        ..., description="GitHub usernames (logins) to validate, e.g. ['octocat', 'taylhans_QCOM']."
    )


@tool(
    name="check_github_user",
    description="Validate whether one or more GitHub usernames exist and check their organization membership status.",
    args_schema=CheckGitHubUserInput,
)
async def check_github_user(usernames: List[str]) -> Dict[str, Any]:
    """Execute the tool."""
    try:
        token = settings.GITHUB_TOKEN
        if not token and hasattr(settings, "get_git_token"):
            token = settings.get_git_token()

        org = settings.GITHUB_ORG
        if not token:
            return {"status": "error", "message": "GitHub token not configured"}

        logins = [u.strip() for u in (usernames or []) if u and u.strip()]
        if not logins:
            return {"status": "error", "message": "No usernames provided"}

        results: List[Dict[str, Any]] = []
        async with GitHubProvider(token=token, org=org) as github:
            for login in logins:
                profile = await github.get_user(login)
                exists = profile is not None
                is_member = False
                if exists and org:
                    try:
                        is_member = await github.is_org_member(login)
                    except Exception as e:  # noqa: BLE001 - membership check is best-effort
                        logger.warning("org membership check failed for %s: %s", login, e)
                results.append({
                    "username": login,
                    "exists": exists,
                    "is_org_member": is_member,
                    "name": (profile or {}).get("name"),
                    "profile_url": (profile or {}).get("html_url"),
                    # Non-members can still be added — GitHub sends an org invite they must accept.
                    "note": (
                        "not found on GitHub" if not exists
                        else ("org member (added immediately)" if is_member
                              else "not an org member (will receive an invitation to accept)")
                    ),
                })

        invalid = [r["username"] for r in results if not r["exists"]]
        return {
            "status": "completed",
            "org": org,
            "results": results,
            "all_valid": not invalid,
            "invalid_usernames": invalid,
            "message": (
                f"All {len(results)} username(s) exist on GitHub."
                if not invalid else
                f"{len(invalid)} username(s) not found: {', '.join(invalid)}"
            ),
        }
    except Exception as e:
        logger.error(f"Error checking GitHub user(s): {str(e)}")
        return {"status": "error", "message": str(e)}
