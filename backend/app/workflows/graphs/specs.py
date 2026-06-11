"""
Serializable workflow-spec catalog (the no-code "workflows as data" source).

Every workflow is described as a **JSON-able dict** (see :mod:`app.workflows.spec_loader`
for the schema and :mod:`app.workflows.expr` for the expression mini-language). The
dicts are the single source of truth: they compile to runtime ``WorkflowSpec``s
(and durable LangGraph graphs) via :func:`spec_from_dict`, *and* they are what the
seed writes into a Workflow's ``graph_spec`` so an admin can edit a workflow's gates
and provisioning steps in the UI instead of changing this file and redeploying.

Every executable workflow lives here as data — including data access, whose
runtime multi-owner resolution is now expressed with a ``resolve_data_owners``
step + a gate ``approvers_from`` expression rather than a dedicated code graph.
"""
from app.models.request import RequestType
from app.workflows.spec import build_spec_graph
from app.workflows.spec_loader import spec_from_dict, stage_specs_from_dict

# Reusable expression fragments (kept readable; these are plain JSON).
_PARAMS = {"$ctx": True}                                  # the whole context dict
_REQ_ID = {"$var": "request_id"}


def _var(path, default=None):
    return {"$var": {"path": path, "default": default}} if default is not None else {"$var": path}


# Normalized asset list for data-access flows: prefer ctx["assets"], else
# synthesize a single-element list from the flat asset_name/asset_type fields
# (mirrors the old data_access graph's ``_assets`` helper).
_ASSETS = {
    "$coalesce": [
        {"$var": "assets"},
        {"$list": [{"$obj": {"asset_name": {"$var": "asset_name"},
                             "asset_type": {"$var": "asset_type"}}}]},
    ]
}


def _data_access_spec(name: str) -> dict:
    """Data-access workflow as data: resolve owners -> data-owner gate -> grant.

    Replaces the former dedicated ``graphs/data_access.py`` code graph. The
    runtime owner discovery is a ``resolve_data_owners`` step whose result is
    lifted into context (``writes_context``) so the gate's ``approvers_from``
    can route the approval to the resolved owner group(s). Grants fan out one
    per asset via ``for_each``, each through the governed ToolExecutor.
    """
    return {
        "name": name,
        # The grant step writes the access_granted fact (parity with the old
        # graph, which emitted it at provision time), so no complete_fact here.
        "stages": [
            {"kind": "step", "name": "resolve_owners", "tool": "resolve_data_owners",
             "running_status": "data_owner_approval",
             "writes_context": ["data_owners"],
             "args": {"assets": _ASSETS, "data_owners": {"$var": "data_owners"}}},
            {"kind": "gate", "name": "await_approval", "type": "data_owner",
             "waiting_status": "data_owner_approval",
             "approvers_from": {"$var": "data_owners"}},
            {"kind": "step", "name": "provision", "tool": "grant_uc_access",
             "approvals": ["data_owner"], "success_fact": "access_granted",
             "for_each": _ASSETS,
             "item_args": {"asset_type": {"$item": "asset_type"},
                           "asset_name": {"$item": "asset_name"},
                           "principal": {"$var": "requested_by_email"},
                           "access_level": {"$var": "access_level"}}},
        ],
    }


# --------------------------------------------------------------------------
# The catalog: RequestType.value -> spec dict
# --------------------------------------------------------------------------
SPECS = {
    RequestType.WORKSPACE_ACCESS.value: {
        "name": "workspace_access",
        "complete_fact": "access_granted",
        "stages": [
            {"kind": "gate", "name": "manager_approval", "type": "manager",
             "waiting_status": "manager_approval",
             "auto_approve": {"$or": [{"$var": "is_auto_approve"},
                                      {"$eq": [{"$var": "scope"}, "enterprise"]}]}},
            {"kind": "step", "name": "provision", "tool": "add_group_membership",
             "approvals": ["manager"], "success_fact": "access_granted",
             "args": {"group": {"$coalesce": [{"$var": "access_group"}, {"$var": "workspace"}]},
                      "members": {"$list": [{"$var": "requested_by_email"}]}}},
        ],
    },
    RequestType.SERVICE_PRINCIPAL.value: {
        "name": "service_principal",
        "complete_fact": "terraform_apply_success",
        "stages": [
            {"kind": "step", "name": "plan", "tool": "terraform_plan",
             "args": {"request_id": _REQ_ID, "parameters": _PARAMS}},
            {"kind": "gate", "name": "platform_admin_approval", "type": "platform_admin",
             "waiting_status": "manager_approval"},
            {"kind": "step", "name": "apply", "tool": "create_service_principal",
             "approvals": ["platform_admin"], "success_fact": "terraform_apply_success",
             "args": {"display_name": _var("display_name", "sp"), "parameters": _PARAMS}},
        ],
    },
    RequestType.WORKSPACE_PROVISION.value: {
        "name": "workspace_provision",
        "complete_fact": "terraform_apply_success",
        "stages": [
            {"kind": "gate", "name": "manager_approval", "type": "manager",
             "waiting_status": "manager_approval"},
            {"kind": "gate", "name": "training_pending", "type": "training",
             "waiting_status": "training_pending",
             "auto_approve": {"$not": {"$var": "requires_training"}}},
            {"kind": "step", "name": "plan", "tool": "terraform_plan",
             "args": {"request_id": _REQ_ID, "parameters": _PARAMS}},
            {"kind": "gate", "name": "awaiting_admin_approval", "type": "platform_admin",
             "waiting_status": "manager_approval"},
            {"kind": "step", "name": "apply", "tool": "terraform_apply",
             "approvals": ["platform_admin"], "success_fact": "terraform_apply_success",
             "args": {"request_id": _REQ_ID, "parameters": _PARAMS}},
        ],
    },
    RequestType.CATALOG_SCHEMA_TABLE.value: {
        "name": "catalog_schema_table",
        "complete_fact": "terraform_apply_success",
        "stages": [
            {"kind": "step", "name": "plan", "tool": "terraform_plan",
             "args": {"request_id": _REQ_ID, "parameters": _PARAMS}},
            {"kind": "gate", "name": "awaiting_approval", "type": "platform_admin",
             "waiting_status": "manager_approval"},
            {"kind": "step", "name": "apply", "tool": "terraform_apply",
             "approvals": ["platform_admin"], "success_fact": "terraform_apply_success",
             "args": {"request_id": _REQ_ID, "parameters": _PARAMS}},
        ],
    },
    RequestType.VOLUME_CREATION.value: {
        "name": "volume_creation",
        "complete_fact": "terraform_apply_success",
        "stages": [
            {"kind": "gate", "name": "manager_approval", "type": "manager",
             "waiting_status": "manager_approval"},
            {"kind": "step", "name": "plan", "tool": "terraform_plan",
             "args": {"request_id": _REQ_ID, "parameters": _PARAMS}},
            {"kind": "gate", "name": "awaiting_approval", "type": "platform_admin",
             "waiting_status": "manager_approval"},
            {"kind": "step", "name": "apply", "tool": "terraform_apply",
             "approvals": ["platform_admin"], "success_fact": "terraform_apply_success",
             "args": {"request_id": _REQ_ID, "parameters": _PARAMS}},
        ],
    },
    RequestType.CREDENTIAL_CREATION.value: {
        "name": "credential_creation",
        "complete_fact": "provisioning_completed",
        "stages": [
            {"kind": "gate", "name": "manager_approval", "type": "manager",
             "waiting_status": "manager_approval"},
            {"kind": "gate", "name": "platform_admin_approval", "type": "platform_admin",
             "waiting_status": "manager_approval"},
            {"kind": "step", "name": "provision", "tool": "create_uc_object",
             "approvals": ["platform_admin"], "success_fact": "provisioning_completed",
             "args": {"object_type": "credential", "name": _var("name", ""), "parameters": _PARAMS}},
        ],
    },
    RequestType.WORKSPACE_FOLDER_CREATION.value: {
        "name": "workspace_folder_creation",
        "complete_fact": "provisioning_completed",
        "stages": [
            {"kind": "gate", "name": "manager_approval", "type": "manager",
             "waiting_status": "manager_approval"},
            {"kind": "step", "name": "provision", "tool": "create_uc_object",
             "approvals": ["manager"], "success_fact": "provisioning_completed",
             "args": {"object_type": "folder",
                      "name": {"$var": {"path": "path", "default": _var("name", "")}},
                      "parameters": _PARAMS}},
        ],
    },
    RequestType.TAG_CREATION.value: {
        "name": "tag_creation",
        "complete_fact": "provisioning_completed",
        "stages": [
            {"kind": "gate", "name": "manager_approval", "type": "manager",
             "waiting_status": "manager_approval"},
            {"kind": "step", "name": "provision", "tool": "create_uc_object",
             "approvals": ["manager"], "success_fact": "provisioning_completed",
             "args": {"object_type": "tag",
                      "name": {"$var": {"path": "tag_key", "default": _var("name", "")}},
                      "parameters": _PARAMS}},
        ],
    },
    RequestType.GITHUB_REPO_CREATION.value: {
        "name": "github_repo_creation",
        "complete_fact": "repo_created",
        "stages": [
            {"kind": "step", "name": "provision", "tool": "github_create_repo",
             "success_fact": "repo_created",
             "args": {"repo_name": _var("repo_name", ""), "template": {"$var": "template"},
                      "description": {"$var": "description"},
                      "visibility": _var("visibility", "private")}},
        ],
    },
    RequestType.GITHUB_REPO_ACCESS.value: {
        "name": "github_repo_access",
        "complete_fact": "provisioning_completed",
        "stages": [
            {"kind": "gate", "name": "manager_approval", "type": "manager",
             "waiting_status": "manager_approval"},
            {"kind": "gate", "name": "data_owner_approval", "type": "data_owner",
             "waiting_status": "data_owner_approval"},
            {"kind": "step", "name": "provision", "tool": "github_set_permissions",
             "approvals": ["data_owner"], "success_fact": "provisioning_completed",
             "args": {"repo_name": _var("repo_name", ""),
                      "username": _var("github_username", ""),
                      "permission": _var("permission", "push")}},
        ],
    },
    RequestType.REST_API_ACCESS.value: {
        "name": "rest_api_access",
        "complete_fact": "workspace_created",
        "stages": [
            {"kind": "gate", "name": "platform_admin_approval", "type": "platform_admin",
             "waiting_status": "manager_approval"},
            {"kind": "step", "name": "provision", "tool": "create_uc_object",
             "approvals": ["platform_admin"], "success_fact": "workspace_created",
             "args": {"object_type": "rest_api_access", "name": _var("name", ""), "parameters": _PARAMS}},
        ],
    },
    RequestType.ALLOWLIST_EXCEPTION.value: {
        "name": "allowlist_exception",
        "complete_fact": "allowlist_updated",
        "stages": [
            {"kind": "gate", "name": "platform_admin_approval", "type": "platform_admin",
             "waiting_status": "manager_approval"},
            {"kind": "step", "name": "update", "tool": "update_allowlist",
             "approvals": ["platform_admin"], "success_fact": "allowlist_updated",
             "args": {"resource_id": {"$var": "resource_id"}, "justification": {"$var": "justification"}}},
        ],
    },
    RequestType.TAG_CHANGE.value: {
        "name": "tag_change",
        "complete_fact": "pr_merged",
        "stages": [
            {"kind": "step", "name": "open_pr", "tool": "open_tag_change_pr",
             "args": {"title": _var("title", "Tag change"), "body": _var("justification", "")}},
            {"kind": "gate", "name": "pr_open", "type": "pr_merge", "waiting_status": "provisioning"},
        ],
    },
    RequestType.REUSABLE_ASSETS.value: {"name": "reusable_assets", "stages": []},
    RequestType.TRAINING_LINKS.value: {"name": "training_links", "stages": []},
    RequestType.TRAINING_VERIFICATION.value: {
        "name": "training_verification",
        "complete_fact": "verification_completed",
        "stages": [
            {"kind": "gate", "name": "verifying", "type": "training",
             "waiting_status": "training_pending"},
        ],
    },
    RequestType.SIMPLE_EMAIL.value: {
        "name": "simple_email",
        "stages": [
            {"kind": "step", "name": "send", "tool": "send_notification",
             "args": {"subject": _var("subject", ""), "body": _var("body", ""),
                      "to_email": {"$var": "to_email"}}},
        ],
    },
    RequestType.CAMPAIGN.value: {
        "name": "campaign",
        "complete_fact": "campaign_finished",
        "stages": [
            {"kind": "gate", "name": "training_pending", "type": "training",
             "waiting_status": "training_pending",
             "auto_approve": {"$not": {"$var": "requires_training"}}},
            {"kind": "step", "name": "spawn", "tool": "spawn_child_request",
             "for_each": {"$coalesce": [{"$var": "recipients"}, {"$list": [None]}]},
             "item_args": {"child_type": "simple_email",
                           "parameters": {"$obj": {"to_email": {"$item": True},
                                                   "subject": _var("subject", "")}}}},
            {"kind": "gate", "name": "await_children", "type": "children",
             "waiting_status": "provisioning"},
        ],
    },
    RequestType.PROJECT_ONBOARDING.value: {
        "name": "project_onboarding",
        "complete_fact": "all_children_completed",
        "stages": [
            {"kind": "gate", "name": "manager_approval", "type": "manager",
             "waiting_status": "manager_approval"},
            {"kind": "step", "name": "spawn", "tool": "spawn_child_request",
             "approvals": ["manager"],
             "for_each": {"$coalesce": [{"$var": "children"},
                                        [{"child_type": "workspace_provision", "parameters": {}}]]},
             "item_args": {"child_type": {"$item": "child_type"},
                           "parameters": {"$item": {"path": "parameters", "default": {}}}}},
            {"kind": "gate", "name": "await_children", "type": "children",
             "waiting_status": "provisioning"},
        ],
    },
    RequestType.ENFORCEMENT_SENTINEL.value: {
        "name": "enforcement_sentinel",
        "complete_fact": "notify_completed",
        "stages": [
            {"kind": "step", "name": "discover", "tool": "sentinel_discover",
             "args": {"scope": {"$var": "scope"}}},
            {"kind": "step", "name": "enforce", "tool": "sentinel_enforce",
             "args": {"enforcement_mode": _var("enforcement_mode", "audit_only")}},
            {"kind": "step", "name": "notify", "tool": "send_notification",
             "success_fact": "notify_completed",
             "args": {"subject": "Enforcement run complete", "body": _var("summary", "")}},
        ],
    },
    RequestType.REPORT_EXECUTION.value: {
        "name": "report_execution",
        "complete_fact": "distribution_completed",
        "stages": [
            {"kind": "step", "name": "execute", "tool": "execute_report",
             "args": {"prompts": {"$var": {"path": "prompts", "default": []}}}},
            {"kind": "step", "name": "distribute", "tool": "send_notification",
             "success_fact": "distribution_completed",
             "args": {"subject": _var("subject", "Report"), "body": _var("body", "")}},
        ],
    },
    RequestType.ASSET_DEDUPLICATION.value: {
        "name": "asset_deduplication",
        "complete_fact": "notify_completed",
        "stages": [
            {"kind": "step", "name": "run_job", "tool": "run_notebook_job",
             "args": {"notebook_path": {"$var": "notebook_path"}, "parameters": _PARAMS}},
            {"kind": "step", "name": "notify", "tool": "send_notification",
             "success_fact": "notify_completed",
             "args": {"subject": "Deduplication complete", "body": _var("summary", "")}},
        ],
    },
    # Data access (was a dedicated code graph): one spec, three request types.
    RequestType.DATA_ACCESS_REQUEST.value: _data_access_spec("data_access"),
    RequestType.CATALOG_SCHEMA_TABLE_ACCESS.value: _data_access_spec("catalog_schema_table_access"),
    RequestType.BATCH_DATA_ACCESS.value: _data_access_spec("batch_data_access"),
}


def make_spec_builder(spec_dict):
    """Wrap a spec dict into a no-arg graph builder for the registry."""
    def builder():
        return build_spec_graph(spec_from_dict(spec_dict))
    return builder


# RequestType.value -> no-arg graph builder. Every type is spec-generated.
SPEC_FACTORIES = {rt: make_spec_builder(spec) for rt, spec in SPECS.items()}


def ui_stage_ids(request_type) -> list:
    """Ordered UI states for a request type: pending -> stages... -> completed."""
    key = getattr(request_type, "value", request_type)
    stage_names = [s["name"] for s in SPECS[key].get("stages", [])] if key in SPECS else []
    return ["pending"] + stage_names + ["completed"]


def stage_specs(request_type) -> list:
    """Introspect a type's stages for the UI renderer.

    Returns ordered dicts: ``{name, kind: gate|step, gate_type, success_fact}``.
    """
    key = getattr(request_type, "value", request_type)
    if key in SPECS:
        return stage_specs_from_dict(SPECS[key])
    return []


def editable_states(request_type) -> list:
    """Stages from which a platform_admin may Edit & Restart (platform_admin gates)."""
    key = getattr(request_type, "value", request_type)
    if key not in SPECS:
        return []
    return [s["name"] for s in SPECS[key].get("stages", [])
            if s.get("kind") == "gate" and s.get("type") == "platform_admin"]
