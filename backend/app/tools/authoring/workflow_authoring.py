"""Workflow (Workflow) authoring tools for the agent.

Admin-only building blocks so the agent can co-author no-code workflows in chat:
discover the available step tools and gate types, inspect an existing workflow,
validate / preview (dry-run) a candidate graph, and — only on explicit admin
confirmation — save a draft or publish.

Design notes:
  * All gated with ``required_role="Governance Admin"`` (Platform Admins always
    pass; Governance Admins match by name; others are filtered out upstream).
  * Read tools never mutate. ``save_workflow_draft`` / ``publish_workflow`` are
    ``app_write`` (DB-only), so they route through the governed ToolExecutor and
    are audited but don't trigger infra approval gates.
  * The same validators the API uses are reused (``validate_spec_dict`` +
    ``project_run``) so what the agent checks == what the editor/publish enforce.
"""
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.tools.mcp import tool

_AUTHOR_ROLE = "Governance Admin"

# Returned by the write tools when the environment locks in-place authoring
# (WORKFLOW_AUTHORING_LOCKED, e.g. prod). The agent surfaces this to the admin.
_LOCKED_MSG = (
    "Workflow authoring is locked in this environment. Workflows can only be changed "
    "by importing a vetted bundle (promotion), not edited live here. Build and publish "
    "in a lower environment, then promote."
)


def _authoring_locked() -> bool:
    from app.core.config import settings

    return bool(settings.WORKFLOW_AUTHORING_LOCKED)

# Gate kinds + expression operators the spec language supports, surfaced to the
# agent so it can author specs without guessing (mirrors spec_loader.GATE_TYPES
# and expr's operator set).
_GATE_TYPES = ["manager", "platform_admin", "data_owner", "training", "pr_merge", "children"]
_EXPR_OPS = [
    "$var (ctx field, dotted paths, optional default)",
    "$item (for_each item, only in item_args/for_each)",
    "$ctx (whole context)", "$literal (value as-is)",
    "$eq/$ne/$in [a,b]", "$and/$or [..]", "$not a", "$bool a",
    "$coalesce [..] (first truthy)", "$obj {k: expr}", "$list [expr,..]",
]


def _db():
    from app.db.session import get_db
    return next(get_db())


# --------------------------------------------------------------------------
# Read tools
# --------------------------------------------------------------------------
@tool(
    name="list_workflow_building_blocks",
    description=(
        "List the building blocks for authoring a no-code workflow (Workflow): the "
        "available step TOOLS (with side-effect class + whether they mutate), the "
        "GATE types, and the expression operators. Call this FIRST when helping an "
        "admin design or edit a workflow so you wire steps to real tools."
    ),
    required_role=_AUTHOR_ROLE,
    friendly_label="Loading workflow building blocks...",
)
async def list_workflow_building_blocks() -> Dict[str, Any]:
    from app.v2.tool_registry import available_tools

    return {
        "step_tools": available_tools(),
        "gate_types": _GATE_TYPES,
        "expression_operators": _EXPR_OPS,
        "spec_shape": {
            "name": "str (required)",
            "complete_fact": "optional fact written on completion",
            "stages": "ordered list of gate/step objects",
            "gate": {"kind": "gate", "name": "str", "type": "one of gate_types",
                     "waiting_status": "optional", "auto_approve": "optional expression -> bool"},
            "step": {"kind": "step", "name": "str", "tool": "a step_tools name",
                     "args": "object of name -> expression", "approvals": "list of prior gate types",
                     "success_fact": "optional", "for_each": "optional expression -> list",
                     "item_args": "object (per-item), uses $item",
                     "run_if": "optional expression -> bool; when false the step is SKIPPED "
                               "(conditional branching). Omit it to always run."},
        },
        "note": (
            "Reserved stage names: complete, rejected, pending, completed. A step that "
            "runs after a gate should list that gate's type in 'approvals' so policy "
            "enforcement sees the approval. Use a step's 'run_if' for conditional "
            "branching (e.g. only notify security when tier == 'high'). Consult the "
            "Context Catalog guide ('workflow authoring') for finicky-tool guidance "
            "before publishing."
        ),
    }


class GetWorkflowInput(BaseModel):
    key: str = Field(..., description="The workflow key (workflow identifier), e.g. 'workspace_access'.")


@tool(
    name="get_workflow",
    description=(
        "Fetch an existing workflow (Workflow) by key, including its current graph_spec, "
        "status (draft/published), request_type, and metadata. Use this to inspect a "
        "workflow before editing it."
    ),
    required_role=_AUTHOR_ROLE,
    args_schema=GetWorkflowInput,
    friendly_label="Loading workflow...",
)
async def get_workflow(key: str) -> Dict[str, Any]:
    from app.services.workflow_service import WorkflowService

    db = _db()
    try:
        workflow = WorkflowService.get_by_key(db, key)
        if not workflow:
            existing = [s.key for s in WorkflowService.list_workflows(db)]
            return {"found": False,
                    "error": f"No workflow with key '{key}'.",
                    "available_keys": existing}
        return {
            "found": True,
            "key": workflow.key,
            "name": workflow.name,
            "status": workflow.status,
            "version": workflow.version,
            "request_type": workflow.request_type,
            "goal": workflow.goal,
            "graph_spec": workflow.graph_spec,
        }
    finally:
        db.close()


class ValidateSpecInput(BaseModel):
    graph_spec: Dict[str, Any] = Field(..., description="The candidate workflow graph_spec (JSON object with name + stages).")


@tool(
    name="validate_workflow_spec",
    description=(
        "Structurally validate a candidate workflow graph_spec WITHOUT saving it: "
        "checks stage shapes, gate types, that each step's tool exists, and that all "
        "expressions are well-formed. Returns {valid, error}. Always validate before "
        "saving or publishing."
    ),
    required_role=_AUTHOR_ROLE,
    args_schema=ValidateSpecInput,
    friendly_label="Validating workflow...",
)
async def validate_workflow_spec(graph_spec: Dict[str, Any]) -> Dict[str, Any]:
    from app.v2.spec_loader import SpecError, lint_step_tool_args, validate_spec_dict

    try:
        validate_spec_dict(graph_spec)
    except SpecError as e:
        return {"valid": False, "error": str(e)}
    # Structurally valid — surface non-blocking arg-name lint so the author
    # catches wrong/missing tool args (which **kwargs would otherwise swallow).
    warnings = lint_step_tool_args(graph_spec)
    return {"valid": True, "warnings": warnings}


class PreviewSpecInput(BaseModel):
    graph_spec: Dict[str, Any] = Field(..., description="The candidate workflow graph_spec to project.")
    sample_context: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Sample request context (the fields a real request would carry) to evaluate expressions against.",
    )


@tool(
    name="preview_workflow_spec",
    description=(
        "Dry-run a candidate workflow graph_spec against a sample request context "
        "WITHOUT running any tool or writing anything. Projects which gates auto-approve, "
        "the exact args each step would receive, and fan-out — so you can confirm behavior "
        "with the admin before saving/publishing."
    ),
    required_role=_AUTHOR_ROLE,
    args_schema=PreviewSpecInput,
    friendly_label="Previewing workflow...",
)
async def preview_workflow_spec(
    graph_spec: Dict[str, Any], sample_context: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    from app.v2.dry_run import project_run
    from app.v2.spec_loader import lint_step_tool_args

    try:
        projection = project_run(graph_spec, sample_context or {})
    except Exception as e:  # noqa: BLE001 - surface to the agent so it can fix the spec
        return {"ok": False, "error": str(e)}
    return {"ok": True, "projection": projection, "warnings": lint_step_tool_args(graph_spec)}


# --------------------------------------------------------------------------
# Mutating tools (DB-only; governed + audited via the ToolExecutor)
# --------------------------------------------------------------------------
class SaveDraftInput(BaseModel):
    key: str = Field(..., description="Workflow key. Created if new, updated (as a draft) if it exists.")
    graph_spec: Dict[str, Any] = Field(..., description="The workflow graph_spec to save.")
    name: Optional[str] = Field(default=None, description="Human-friendly workflow name (defaults to key).")
    request_type: Optional[str] = Field(
        default=None,
        description="The RequestType value this workflow governs (required for it to run; can be set before publish).",
    )
    goal: Optional[str] = Field(default=None, description="Optional one-line description of the workflow's goal.")
    instructions_markdown: Optional[str] = Field(
        default=None,
        description=(
            "Optional markdown the self-service agent follows at runtime (the goal, what to "
            "gather from the user, and how to format the execute_workflow call). If omitted, a "
            "baseline is auto-generated from the graph_spec so instructions are never blank."
        ),
    )


@tool(
    name="save_workflow_draft",
    description=(
        "Save a workflow graph_spec as a DRAFT (creates the Workflow if new, else updates it "
        "and marks it draft). Validates the spec first and refuses to save an invalid one. "
        "Auto-generates baseline runtime instructions from the spec when none are supplied. "
        "Does NOT publish — the workflow won't affect live requests until published. Only "
        "call after the admin has reviewed a validated, previewed spec."
    ),
    required_role=_AUTHOR_ROLE,
    args_schema=SaveDraftInput,
    side_effect_class="app_write",
    friendly_label="Saving workflow draft...",
    friendly_completion_label="Workflow draft saved",
)
async def save_workflow_draft(
    key: str,
    graph_spec: Dict[str, Any],
    name: Optional[str] = None,
    request_type: Optional[str] = None,
    goal: Optional[str] = None,
    instructions_markdown: Optional[str] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    from app.services.workflow_service import WorkflowService
    from app.v2.instructions import render_instructions_markdown
    from app.v2.spec_loader import SpecError, lint_step_tool_args, validate_spec_dict

    if _authoring_locked():
        return {"ok": False, "locked": True, "error": _LOCKED_MSG}

    try:
        validate_spec_dict(graph_spec)
    except SpecError as e:
        return {"ok": False, "error": f"Invalid graph_spec, not saved: {e}"}

    arg_warnings = lint_step_tool_args(graph_spec)

    actor = kwargs.get("_user_email")
    db = _db()
    try:
        existing = WorkflowService.get_by_key(db, key)
        fields: Dict[str, Any] = {"graph_spec": graph_spec, "status": "draft"}
        if name:
            fields["name"] = name
        if request_type:
            fields["request_type"] = request_type
        if goal:
            fields["goal"] = goal
        # Runtime instructions: honor an explicit value, otherwise auto-derive a
        # baseline from the spec so the self-service agent never gets a blank
        # (the #1 cause of "the workflow does nothing when I run it").
        if instructions_markdown is not None:
            fields["instructions_markdown"] = instructions_markdown
        elif not (existing and existing.instructions_markdown):
            fields["instructions_markdown"] = render_instructions_markdown(
                graph_spec,
                request_type=request_type or (existing.request_type if existing else None),
                goal=goal or (existing.goal if existing else None),
            )
        if existing:
            workflow = WorkflowService.update(db, existing.id, **fields)
            action = "updated"
        else:
            workflow = WorkflowService.create(db, created_by=actor, key=key, **fields)
            action = "created"
        warnings: List[str] = list(arg_warnings)
        if not workflow.request_type:
            warnings.append("No request_type set — set one before publishing or the graph won't run.")
        return {
            "ok": True,
            "action": action,
            "key": workflow.key,
            "status": workflow.status,
            "version": workflow.version,
            "request_type": workflow.request_type,
            "warnings": warnings,
            "note": "Saved as a draft. Publish it (publish_workflow) to make it live.",
        }
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    finally:
        db.close()


class PublishWorkflowInput(BaseModel):
    key: str = Field(..., description="Workflow key to publish. Must already exist as a draft with a valid graph_spec.")


@tool(
    name="publish_workflow",
    description=(
        "Publish a workflow (Workflow) so it governs live requests of its request_type. Runs "
        "the full pre-publish gate (structural validation + compiles the spec + resolves every "
        "tool) and refuses to publish an invalid one. This is consequential — only call after "
        "the admin EXPLICITLY confirms they want it live. Snapshots an immutable version for rollback."
    ),
    required_role=_AUTHOR_ROLE,
    args_schema=PublishWorkflowInput,
    side_effect_class="app_write",
    friendly_label="Publishing workflow...",
    friendly_completion_label="Workflow published",
)
async def publish_workflow(key: str, **kwargs: Any) -> Dict[str, Any]:
    from app.services.workflow_service import WorkflowService
    from app.v2.dry_run import project_run
    from app.v2.spec_loader import validate_spec_dict

    if _authoring_locked():
        return {"ok": False, "locked": True, "error": _LOCKED_MSG}

    actor = kwargs.get("_user_email")
    db = _db()
    try:
        workflow = WorkflowService.get_by_key(db, key)
        if not workflow:
            return {"ok": False, "error": f"No workflow with key '{key}' to publish."}
        if not workflow.graph_spec:
            return {"ok": False, "error": f"Workflow '{key}' has no graph_spec to publish."}
        try:
            validate_spec_dict(workflow.graph_spec)
            project_run(workflow.graph_spec, {})  # compiles + resolves every tool, side-effect free
        except Exception as e:  # noqa: BLE001 - structural/compile errors block publish
            return {"ok": False, "error": f"Pre-publish check failed, not published: {e}"}
        if not workflow.request_type:
            return {"ok": False,
                    "error": "No request_type set on this workflow; set one (save_workflow_draft) before publishing."}
        published = WorkflowService.publish(db, workflow.id, published_by=actor)
        return {
            "ok": True,
            "key": published.key,
            "status": published.status,
            "version": published.version,
            "request_type": published.request_type,
            "note": "Published and live. A version snapshot was saved for rollback.",
        }
    finally:
        db.close()
