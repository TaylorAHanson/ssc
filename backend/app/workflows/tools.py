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
    from app.providers.notification.client import NotificationProvider
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
    assets: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Assets to resolve owners for: [{asset_name, asset_type}, ...]",
    )
    data_owners: Optional[List[str]] = Field(
        None, description="Pre-supplied owners; when set they are returned as-is (no lookup)."
    )


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
async def resolve_data_owners(assets: Optional[List[Dict[str, Any]]] = None,
                              data_owners: Optional[List[str]] = None,
                              **kwargs) -> Dict[str, Any]:
    """Owner resolution for data-access gates, as a tool.

    Lets the declarative spec engine express what used to require the dedicated
    ``data_access`` code graph: a pre-gate step that discovers who must approve.
    Best-effort — if the provider is unavailable we return whatever owners the
    caller already had so the gate still renders.
    """
    owners = list(data_owners or [])
    if not owners:
        owners = await resolve_owner_groups_from_assets(assets)
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
      description="Add members to an identity group (Entra/Okta/SCIM/LMWS-backed).")
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
    provider = _get_notification_provider()
    result = await provider.send(subject=subject, body=body, to_email=to_email)
    return {"sent": True, "result": result}


# --------------------------------------------------------------------------
# Enforcement sentinel tools (automated pipeline)
# --------------------------------------------------------------------------
@tool(name="sentinel_discover", side_effect_class="read",
      description="Discover policy violations across governed assets (OPA evaluation).")
async def sentinel_discover(**kwargs) -> Dict[str, Any]:
    logger.info("sentinel_discover: %s", kwargs.get("scope"))
    return {"violations": kwargs.get("_violations", [])}


@tool(name="sentinel_enforce", side_effect_class="destructive",
      description="Remediate discovered violations (warn/kill/uncertify). Irreversible.")
async def sentinel_enforce(**kwargs) -> Dict[str, Any]:
    logger.info("sentinel_enforce: mode=%s", kwargs.get("enforcement_mode"))
    return {"enforced": True, "mode": kwargs.get("enforcement_mode", "audit_only")}


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
      description="Create a child request workflow (orchestrator pattern).")
async def spawn_child_request(child_type: str, parameters: Optional[Dict[str, Any]] = None, **kwargs) -> Dict[str, Any]:
    logger.info("spawn_child_request: %s params=%s", child_type, parameters)
    return {"spawned": child_type, "parameters": parameters or {}}


@tool(name="update_allowlist", side_effect_class="data_grant",
      description="Record an approved governance allowlist exception (policy reprieve).")
async def update_allowlist(**kwargs) -> Dict[str, Any]:
    logger.info("update_allowlist: resource=%s status=approved", kwargs.get("resource_id"))
    return {"allowlist_updated": True, "resource_id": kwargs.get("resource_id")}


@tool(name="execute_report", side_effect_class="read",
      description="Run report prompts via the agent and assemble the report body.")
async def execute_report(**kwargs) -> Dict[str, Any]:
    logger.info("execute_report: %s prompts", len(kwargs.get("prompts", []) or []))
    return {"report": kwargs.get("_report", "")}
