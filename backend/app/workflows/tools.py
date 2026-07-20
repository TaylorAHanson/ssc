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


@tool(name="open_tag_change_pr", side_effect_class="infra",
      description="Open a GitOps pull request that applies a UC tag change on merge.")
async def open_tag_change_pr(**kwargs) -> Dict[str, Any]:
    provider = _get_github_provider()
    pr = await provider.create_pull_request(
        title=kwargs.get("title", "Tag change"),
        body=kwargs.get("body", ""),
        head=kwargs.get("head", ""),
        base=kwargs.get("base", "main"),
    )
    return {"pull_request": pr}


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
    provider = _get_identity_provider()
    result = await provider.list_members_add(group, members)
    return {"group": group, "members": members, "result": result}


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
    """
    if not request_id:
        return None, None
    from app.db import RequestModel
    from app.db.session import get_db

    db = next(get_db())
    request = db.query(RequestModel).filter(RequestModel.id == request_id).first()
    if request is None:
        db.close()
        return None, None
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
        return await run_discovery(db, request)
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
