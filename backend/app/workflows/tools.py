"""
V2 graph-internal tools (the "providers become tools" leg of the thesis).

Each wraps a mutating provider operation as an :class:`~app.tools.mcp.McpTool`
so graph nodes invoke it through the shared ``ToolExecutor`` — getting OPA
pre-flight, idempotency, and audit for free.

They intentionally live OUTSIDE ``app.tools/`` so auto-discovery (``load_tools``)
does NOT register them as chat-agent tools: a raw ``grant_uc_access`` /
``terraform_apply`` must not be directly callable by the conversational agent
until per-workflow capability scoping (M3) exists. Graphs import them explicitly.

Provider access goes through small ``_get_*_provider`` getters so the eval
harness can monkeypatch them with fakes (no live Databricks/GitHub needed).
"""
import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.tools.mcp import tool

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Provider getters (monkeypatch points)
# --------------------------------------------------------------------------
def _get_databricks_provider():
    from app.core.config import settings
    from app.core.exceptions import PermanentError
    from app.providers.databricks.client import DatabricksProvider

    host = settings.DATABRICKS_HOST or settings.DATABRICKS_WORKSPACE_URL
    if not host:
        raise PermanentError("DATABRICKS_HOST is required")
    return DatabricksProvider(
        host=host,
        token=settings.DATABRICKS_TOKEN,
        client_id=settings.DATABRICKS_CLIENT_ID,
        client_secret=settings.DATABRICKS_CLIENT_SECRET,
        config={"warehouse_id": settings.DATABRICKS_WAREHOUSE_ID},
    )


def _get_github_provider():
    from app.core.config import settings
    from app.providers.github.client import GitHubProvider

    return GitHubProvider(
        token=settings.GITHUB_TOKEN or settings.get_git_token(),
        org=settings.GITHUB_ORG,
    )


def _get_gitops_provider():
    """Terraform / GitOps-volume provider used for infra plan+apply."""
    from app.providers.gitops.volume import VolumeGitOpsProvider
    return VolumeGitOpsProvider()


def _get_notification_provider():
    from app.providers.notifications.client import NotificationProvider
    return NotificationProvider()


def _get_identity_provider():
    """Vendor-neutral group-membership provider (noop/rest/lmws via config)."""
    from app.providers.identity import get_identity_provider
    return get_identity_provider()


# --------------------------------------------------------------------------
# Data-grant tools
# --------------------------------------------------------------------------
class GrantUcAccessInput(BaseModel):
    asset_type: str = Field(..., description="schema | table | view | volume")
    asset_name: str = Field(..., description="Fully-qualified UC name")
    principal: str = Field(..., description="User/group to grant access to")
    access_level: str = Field(..., description="read | write | manage")


@tool(name="grant_uc_access", args_schema=GrantUcAccessInput, side_effect_class="data_grant",
      description="Grant a principal access to a Unity Catalog asset via SQL GRANT.")
async def grant_uc_access(asset_type: str, asset_name: str, principal: str,
                          access_level: str, **kwargs) -> Dict[str, Any]:
    provider = _get_databricks_provider()
    result = await provider.grant_access(
        asset_type=asset_type, asset_name=asset_name,
        principal=principal, access_level=access_level,
    )
    return {"asset_name": asset_name, "result": result}


class ResolveDataOwnersInput(BaseModel):
    # Loosely typed on purpose: the agent occasionally emits `assets` as a bare
    # name string or a single dict instead of the documented list, and sometimes
    # injects a stray `data_owners` string. We normalize inside the tool, so we
    # accept anything here rather than 422-ing (and failing) the workflow step.
    assets: Optional[Any] = Field(
        default=None,
        description="Assets: a list of {asset_name, asset_type}, a single such dict, or a bare asset-name string.",
    )
    asset_name: Optional[str] = Field(
        default=None, description="Single asset name (backwards-compat) when `assets` isn't a list."
    )
    asset_type: Optional[str] = Field(
        default=None, description="Single asset type for the backwards-compat single-asset form."
    )
    data_owners: Optional[Any] = Field(
        default=None, description="Pre-supplied owners — honored only when it's a real list of strings."
    )


def _normalize_assets(assets: Any, asset_name: Optional[str] = None,
                      asset_type: Optional[str] = None) -> List[Dict[str, Any]]:
    """Coerce agent-supplied asset params into ``[{asset_name, asset_type}, ...]``.

    The agent sometimes sends ``assets`` as a bare name string or a single dict
    rather than the documented list. Recover the structured form so owner
    resolution still runs instead of failing the whole step.
    """
    out: List[Dict[str, Any]] = []
    if isinstance(assets, list):
        for a in assets:
            if isinstance(a, dict) and a.get("asset_name"):
                out.append({"asset_name": a.get("asset_name"), "asset_type": a.get("asset_type") or asset_type})
            elif isinstance(a, str) and a:
                out.append({"asset_name": a, "asset_type": asset_type})
    elif isinstance(assets, dict) and assets.get("asset_name"):
        out.append({"asset_name": assets.get("asset_name"), "asset_type": assets.get("asset_type") or asset_type})
    elif isinstance(assets, str) and assets:
        out.append({"asset_name": assets, "asset_type": asset_type})
    if not out and asset_name:
        out.append({"asset_name": asset_name, "asset_type": asset_type})
    return out


async def resolve_owner_groups_from_assets(
    assets: Optional[List[Dict[str, Any]]],
    *,
    fallback_to_owner: bool = True,
) -> List[str]:
    """Resolve approver group(s) for ``assets`` from the UC ``approver_group`` tag.

    Reusable core shared by the ``resolve_data_owners`` tool (pre-gate step) and
    the generic gate ``approver_group_tag`` source. Reads the configured tag from
    each asset; when a tag is missing and ``fallback_to_owner`` is set, uses the
    asset owner. Best-effort: returns ``[]`` (degrades gracefully) if the provider
    is unavailable so the gate still renders.
    """
    if not assets:
        return []
    from app.core.config import settings

    tag_key = settings.APPROVER_GROUP_TAG_KEY
    found: set = set()
    try:
        provider = _get_databricks_provider()
        for asset in assets:
            name, atype = asset.get("asset_name"), asset.get("asset_type")
            if not (name and atype):
                continue
            tags = await provider.get_asset_tags(atype, name, [tag_key])
            grp = tags.get(tag_key)
            if grp:
                found.add(grp)
            elif fallback_to_owner:
                owner = await provider.get_asset_owner(atype, name)
                if owner:
                    found.add(owner)
    except Exception as e:  # noqa: BLE001 - degrade gracefully like the old graph
        logger.warning("resolve_owner_groups_from_assets degraded: %s", e)
    return sorted(found)


@tool(name="resolve_data_owners", args_schema=ResolveDataOwnersInput, side_effect_class="read",
      description="Resolve the data-owner approver group(s) for the requested assets from UC "
                  "tags (approver_group), falling back to the asset owner. Read-only.")
async def resolve_data_owners(assets: Any = None,
                              data_owners: Any = None,
                              asset_name: Optional[str] = None,
                              asset_type: Optional[str] = None,
                              **kwargs) -> Dict[str, Any]:
    """Owner resolution for data-access gates, as a tool.

    Lets the declarative spec engine express what used to require the dedicated
    ``data_access`` code graph: a pre-gate step that discovers who must approve.

    Defensive about its inputs: the agent sometimes sends ``assets`` as a bare
    string/dict or injects a stray ``data_owners`` string. We normalize the
    assets and only honor ``data_owners`` when it's a genuine list of strings;
    otherwise we resolve owners from the assets (tag -> owner).
    """
    owners = [o for o in data_owners if isinstance(o, str)] if isinstance(data_owners, list) else []
    if not owners:
        norm = _normalize_assets(assets, asset_name, asset_type)
        owners = await resolve_owner_groups_from_assets(norm)
    return {"ok": True, "data_owners": owners}


# --------------------------------------------------------------------------
# Infra tools (Terraform / GitOps)
# --------------------------------------------------------------------------
@tool(name="terraform_plan", side_effect_class="read",
      description="Produce a Terraform/GitOps plan for review (no apply).")
async def terraform_plan(**kwargs) -> Dict[str, Any]:
    provider = _get_gitops_provider()
    plan = await provider.plan(kwargs.get("request_id"), kwargs.get("parameters", {}))
    return {"plan": plan}


@tool(name="terraform_apply", side_effect_class="infra",
      description="Apply a reviewed Terraform/GitOps plan (provisions infra).")
async def terraform_apply(**kwargs) -> Dict[str, Any]:
    provider = _get_gitops_provider()
    result = await provider.apply(kwargs.get("request_id"), kwargs.get("parameters", {}))
    return {"applied": result}


class CreateUcObjectInput(BaseModel):
    object_type: str = Field(..., description="catalog | schema | volume | folder | tag | credential")
    name: str = Field(..., description="Object name / path")
    parameters: Dict[str, Any] = Field(default_factory=dict)


@tool(name="create_uc_object", args_schema=CreateUcObjectInput, side_effect_class="infra",
      description="Create a Unity Catalog / workspace object (catalog, schema, volume, folder, tag, credential).")
async def create_uc_object(object_type: str, name: str,
                           parameters: Optional[Dict[str, Any]] = None, **kwargs) -> Dict[str, Any]:
    # Several of these were stubs in V1; keep a thin, idempotent record while the
    # concrete provider calls are wired per object_type.
    logger.info("create_uc_object: %s '%s' params=%s", object_type, name, parameters)
    return {"created": True, "object_type": object_type, "name": name}


class CreateSpInput(BaseModel):
    display_name: str = Field(..., description="Service principal display name")


@tool(name="create_service_principal", args_schema=CreateSpInput, side_effect_class="infra",
      description="Create a Databricks service principal.")
async def create_service_principal(display_name: str, **kwargs) -> Dict[str, Any]:
    provider = _get_gitops_provider()
    result = await provider.apply(kwargs.get("request_id"),
                                  {"display_name": display_name, **kwargs.get("parameters", {})})
    return {"service_principal": display_name, "applied": result}


# --------------------------------------------------------------------------
# GitHub tools
# --------------------------------------------------------------------------
class GithubRepoInput(BaseModel):
    repo_name: str = Field(..., description="Repository name to create")
    template: Optional[str] = Field(default=None, description="Optional template repo")
    description: Optional[str] = Field(default=None, description="Repository description")
    visibility: Optional[str] = Field(default=None, description="public | private | internal")


@tool(name="github_create_repo", args_schema=GithubRepoInput, side_effect_class="infra",
      description="Create a GitHub repository (optionally from a template).")
async def github_create_repo(repo_name: str, template: Optional[str] = None,
                             description: Optional[str] = None,
                             visibility: Optional[str] = None, **kwargs) -> Dict[str, Any]:
    provider = _get_github_provider()
    # The provider merges this config into the GitHub create payload. Use the
    # `private` boolean (universally accepted, incl. the template-generate API)
    # derived from the requested visibility.
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


@tool(name="github_set_permissions", args_schema=GithubAccessInput, side_effect_class="membership",
      description="Grant a GitHub user permissions on a repository.")
async def github_set_permissions(repo_name: str, username: str,
                                 permission: str = "push", **kwargs) -> Dict[str, Any]:
    provider = _get_github_provider()
    result = await provider.set_permissions(repo_name, username, permission)
    return {"repo": repo_name, "username": username, "result": result}


# --------------------------------------------------------------------------
# GitHub org team management (requires an org token with admin:org)
# --------------------------------------------------------------------------
class GithubCreateTeamInput(BaseModel):
    team_name: str = Field(..., description="Team name to create")
    description: Optional[str] = Field(default=None, description="Team description")
    privacy: str = Field(default="closed", description="closed (visible to org) | secret")


@tool(name="github_create_team", args_schema=GithubCreateTeamInput, side_effect_class="infra",
      description="Create a GitHub org team (idempotent). Returns the team, including its slug.")
async def github_create_team(team_name: str, description: Optional[str] = None,
                             privacy: str = "closed", **kwargs) -> Dict[str, Any]:
    provider = _get_github_provider()
    team = await provider.create_team(team_name, description=description, privacy=privacy)
    return {"team_name": team_name, "team_slug": team.get("slug"), "result": team}


class GithubGrantTeamRepoInput(BaseModel):
    team_slug: str = Field(..., description="Team slug (from github_create_team / list_github_teams)")
    repo_name: str = Field(..., description="Repository name")
    permission: str = Field(default="push", description="pull | triage | push | maintain | admin")


@tool(name="github_grant_team_repo", args_schema=GithubGrantTeamRepoInput, side_effect_class="infra",
      description="Give a GitHub team a permission level on a repository.")
async def github_grant_team_repo(team_slug: str, repo_name: str,
                                 permission: str = "push", **kwargs) -> Dict[str, Any]:
    provider = _get_github_provider()
    ok = await provider.grant_team_repo(team_slug, repo_name, permission)
    return {"team_slug": team_slug, "repo": repo_name, "permission": permission, "result": ok}


class GithubAddTeamMembersInput(BaseModel):
    team_slug: str = Field(..., description="Team slug (from github_create_team / list_github_teams)")
    members: List[str] = Field(default_factory=list, description="GitHub usernames to add")
    role: str = Field(default="member", description="member | maintainer")


@tool(name="github_add_team_members", args_schema=GithubAddTeamMembersInput, side_effect_class="membership",
      description="Add one or more GitHub users to a team (org members: immediate; others: invited).")
async def github_add_team_members(team_slug: str, members: List[str],
                                  role: str = "member", **kwargs) -> Dict[str, Any]:
    provider = _get_github_provider()
    results = await provider.add_team_members(team_slug, members or [], role)
    return {"team_slug": team_slug, "members": members, "results": results}


# NOTE: GitHub repo/team access is intentionally NOT a workflow. It carries no
# app-side gate or request record — the repo/team owner approves natively in
# GitHub. That capability lives as an agent-level chat tool
# (app.tools.self_service.request_github_access), not here.


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


def _tag_change_pr_body(request_id: str, dataset_name: str, requested_by: Optional[str],
                        changes: List[Dict[str, Any]]) -> str:
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


@tool(name="open_tag_change_pr", args_schema=OpenTagChangePrInput, side_effect_class="infra",
      description="Open a GitOps pull request that applies a UC tag change on merge.")
async def open_tag_change_pr(dataset_name: str, tags_sql: str,
                             submitted_at: Optional[str] = None,
                             requested_by: Optional[str] = None,
                             requested_by_email: Optional[str] = None,
                             changes: Optional[List[Dict[str, Any]]] = None,
                             pr_title: Optional[str] = None,
                             **kwargs) -> Dict[str, Any]:
    """Commit the generated tag SQL to the governance repo and open a PR for it.

    The app never runs ``ALTER ... TAGS`` itself: merging the PR is what applies
    the change, so this is the whole mutation. Branch, file, and PR creation are
    each idempotent, and the filename is derived from the request's submission
    time, so a replay updates the same migration rather than adding a second one.
    """
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
        # The governance repo branches per environment and has no 'main', so
        # guessing here would 404 at branch creation with a much vaguer message.
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
    # A blank path would put the migration at the repo root, where the governance
    # repo's validation workflow doesn't look — so it would merge unvalidated.
    prefix = (settings.GOVERNANCE_TAGS_PATH or "").strip().strip("/")
    path = f"{prefix}/{filename}" if prefix else filename
    # The "tag-change/" prefix is contractual, not cosmetic: the governance repo
    # only auto-closes a failed validation on branches matching it (its
    # APP_BRANCH_PREFIX). Rename this and a rejected migration stays open instead,
    # leaving the request waiting on a merge that will never come.
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

    provider = _get_github_provider()
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
    # Written here rather than via the step's success_fact: the gate poller and
    # the tag-change list endpoint both read these fields off a flat pr_created
    # payload, and the generic step fact nests them under step/results.
    db = next(get_db())
    try:
        add_fact(db, request_id, "pr_created", result, actor="system")
    finally:
        db.close()

    logger.info("[%s] opened tag-change PR #%s (%s) on %s", request_id, pr_number, path, repo)
    return result


# --------------------------------------------------------------------------
# Membership tool (identity group) — backed by the pluggable
# IdentityGroupProvider (noop|rest|lmws via settings.IDENTITY_PROVIDER).
# --------------------------------------------------------------------------
class GroupMembershipInput(BaseModel):
    group: str = Field(..., description="Identity group / list name")
    members: List[str] = Field(..., description="Members to add")


@tool(name="add_group_membership", args_schema=GroupMembershipInput, side_effect_class="membership",
      description=(
          "Add members to an identity group/list (Entra/Okta/SCIM/LMWS-backed). "
          "To verify before adding, look up the USER with member_lookup — do NOT "
          "call group_lookup first: restricted / N2K lists reject that lookup even "
          "when the add is valid, so gating on it wrongly blocks the request. The "
          "membership backend authorizes the write itself; if the caller isn't "
          "entitled, this tool surfaces that error."))
async def add_group_membership(group: str, members: List[str], **kwargs) -> Dict[str, Any]:
    # Normalize member identifiers the same way member_lookup does: the directory
    # keys on the corporate username (local part of the email), so a raw
    # 'user@domain' is rejected as an invalid member. Accept either form.
    from app.tools.self_service.identity_groups import _normalize_member

    normalized = [_normalize_member(m) for m in members]
    normalized = [m for m in normalized if m]
    if normalized != members:
        logger.info("add_group_membership: normalized members %r -> %r", members, normalized)
    provider = _get_identity_provider()
    result = await provider.list_members_add(group, normalized)
    return {"group": group, "members": normalized, "result": result}


# --------------------------------------------------------------------------
# Notify tool
# --------------------------------------------------------------------------
class NotifyInput(BaseModel):
    subject: str = Field(...)
    body: str = Field(...)
    to_email: Optional[str] = Field(default=None)


@tool(name="send_notification", args_schema=NotifyInput, side_effect_class="notify",
      description="Send an email/Teams notification.")
async def send_notification(subject: str, body: str, to_email: Optional[str] = None, **kwargs) -> Dict[str, Any]:
    from app.core.config import settings
    provider = _get_notification_provider()
    # Fall back to the governance group when a workflow step doesn't specify a
    # recipient (e.g. the enforcement_sentinel notify step), matching the
    # poller's failure-notification behaviour.
    recipient = to_email or settings.GOVERNANCE_EMAIL_GROUP
    # Recipients may be a comma-separated list (e.g. report subscribers); send
    # to each individually so a single bad address can't drop the whole batch.
    recipients = [e.strip() for e in str(recipient or "").split(",") if e.strip()]
    results = [await provider.send_email(to=r, subject=subject, body=body, is_html=True)
               for r in recipients]
    return {"sent": any(results), "to": recipients, "result": results}


# --------------------------------------------------------------------------
# Enforcement sentinel tools (automated pipeline)
# --------------------------------------------------------------------------
def _load_request(request_id: Optional[str]):
    """Load the originating request row for a workflow step (or ``(None, None)``).

    Returns an open ``(db, request)`` pair; callers are responsible for closing
    ``db``. Used by tools that must persist back to / read from the request that
    spawned the workflow step (sentinel scan results, allowlist exceptions,
    report bodies). The request id is injected by the ToolExecutor as
    ``_request_id`` (the step's executor scope).

    The load below opens a transaction, and SQLAlchemy keeps the pooled
    connection checked out until that transaction ends. A step that then runs
    for minutes without touching the database (a multi-workspace Sentinel scan)
    would hold a Lakebase connection idle-in-transaction for its whole duration,
    and Lakebase eventually closes it — which is what surfaced as "SSL
    connection has been closed unexpectedly" on the *next* write. So we end the
    read transaction immediately, returning the connection to the pool; the
    session stays usable and transparently checks out a fresh, pre-pinged
    connection the next time it is used.

    ``expire_on_commit`` is disabled first: otherwise that commit expires
    ``request``, and the caller's very next attribute read would lazily reload
    it and re-open the transaction we just closed.
    """
    if not request_id:
        return None, None
    from app.db import RequestModel
    from app.db.session import get_db

    db = next(get_db())
    db.expire_on_commit = False
    request = db.query(RequestModel).filter(RequestModel.id == request_id).first()
    if request is None:
        db.close()
        return None, None
    db.commit()
    return db, request


@tool(name="sentinel_discover", side_effect_class="read",
      description="Discover policy violations across governed assets (OPA evaluation).")
async def sentinel_discover(**kwargs) -> Dict[str, Any]:
    # Test/eval hook: callers (golden transcripts) may inject canned violations.
    if "_violations" in kwargs:
        violations = kwargs.get("_violations") or []
        return {"violations": violations, "checks": [], "summary": f"{len(violations)} injected violation(s)."}

    request_id = kwargs.get("_request_id")
    db, request = _load_request(request_id)
    if request is None:
        logger.warning("sentinel_discover: no request found for id=%s; nothing to scan", request_id)
        return {"violations": [], "checks": [], "summary": "No request context; scan skipped."}

    logger.info("sentinel_discover: request=%s workspaces=%s", request_id,
                (request.state_context or {}).get("workspaces") or "all")
    try:
        from app.workflows.sentinel import run_discovery
        # No session is handed to run_discovery: it runs for minutes and opens
        # its own short-lived sessions, so nothing holds a connection meanwhile.
        result = await run_discovery(request)
        # IMPORTANT: run_discovery already persisted every violation and check to
        # the ``sentinel_findings`` table, and downstream steps (enforce/notify)
        # reload them from there. Return ONLY the compact counts + summary — never
        # the full lists. The graph engine persists a step's tool result in three
        # places (the ``discover_completed`` fact/event row, the LangGraph
        # checkpoint, and — via ``writes_context`` — state_context); carrying tens
        # of thousands of records through all of them spiked memory (OOM-killing
        # the app mid-scan) and re-bloated the very columns the findings table
        # exists to keep small.
        return {
            "summary": result.get("summary", ""),
            "violation_count": result.get("violation_count", 0),
            "pass_count": result.get("pass_count", 0),
            "total_resources_scanned": result.get("total_resources_scanned", 0),
        }
    finally:
        db.close()


@tool(name="sentinel_enforce", side_effect_class="app_write",
      description="Apply automated remediation for discovered violations: safe/reversible "
                  "actions (certify/uncertify/warn) execute; destructive intents are "
                  "downgraded to an owner warning and left for manual Review & Act.")
async def sentinel_enforce(**kwargs) -> Dict[str, Any]:
    request_id = kwargs.get("_request_id")
    db, request = _load_request(request_id)
    if request is None:
        logger.warning("sentinel_enforce: no request found for id=%s", request_id)
        return {"enforced": True, "actions": [], "summary": "No request context; nothing to enforce."}

    logger.info("sentinel_enforce: request=%s", request_id)
    try:
        from app.workflows.sentinel import run_enforcement
        return await run_enforcement(db, request)
    finally:
        db.close()


@tool(name="sentinel_notify", side_effect_class="notify",
      description="Send governance notifications for a sentinel run: immediate email for "
                  "new HIGH-severity violations (deduped by transition) + an anchored "
                  "once-per-day digest to the governance group.")
async def sentinel_notify(**kwargs) -> Dict[str, Any]:
    request_id = kwargs.get("_request_id")
    db, request = _load_request(request_id)
    if request is None:
        logger.warning("sentinel_notify: no request found for id=%s", request_id)
        return {"notified": False, "reason": "no_request_context"}
    try:
        from app.workflows.sentinel import run_notify
        return await run_notify(db, request)
    finally:
        db.close()


# --------------------------------------------------------------------------
# Job-run + orchestration tools
# --------------------------------------------------------------------------
@tool(name="run_notebook_job", side_effect_class="infra",
      description="Run a Databricks notebook job and return its output.")
async def run_notebook_job(**kwargs) -> Dict[str, Any]:
    provider = _get_databricks_provider()
    result = await provider.submit_job(kwargs.get("notebook_path"), kwargs.get("parameters", {}))
    return {"job_result": result}


class SpawnChildInput(BaseModel):
    child_type: str = Field(..., description="RequestType value of the child workflow")
    parameters: Dict[str, Any] = Field(default_factory=dict)


@tool(name="spawn_child_request", args_schema=SpawnChildInput, side_effect_class="app_write",
      description="[DEPRECATED] Create a child request workflow (orchestrator pattern). "
                  "Use a compound workflow (a 'subworkflow' stage) instead.")
async def spawn_child_request(child_type: str, parameters: Optional[Dict[str, Any]] = None, **kwargs) -> Dict[str, Any]:
    # DEPRECATED: the sibling-spawn model never wired child completion back to the
    # parent (the ``children`` gate's ``all_children_completed`` fact was never
    # written). Compound workflows now compose children as nested subgraphs under
    # one request (see app/workflows/spec.py SubWorkflow + build_spec_graph). This
    # stays a no-op stub so any legacy published spec still validates/loads.
    logger.warning("spawn_child_request is deprecated; use a compound workflow "
                   "(subworkflow stage). type=%s", child_type)
    return {"spawned": child_type, "parameters": parameters or {}}


@tool(name="update_allowlist", side_effect_class="data_grant",
      description="Record an approved governance allowlist exception (policy reprieve).")
async def update_allowlist(**kwargs) -> Dict[str, Any]:
    """Persist an approved allowlist exception so the Sentinel grants a reprieve.

    The ``allowlist_exception`` workflow gates on platform-admin approval *before*
    this step runs, so by the time we get here the exception is approved. Upsert
    a single ``AllowlistModel`` row per originating request to ``approved``.
    Fields not threaded through the graph args (resource_type / workspace /
    expires_at) are read from the request's ``state_context``.
    """
    import uuid
    from datetime import datetime

    request_id = kwargs.get("_request_id")
    db, request = _load_request(request_id)
    if request is None:
        logger.warning("update_allowlist: no request found for id=%s; nothing recorded", request_id)
        return {"allowlist_updated": False, "resource_id": kwargs.get("resource_id")}

    try:
        from app.db.allowlist import AllowlistModel

        ctx = request.state_context or {}
        resource_id = kwargs.get("resource_id") or ctx.get("resource_id")
        if not resource_id:
            logger.warning("update_allowlist: missing resource_id (request=%s); nothing recorded", request_id)
            return {"allowlist_updated": False, "resource_id": None}

        justification = kwargs.get("justification") or ctx.get("justification") or ""
        resource_type = kwargs.get("resource_type") or ctx.get("resource_type") or "unknown"
        workspace = kwargs.get("workspace") or ctx.get("workspace") or ""
        approved_by = ctx.get("approved_by") or kwargs.get("_user_email")

        expires_at = None
        raw_expiry = kwargs.get("expires_at") or ctx.get("expires_at")
        if raw_expiry:
            try:
                expires_at = datetime.fromisoformat(str(raw_expiry).replace("Z", "+00:00"))
            except ValueError:
                logger.warning("update_allowlist: bad expires_at %r; ignoring", raw_expiry)

        entry = (
            db.query(AllowlistModel)
            .filter(AllowlistModel.request_id == request_id)
            .first()
        )
        if entry is None:
            entry = AllowlistModel(
                id=str(uuid.uuid4()),
                resource_id=resource_id,
                resource_type=resource_type,
                workspace=workspace,
                justification=justification,
                request_id=request_id,
            )
            db.add(entry)
        entry.status = "approved"
        entry.approved_by = approved_by
        if expires_at is not None:
            entry.expires_at = expires_at
        if justification:
            entry.justification = justification
        db.commit()

        logger.info("update_allowlist: approved exception resource=%s workspace=%s request=%s",
                    resource_id, workspace, request_id)
        return {"allowlist_updated": True, "allowlist_id": entry.id, "resource_id": resource_id}
    finally:
        db.close()


@tool(name="execute_report", side_effect_class="read",
      description="Run report prompts via the agent and assemble the report body.")
async def execute_report(**kwargs) -> Dict[str, Any]:
    """Run each configured report prompt through the agent and assemble HTML.

    Mirrors the V1 reporting state machine: a read-only ``AgentRunner`` answers
    each prompt with real tool-backed data, the results are stitched into an
    HTML fragment, and ``subject``/``body`` are written to graph context (via
    ``writes_context``) for the downstream ``send_notification`` distribute step.
    Also persisted to ``state_context`` for the UI / audit trail.
    """
    # Test/eval hook: callers (golden transcripts) may inject a canned body.
    if "_report" in kwargs:
        report = kwargs.get("_report", "")
        return {"report": report, "body": report, "subject": "Report"}

    from datetime import datetime
    from zoneinfo import ZoneInfo

    request_id = kwargs.get("_request_id")
    db, request = _load_request(request_id)
    ctx = (request.state_context or {}) if request is not None else {}
    prompts = kwargs.get("prompts") or ctx.get("prompts") or []
    report_name = ctx.get("name", "Report")
    tz = ZoneInfo("America/Los_Angeles")

    try:
        if not prompts:
            logger.warning("execute_report: no prompts configured (request=%s)", request_id)
            body = "<p>No report prompts were configured.</p>"
            return {"report": body, "body": body, "subject": f"Report: {report_name}"}

        from app.agents.runner import AgentRunner
        from app.tools import get_read_only_tools

        system_prompt = (
            "You are a specialized read-only reporting assistant. "
            "Your goal is to fetch real data using your tools and present it clearly. "
            "Always return the final result as a clean HTML snippet (e.g. <table>, <ul>, <p>). "
            "Do not include <html> or <body> tags. "
            "If you cannot find data, state that clearly instead of making it up. "
            f"The current time is {datetime.now(tz).strftime('%Y-%m-%d %H:%M:%S %Z')}."
        )
        runner = AgentRunner(system_prompt=system_prompt, tools=get_read_only_tools())

        results: List[Dict[str, str]] = []
        for p in prompts:
            # Be defensive: a subscription may store a prompt as a bare string
            # instead of the documented {label, prompt} dict. Coerce so a malformed
            # entry never 500s the scheduled report.
            if isinstance(p, str):
                p = {"label": report_name, "prompt": p}
            elif not isinstance(p, dict):
                logger.warning("execute_report: skipping malformed prompt %r (request=%s)", p, request_id)
                continue
            label = p.get("label", "Untitled")
            prompt_text = p.get("prompt", "")
            logger.info("execute_report: running prompt '%s' (request=%s)", label, request_id)
            response = await runner.run(query=prompt_text)
            content = (response.get("content", "") or "").replace("```html", "").replace("```", "").strip()
            results.append({"label": label, "html": content})

        generated_at = datetime.now(tz).strftime('%Y-%m-%d %H:%M:%S %Z')
        sections = "".join(
            f'<div class="report-section" style="margin-bottom: 2rem;">'
            f'<h3 style="color: #444; margin-bottom: 0.5rem;">{r["label"]}</h3>'
            f'<div class="section-content">{r["html"]}</div></div>'
            for r in results
        )
        body = (
            f'<div class="report-header"><h2 style="margin-top: 0;">{report_name}</h2>'
            f'<p style="color: #666; font-size: 0.9rem;">Generated at: {generated_at}</p></div>'
            f'<hr style="border: 0; border-top: 1px solid #eee; margin: 1.5rem 0;" />{sections}'
        )
        subject = f"Report: {report_name}"

        if request is not None:
            from sqlalchemy.orm.attributes import flag_modified
            updated = dict(request.state_context or {})
            updated.update({"report_results": results, "final_report_html": body,
                            "body": body, "subject": subject})
            request.state_context = updated
            flag_modified(request, "state_context")
            db.add(request)
            db.commit()

        logger.info("execute_report: assembled %d section(s) (request=%s)", len(results), request_id)
        return {"report": body, "body": body, "subject": subject, "report_results": results}
    finally:
        if db is not None:
            db.close()
