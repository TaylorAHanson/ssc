"""
Workflows API (no-code authoring).

Admin CRUD + draft/publish for Workflows (DB-backed workflow definitions). Writes
require Platform/Governance Admin; reads are available to any authenticated
user. The agent reads published Workflows through the prompt builder and the
``get_workflow_instructions`` tool, not this API.
"""
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api import deps
from app.core.config import settings
from app.core.feature_flags import is_feature_enabled
from app.models.user import User
from app.services.workflow_service import WorkflowService

logger = logging.getLogger(__name__)

router = APIRouter()

_WRITE_ROLES = ["Platform Admin", "Governance Admin"]


def _require_feature() -> None:
    if not is_feature_enabled("workflow_authoring"):
        raise HTTPException(status_code=404, detail="Workflow authoring is not enabled")


def _require_authoring_unlocked() -> None:
    """Block in-place workflow authoring in a locked environment (e.g. prod).

    When ``WORKFLOW_AUTHORING_LOCKED`` is set, workflows change only via an
    all-or-nothing bundle import (the promotion path). Reads, export, validate,
    and dry-run remain available so admins can still inspect and test.
    """
    if settings.WORKFLOW_AUTHORING_LOCKED:
        raise HTTPException(
            status_code=403,
            detail=(
                "Workflow authoring is locked in this environment. Promote changes "
                "by importing a vetted bundle (Workflows → Import) rather than editing live."
            ),
        )


class WorkflowCreate(BaseModel):
    key: str = Field(..., description="Stable internal name the agent references")
    name: Optional[str] = None
    goal: Optional[str] = None
    instructions_markdown: Optional[str] = None
    allowed_tools: Optional[List[str]] = None
    policy_ref: Optional[str] = None
    params_schema: Optional[Dict[str, Any]] = None
    graph_spec: Optional[Dict[str, Any]] = None
    request_type: Optional[str] = None
    status: str = Field(default="draft", description="draft or published")


class WorkflowUpdate(BaseModel):
    name: Optional[str] = None
    goal: Optional[str] = None
    instructions_markdown: Optional[str] = None
    allowed_tools: Optional[List[str]] = None
    policy_ref: Optional[str] = None
    params_schema: Optional[Dict[str, Any]] = None
    graph_spec: Optional[Dict[str, Any]] = None
    request_type: Optional[str] = None
    status: Optional[str] = None


class SpecValidateRequest(BaseModel):
    graph_spec: Dict[str, Any]


class SpecTestRequest(BaseModel):
    graph_spec: Dict[str, Any]
    sample_context: Optional[Dict[str, Any]] = None


class SpecEvaluateRequest(BaseModel):
    graph_spec: Dict[str, Any]


class RollbackRequest(BaseModel):
    version: int


class ImportRequest(BaseModel):
    bundle: Dict[str, Any]
    as_status: str = Field(default="draft", description="draft or published")
    overwrite: bool = True
    prune: bool = Field(
        default=False,
        description="Also DELETE workflows not present in the bundle (propagates source-env deletions).",
    )


def _validate_graph_spec(spec: Optional[Dict[str, Any]]) -> None:
    """Reject malformed workflow graphs before they are saved/published."""
    if spec is None:
        return
    from app.workflows.spec_loader import SpecError, validate_spec_dict

    try:
        validate_spec_dict(spec)
    except SpecError as e:
        raise HTTPException(status_code=400, detail=f"Invalid graph_spec: {e}")


def _behavioral_publish_gate(spec: Optional[Dict[str, Any]]) -> None:
    """Side-effect-free pre-publish behavioral check.

    Beyond structural validation, this compiles the spec and resolves every
    referenced tool by name via the dry-run projector (no tools run, no DB
    touched). It catches unknown tool names and compile errors that structural
    validation alone misses — the cheap, safe gate we can run inside the live
    API process (the full hermetic harness monkeypatches module globals and must
    never run in-process).
    """
    if spec is None:
        return
    from app.workflows.dry_run import project_run

    try:
        project_run(spec, {})
    except Exception as e:  # noqa: BLE001 - surface as a 400 to the author
        raise HTTPException(
            status_code=400,
            detail=f"graph_spec failed pre-publish check: {e}",
        )


@router.get("")
def list_workflows(
    include_drafts: bool = True,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    _: None = Depends(_require_feature),
) -> Any:
    workflows = WorkflowService.list_workflows(db, include_drafts=include_drafts)
    from app.workflows.evaluator import evaluate_spec

    out = []
    for s in workflows:
        d = WorkflowService.to_dict(s, include_body=False)
        # Attach an at-a-glance advisory evaluation (risk + quality) so the list
        # can badge each workflow without opening it. ``db=None`` keeps this cheap
        # (skips the per-row subworkflow-ref lint); the editor's Evaluate modal
        # runs the full db-backed report.
        spec = s.graph_spec
        evaluation = None
        if spec and spec.get("stages"):
            try:
                rep = evaluate_spec(spec)
                evaluation = {
                    "valid": rep["valid"],
                    "risk": rep["risk"],
                    "quality": rep["quality"],
                    "findings": len(rep["findings"]),
                    "top_severity": rep["findings"][0]["severity"] if rep["findings"] else None,
                }
            except Exception as e:  # noqa: BLE001 - never break the list on a bad spec
                logger.debug("evaluation skipped for workflow %s: %s", s.key, e)
        d["evaluation"] = evaluation
        out.append(d)
    return out


@router.post("")
def create_workflow(
    *,
    db: Session = Depends(deps.get_db),
    body: WorkflowCreate,
    current_user: User = Depends(deps.require_any_role(_WRITE_ROLES)),
    _: None = Depends(_require_feature),
    __: None = Depends(_require_authoring_unlocked),
) -> Any:
    _validate_graph_spec(body.graph_spec)
    try:
        workflow = WorkflowService.create(db, created_by=current_user.email, **body.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return WorkflowService.to_dict(workflow)


@router.get("/{workflow_id}")
def get_workflow(
    workflow_id: str,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    _: None = Depends(_require_feature),
) -> Any:
    workflow = WorkflowService.get(db, workflow_id)
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return WorkflowService.to_dict(workflow)


@router.put("/{workflow_id}")
def update_workflow(
    *,
    workflow_id: str,
    db: Session = Depends(deps.get_db),
    body: WorkflowUpdate,
    current_user: User = Depends(deps.require_any_role(_WRITE_ROLES)),
    _: None = Depends(_require_feature),
    __: None = Depends(_require_authoring_unlocked),
) -> Any:
    if "graph_spec" in body.model_fields_set:
        _validate_graph_spec(body.graph_spec)
    try:
        workflow = WorkflowService.update(db, workflow_id, **body.model_dump(exclude_unset=True))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return WorkflowService.to_dict(workflow)


@router.post("/validate-spec")
def validate_spec(
    *,
    body: SpecValidateRequest,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.require_any_role(_WRITE_ROLES)),
    _: None = Depends(_require_feature),
) -> Any:
    """Author-time check of a workflow graph_spec (used by the editor)."""
    _validate_graph_spec(body.graph_spec)
    from app.tools.authoring.workflow_authoring import _composable_keys
    from app.workflows.spec_loader import lint_step_tool_args, lint_subworkflow_refs

    spec = body.graph_spec or {}
    warnings = lint_step_tool_args(spec) + lint_subworkflow_refs(spec, _composable_keys(db))
    return {"valid": True, "warnings": warnings}


@router.get("/meta/tools")
def list_workflow_tools(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.require_any_role(_WRITE_ROLES)),
    _: None = Depends(_require_feature),
) -> Any:
    """Wireable V2 tools (name + side-effect class) for the workflow editor."""
    from app.workflows.tool_registry import available_tools

    return available_tools(db)


@router.post("/test-spec")
def test_spec(
    *,
    body: SpecTestRequest,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.require_any_role(_WRITE_ROLES)),
    _: None = Depends(_require_feature),
) -> Any:
    """Dry-run a draft workflow against a sample request (no tools run, no DB writes).

    Returns a stage-by-stage projection: which gates auto-approve vs. require a
    human, and the exact arguments each step's tool would receive.
    """
    _validate_graph_spec(body.graph_spec)
    from app.tools.authoring.workflow_authoring import _composable_keys
    from app.workflows.dry_run import project_run
    from app.workflows.spec_loader import lint_step_tool_args, lint_subworkflow_refs

    try:
        projection = project_run(body.graph_spec, body.sample_context or {})
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Dry-run failed: {e}")
    spec = body.graph_spec or {}
    projection["warnings"] = lint_step_tool_args(spec) + lint_subworkflow_refs(
        spec, _composable_keys(db)
    )
    return projection


@router.post("/evaluate-spec")
def evaluate_spec_endpoint(
    *,
    body: SpecEvaluateRequest,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.require_any_role(_WRITE_ROLES)),
    _: None = Depends(_require_feature),
) -> Any:
    """Advisory evaluation of a workflow graph_spec (risk + quality + findings).

    Never blocks anything — it's an author-time signal surfaced in the editor.
    Computed deterministically from the spec (no tools run, no LLM).
    """
    from app.workflows.evaluator import evaluate_spec

    return evaluate_spec(body.graph_spec or {}, db)


@router.post("/{workflow_id}/publish")
def publish_workflow(
    *,
    workflow_id: str,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.require_any_role(_WRITE_ROLES)),
    _: None = Depends(_require_feature),
    __: None = Depends(_require_authoring_unlocked),
) -> Any:
    # A workflow carrying a workflow graph must be valid before it can go live.
    existing = WorkflowService.get(db, workflow_id)
    if existing and existing.graph_spec:
        _validate_graph_spec(existing.graph_spec)
        _behavioral_publish_gate(existing.graph_spec)
    try:
        workflow = WorkflowService.publish(db, workflow_id, published_by=current_user.email)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return WorkflowService.to_dict(workflow)


@router.post("/{workflow_id}/unpublish")
def unpublish_workflow(
    *,
    workflow_id: str,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.require_any_role(_WRITE_ROLES)),
    _: None = Depends(_require_feature),
    __: None = Depends(_require_authoring_unlocked),
) -> Any:
    try:
        workflow = WorkflowService.unpublish(db, workflow_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return WorkflowService.to_dict(workflow)


def _set_disabled(db: Session, workflow_id: str, *, disabled: bool, actor: str) -> Any:
    try:
        workflow = WorkflowService.set_disabled(db, workflow_id, disabled)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    logger.info(
        "Workflow '%s' (%s) %s by %s",
        workflow.key, workflow.id, "disabled" if disabled else "enabled", actor,
    )
    return WorkflowService.to_dict(workflow)


@router.post("/{workflow_id}/disable")
def disable_workflow(
    *,
    workflow_id: str,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.require_any_role(_WRITE_ROLES)),
    _: None = Depends(_require_feature),
) -> Any:
    """Turn a workflow off (operational kill switch).

    Hides the workflow from the agent (capabilities, instructions, execution)
    without changing its definition or version. Deliberately NOT gated by
    ``_require_authoring_unlocked``: turning a workflow off is an operational
    safety action that must stay available even when authoring is locked (prod),
    where unpublish/edit are blocked. Fully reversible via ``/enable``.
    """
    return _set_disabled(db, workflow_id, disabled=True, actor=current_user.email)


@router.post("/{workflow_id}/enable")
def enable_workflow(
    *,
    workflow_id: str,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.require_any_role(_WRITE_ROLES)),
    _: None = Depends(_require_feature),
) -> Any:
    """Turn a previously-disabled workflow back on (see :func:`disable_workflow`)."""
    return _set_disabled(db, workflow_id, disabled=False, actor=current_user.email)


@router.delete("/{workflow_id}")
def delete_workflow(
    *,
    workflow_id: str,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.require_any_role(_WRITE_ROLES)),
    _: None = Depends(_require_feature),
    __: None = Depends(_require_authoring_unlocked),
) -> Any:
    try:
        WorkflowService.delete(db, workflow_id, deleted_by=current_user.email)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"success": True}


# --------------------------------------------------------------------------
# Version history + rollback
# --------------------------------------------------------------------------
@router.get("/{workflow_id}/versions")
def list_workflow_versions(
    workflow_id: str,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.require_any_role(_WRITE_ROLES)),
    _: None = Depends(_require_feature),
) -> Any:
    """Published-version history for a workflow (newest first)."""
    if not WorkflowService.get(db, workflow_id):
        raise HTTPException(status_code=404, detail="Workflow not found")
    return [WorkflowService.version_to_dict(v) for v in WorkflowService.list_versions(db, workflow_id)]


@router.post("/{workflow_id}/rollback")
def rollback_workflow(
    *,
    workflow_id: str,
    body: RollbackRequest,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.require_any_role(_WRITE_ROLES)),
    _: None = Depends(_require_feature),
    __: None = Depends(_require_authoring_unlocked),
) -> Any:
    """Restore a prior published version's body into the workflow as a new draft."""
    try:
        workflow = WorkflowService.rollback(db, workflow_id, body.version)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return WorkflowService.to_dict(workflow)


# --------------------------------------------------------------------------
# Export / import (promote workflows across environments)
# --------------------------------------------------------------------------
@router.get("/export/bundle")
def export_workflows(
    ids: Optional[str] = None,
    published_only: bool = False,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.require_any_role(_WRITE_ROLES)),
    _: None = Depends(_require_feature),
) -> Any:
    """Export workflows as a portable, env-agnostic JSON bundle (keyed by workflow key)."""
    id_list = [i for i in (ids.split(",") if ids else []) if i.strip()]
    return WorkflowService.export_bundle(db, ids=id_list or None, published_only=published_only)


@router.post("/import/bundle")
def import_workflows(
    *,
    body: ImportRequest,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.require_any_role(_WRITE_ROLES)),
    _: None = Depends(_require_feature),
) -> Any:
    """Import a bundle into this environment (upsert by key); defaults to drafts.

    This is intentionally NOT blocked by the authoring lock: in a locked
    environment (prod) an all-or-nothing bundle import is the *only* sanctioned
    way to change workflows. Promote vetted bundles here as ``published``.

    With ``prune=true`` the import also deletes authored/promoted workflows not in
    the bundle (code-seeded ones are protected), so a deletion in the source env
    propagates here. This is destructive and confirmed in the UI before sending.
    """
    try:
        return WorkflowService.import_bundle(
            db, body.bundle, as_status=body.as_status,
            overwrite=body.overwrite, prune=body.prune, created_by=current_user.email,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
