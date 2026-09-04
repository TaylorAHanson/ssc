"""
GitHub workflow tools and tag change PR orchestration.
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
class GithubRepoInput(BaseModel):
    repo_name: str = Field(..., description="Repository name to create")
    template: Optional[str] = Field(default=None, description="Optional template repo")
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
    if template:
        result = await provider.create_from_template(template, repo_name, config)
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


class OpenTagChangePrInput(BaseModel):
    dataset_name: str = Field(..., description="Dataset the tag change applies to")
    tags_sql: str = Field(..., description="Generated ALTER ... SET/UNSET TAGS statements")
    submitted_at: Optional[str] = Field(
        default=None,
        description="ISO-8601 submission time; orders the migration and stays stable on retry",
    )
    requested_by: Optional[str] = Field(default=None, description="Requester display name")
    requested_by_email: Optional[str] = Field(default=None, description="Requester email")
    changes: Optional[List[Dict[str, Any]]] = Field(
        default=None, description="Per-table {table, set, unset} diff, summarized in the PR body"
    )
    pr_title: Optional[str] = Field(default=None, description="PR title; defaults from dataset_name")


def _parse_submitted_at(value: Optional[str]):
    """Submission time for ordering/naming the migration; falls back to now."""
    from datetime import datetime, timezone

    if value:
        try:
            parsed = datetime.fromisoformat(value)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            logger.warning("Unparseable submitted_at %r; using current time", value)
    return datetime.now(timezone.utc)


def _tag_change_pr_body(
    request_id: str,
    dataset_name: str,
    requested_by: Optional[str],
    changes: List[Dict[str, Any]],
) -> str:
    lines = [
        f"Tag change for **{dataset_name}**, requested by {requested_by or 'unknown'}.",
        "",
        f"Request: `{request_id}`",
        "",
        "| Table | Set | Unset |",
        "| --- | --- | --- |",
    ]
    for change in changes:
        set_tags = change.get("set") or {}
        unset_tags = change.get("unset") or []
        set_cell = ", ".join(f"`{k}` = `{v}`" for k, v in set_tags.items()) or "—"
        unset_cell = ", ".join(f"`{k}`" for k in unset_tags) or "—"
        lines.append(f"| `{change.get('table')}` | {set_cell} | {unset_cell} |")
    lines += [
        "",
        "Merging this PR applies the tags to this environment. Opened automatically; "
        "close it to reject the request.",
    ]
    return "\n".join(lines)


@tool(
    name="open_tag_change_pr",
    args_schema=OpenTagChangePrInput,
    side_effect_class="infra",
    description="Open a GitOps pull request that applies a UC tag change on merge.",
)
async def open_tag_change_pr(
    dataset_name: str,
    tags_sql: str,
    submitted_at: Optional[str] = None,
    requested_by: Optional[str] = None,
    requested_by_email: Optional[str] = None,
    changes: Optional[List[Dict[str, Any]]] = None,
    pr_title: Optional[str] = None,
    **kwargs,
) -> Dict[str, Any]:
    """Commit the generated tag SQL to the governance repo and open a PR for it."""
    from app.core.config import settings
    from app.core.exceptions import PermanentError
    from app.db.session import get_db
    from app.state_machines.facts import add_fact
    from app.workflows.tag_sql import build_migration_file, migration_filename

    request_id = kwargs.get("_request_id")
    if not request_id:
        raise PermanentError(
            "open_tag_change_pr must run inside a request workflow: the request id "
            "names the branch and migration file, and there is no safe substitute."
        )
    repo = (settings.GOVERNANCE_TAGS_REPO or "").strip()
    base = (settings.GOVERNANCE_TAGS_BASE_BRANCH or "").strip()
    if not repo:
        raise PermanentError(
            "GOVERNANCE_TAGS_REPO is not set, so there is nowhere to open the tag-change "
            "PR. Set it in Admin -> Settings (Governance Tags) or via databricks.yml."
        )
    if not base:
        raise PermanentError(
            "GOVERNANCE_TAGS_BASE_BRANCH is not set. It must name the governance repo "
            f"branch for this environment (e.g. '{settings.ENVIRONMENT}'), because merging "
            "into that branch is what applies the tags. Set it in Admin -> Settings."
        )
    if not (tags_sql or "").strip():
        raise PermanentError(
            "The tag change generated no SQL — nothing to apply. This means the desired "
            "tags already match Unity Catalog; the request should not have been created."
        )

    generated_at = _parse_submitted_at(submitted_at)
    filename = migration_filename(request_id, generated_at)
    prefix = (settings.GOVERNANCE_TAGS_PATH or "").strip().strip("/")
    path = f"{prefix}/{filename}" if prefix else filename
    branch = f"tag-change/{request_id}"
    title = pr_title or f"Tag change: {dataset_name}"
    content = build_migration_file(
        request_id=request_id,
        dataset_name=dataset_name,
        requested_by=requested_by,
        requested_by_email=requested_by_email,
        generated_at=generated_at,
        sql=tags_sql,
    )

    provider = _common._get_github_provider()
    await provider.create_branch(repo=repo, branch=branch, from_branch=base)
    await provider.create_or_update_file(
        repo=repo,
        path=path,
        content=content,
        branch=branch,
        message=f"Tag change: {dataset_name} ({request_id})",
    )
    pr = await provider.create_pull_request(
        repo=repo,
        title=title,
        head=branch,
        base=base,
        body=_tag_change_pr_body(request_id, dataset_name, requested_by, changes or []),
    )

    pr_number = pr.get("number")
    pr_url = pr.get("html_url")
    result = {
        "pr_number": pr_number,
        "pr_url": pr_url,
        "repo": repo,
        "branch": branch,
        "base": base,
        "file_path": path,
    }
    db = next(get_db())
    try:
        add_fact(db, request_id, "pr_created", result, actor="system")
    finally:
        db.close()

    logger.info("[%s] opened tag-change PR #%s (%s) on %s", request_id, pr_number, path, repo)
    return result
