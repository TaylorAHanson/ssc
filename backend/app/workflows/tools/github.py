"""
GitHub workflow tools.
"""
import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.tools.mcp import tool
from app.workflows.tools import _common

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# GitHub tools
# --------------------------------------------------------------------------
# Values the agent sends to mean "no template". Treating them as a repo name
# makes GitHub 404 on /repos/<owner>/<value>/generate and fails the request.
_NO_TEMPLATE = {"", "none", "empty", "blank", "no", "no template", "n/a", "na", "null", "false"}


class GithubRepoInput(BaseModel):
    repo_name: str = Field(..., description="Repository name to create")
    template: Optional[str] = Field(
        default=None,
        description="Template repo name to generate from. Omit for an empty repository.",
    )
    description: Optional[str] = Field(default=None, description="Repository description")
    visibility: Optional[str] = Field(default=None, description="public | private | internal")


@tool(
    name="github_create_repo",
    args_schema=GithubRepoInput,
    side_effect_class="infra",
    description="Create a GitHub repository (optionally from a template).",
)
async def github_create_repo(
    repo_name: str,
    template: Optional[str] = None,
    description: Optional[str] = None,
    visibility: Optional[str] = None,
    **kwargs,
) -> Dict[str, Any]:
    provider = _common._get_github_provider()
    config: Dict[str, Any] = {}
    if description:
        config["description"] = description
    if visibility:
        config["private"] = visibility.lower() != "public"
    if template and template.strip().lower() not in _NO_TEMPLATE:
        result = await provider.create_from_template(template.strip(), repo_name, config)
    else:
        result = await provider.create_repo(repo_name, config)
    return {"repo": repo_name, "result": result}


class GithubAccessInput(BaseModel):
    repo_name: str = Field(...)
    username: str = Field(...)
    permission: str = Field(default="push", description="pull | push | admin")


@tool(
    name="github_set_permissions",
    args_schema=GithubAccessInput,
    side_effect_class="membership",
    description="Grant a GitHub user permissions on a repository.",
)
async def github_set_permissions(
    repo_name: str,
    username: str,
    permission: str = "push",
    **kwargs,
) -> Dict[str, Any]:
    provider = _common._get_github_provider()
    result = await provider.set_permissions(repo_name, username, permission)
    return {"repo": repo_name, "username": username, "result": result}


# --------------------------------------------------------------------------
# GitHub org team management (requires an org token with admin:org)
# --------------------------------------------------------------------------
class GithubCreateTeamInput(BaseModel):
    team_name: str = Field(..., description="Team name to create")
    description: Optional[str] = Field(default=None, description="Team description")
    privacy: str = Field(default="closed", description="closed (visible to org) | secret")


@tool(
    name="github_create_team",
    args_schema=GithubCreateTeamInput,
    side_effect_class="infra",
    description="Create a GitHub org team (idempotent). Returns the team, including its slug.",
)
async def github_create_team(
    team_name: str,
    description: Optional[str] = None,
    privacy: str = "closed",
    **kwargs,
) -> Dict[str, Any]:
    provider = _common._get_github_provider()
    team = await provider.create_team(team_name, description=description, privacy=privacy)
    return {"team_name": team_name, "team_slug": team.get("slug"), "result": team}


class GithubGrantTeamRepoInput(BaseModel):
    team_slug: str = Field(..., description="Team slug (from github_create_team / list_github_teams)")
    repo_name: str = Field(..., description="Repository name")
    permission: str = Field(default="push", description="pull | triage | push | maintain | admin")


@tool(
    name="github_grant_team_repo",
    args_schema=GithubGrantTeamRepoInput,
    side_effect_class="infra",
    description="Give a GitHub team a permission level on a repository.",
)
async def github_grant_team_repo(
    team_slug: str,
    repo_name: str,
    permission: str = "push",
    **kwargs,
) -> Dict[str, Any]:
    provider = _common._get_github_provider()
    ok = await provider.grant_team_repo(team_slug, repo_name, permission)
    return {"team_slug": team_slug, "repo": repo_name, "permission": permission, "result": ok}


class GithubAddTeamMembersInput(BaseModel):
    team_slug: str = Field(..., description="Team slug (from github_create_team / list_github_teams)")
    members: List[str] = Field(default_factory=list, description="GitHub usernames to add")
    role: str = Field(default="member", description="member | maintainer")


@tool(
    name="github_add_team_members",
    args_schema=GithubAddTeamMembersInput,
    side_effect_class="membership",
    description="Add one or more GitHub users to a team (org members: immediate; others: invited).",
)
async def github_add_team_members(
    team_slug: str,
    members: List[str],
    role: str = "member",
    **kwargs,
) -> Dict[str, Any]:
    provider = _common._get_github_provider()
    results = await provider.add_team_members(team_slug, members or [], role)
    return {"team_slug": team_slug, "members": members, "results": results}
