"""
V2 graph-internal tools (the "providers become tools" leg of the thesis).

Each wraps a mutating provider operation as an :class:`~app.tools.mcp.McpTool`
so graph nodes invoke it through the shared ``ToolExecutor`` — getting OPA
pre-flight, idempotency, and audit for free.

They intentionally live OUTSIDE ``app.tools/`` so auto-discovery (``load_tools``)
does NOT register them as chat-agent tools: a raw ``add_group_membership`` /
``terraform_apply`` must not be directly callable by the conversational agent
until per-workflow capability scoping (M3) exists. Graphs import them explicitly.

Provider access goes through small ``_get_*_provider`` getters so the eval
harness can monkeypatch them with fakes (no live Databricks/GitHub needed).

All workflow tools are modularized in submodules and re-exported here for
clean backwards-compatibility.
"""
from typing import Any
import sys
from app.workflows.tools import _common as _common_mod
from app.workflows.tools._common import (
    _get_databricks_provider,
    _get_github_provider,
    _get_gitops_provider,
    _get_identity_provider,
    _get_notification_provider,
    _get_terramate_provider,
    _load_request,
)


class _ToolsModule(type(sys)):
    """Custom module class that intercepts setattr on app.workflows.tools
    so that monkeypatching getter attributes (e.g. T._get_github_provider = fake)
    automatically syncs to app.workflows.tools._common.
    """
    def __setattr__(self, name: str, value: Any):
        if name in (
            "_get_databricks_provider",
            "_get_github_provider",
            "_get_gitops_provider",
            "_get_identity_provider",
            "_get_notification_provider",
            "_get_terramate_provider",
        ):
            setattr(_common_mod, name, value)
        super().__setattr__(name, value)


sys.modules[__name__].__class__ = _ToolsModule


from app.workflows.tools.approver_groups import resolve_owner_groups_from_assets
from app.workflows.tools.code_review import (
    ReviewAppCodeInput,
    review_databricks_app_code,
)
from app.workflows.tools.github import (
    GithubAccessInput,
    GithubAddTeamMembersInput,
    GithubCreateTeamInput,
    GithubGrantTeamRepoInput,
    GithubRepoInput,
    github_add_team_members,
    github_create_repo,
    github_create_team,
    github_grant_team_repo,
    github_set_permissions,
)
from app.workflows.tools.infra import (
    CreateSpInput,
    CreateUcObjectInput,
    TerramateCheckStatusInput,
    TerramatePollStatusInput,
    TerramateProvisionInput,
    TerramateResourceType,
    TerramateSubmitInput,
    create_service_principal,
    create_uc_object,
    terraform_apply,
    terraform_plan,
    terramate_check_status,
    terramate_poll_status,
    terramate_provision,
    terramate_submit_request,
)
from app.workflows.tools.jobs import (
    SpawnChildInput,
    execute_report,
    run_notebook_job,
    spawn_child_request,
    update_allowlist,
)
from app.workflows.tools.membership import (
    GroupMembershipInput,
    NotifyInput,
    add_group_membership,
    send_notification,
)
from app.workflows.tools.preview_features import (
    assess_preview_feature,
    set_preview_setting,
    set_preview_target_status,
    verify_preview_setting,
)
from app.workflows.tools.metadata import (
    prepare_metadata_change,
    record_metadata_change,
)
from app.workflows.tools.rbac import (
    RENAMED_TOOLS as _RBAC_RENAMED_TOOLS,
    rbac_prepare_access_change,
    record_access_change,
)
from app.workflows.tools.sentinel import (
    sentinel_discover,
    sentinel_enforce,
    sentinel_notify,
)

RENAMED_TOOLS = {**_RBAC_RENAMED_TOOLS}

__all__ = [
    # Common getters / helpers
    "_get_databricks_provider",
    "_get_github_provider",
    "_get_gitops_provider",
    "_get_identity_provider",
    "_get_notification_provider",
    "_get_terramate_provider",
    "_load_request",
    # Code review
    "ReviewAppCodeInput",
    "review_databricks_app_code",
    # Approver resolution
    "resolve_owner_groups_from_assets",
    # Infra & Terramate & Terraform
    "CreateSpInput",
    "CreateUcObjectInput",
    "TerramateCheckStatusInput",
    "TerramatePollStatusInput",
    "TerramateProvisionInput",
    "TerramateResourceType",
    "TerramateSubmitInput",
    "create_service_principal",
    "create_uc_object",
    "terraform_apply",
    "terraform_plan",
    "terramate_check_status",
    "terramate_poll_status",
    "terramate_provision",
    "terramate_submit_request",
    # GitHub & Tags
    "GithubAccessInput",
    "GithubAddTeamMembersInput",
    "GithubCreateTeamInput",
    "GithubGrantTeamRepoInput",
    "GithubRepoInput",
    "github_add_team_members",
    "github_create_repo",
    "github_create_team",
    "github_grant_team_repo",
    "github_set_permissions",
    # Membership & Notifications
    "GroupMembershipInput",
    "NotifyInput",
    "add_group_membership",
    "send_notification",
    # Preview features
    "assess_preview_feature",
    "set_preview_setting",
    "set_preview_target_status",
    "verify_preview_setting",
    # Access Rules requests
    "rbac_prepare_access_change",
    "record_access_change",
    # Metadata change requests
    "prepare_metadata_change",
    "record_metadata_change",
    # Former tool names (old -> current), resolved by the tool registry
    "RENAMED_TOOLS",
    # Sentinel
    "sentinel_discover",
    "sentinel_enforce",
    "sentinel_notify",
    # Jobs & Reports
    "SpawnChildInput",
    "execute_report",
    "run_notebook_job",
    "spawn_child_request",
    "update_allowlist",
]
