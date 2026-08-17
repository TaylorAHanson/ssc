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
    # Optimistic concurrency: the ``updated_at`` the client loaded. When present
    # and stale, the write is refused with a 409 instead of silently winning —
    # the authoring assistant saves the same rows the editor does, so
    # last-write-wins here means one of them loses work without being told.
    if_unmodified_since: Optional[str] = Field(
        default=None,
        description="The workflow's updated_at as last read by the client. 409 if it has changed since.",
    )


class SpecValidateRequest(BaseModel):
    graph_spec: Dict[str, Any]


class SpecTestRequest(BaseModel):
    graph_spec: Dict[str, Any]
    sample_context: Optional[Dict[str, Any]] = None


class SpecEvaluateRequest(BaseModel):
    graph_spec: Dict[str, Any]
    # Scored alongside the graph when provided: the runtime agent follows this
    # text, so "is this workflow good?" isn't answerable from the graph alone.
    instructions_markdown: Optional[str] = None
    # The goal is the workflow's whole line in the runtime Capabilities menu, so
    # it's scored against the published catalog it will compete with. ``key``
    # keeps a workflow from colliding with its own published row.
    goal: Optional[str] = None
    key: Optional[str] = None


class GenerateInstructionsRequest(BaseModel):
    graph_spec: Optional[Dict[str, Any]] = None
    request_type: Optional[str] = None
    goal: Optional[str] = None
    name: Optional[str] = None
    # When given, the generator is told to improve rather than replace: the
    # author's existing wording is the starting point.
    existing_instructions: Optional[str] = None


class WorkflowTestUpsert(BaseModel):
    name: Optional[str] = None
    question: Optional[str] = None
    expected_outcome: Optional[str] = None
    enabled: Optional[bool] = None


class GenerateTestsRequest(BaseModel):
    count: int = Field(default=5, ge=1, le=6)
    # When false (default) the proposals are returned for the author to edit and
    # nothing is written: generated tests are a starting point, not a fait accompli.
    save: bool = False


class RunTestsRequest(BaseModel):
    # Omit to run every enabled case.
    test_ids: Optional[List[str]] = None


class RollbackRequest(BaseModel):
    # Either identifier works; ``snapshot_id`` is required to restore an autosave
    # backup, since those share the version number they were based on.
    version: Optional[int] = None
    snapshot_id: Optional[str] = None


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


def _tests_publish_gate(db: Session, workflow_id: str) -> None:
    """Optionally refuse to publish while behavioral tests aren't passing.

    Off by default: the judge is non-deterministic, so a hard block would strand
    authors whose workflow is fine. When an operator turns it on, "never run"
    blocks too — an untested workflow is exactly what the setting is for.
    """
    if not getattr(settings, "WORKFLOW_TESTS_BLOCK_PUBLISH", False):
        return
    from app.services.workflow_test_service import WorkflowTestService

    health = WorkflowTestService.health(db, workflow_id)
    if health["total"] == 0:
        raise HTTPException(
            status_code=400,
            detail=(
                "This environment requires passing behavioral tests before publishing, "
                "and this workflow has none. Add cases in the Tests tab and run them."
            ),
        )
    if not health["ready"]:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Behavioral tests are not passing: {health['passing']} of "
                f"{health['total']} passing, {health['failing']} failing, "
                f"{health['never_run']} never run. Fix or re-run them in the Tests tab."
            ),
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
    from app.services.workflow_test_service import WorkflowTestService

    # Health, not just structure: risk/quality say whether the GRAPH is sound, but a
    # workflow can score well and still be a stub nobody verified. Attach test
    # posture and last-publish time in bulk so a gap is visible in the list, before
    # someone finds it mid-demo.
    ids = [s.id for s in workflows]
    tests_health = WorkflowTestService.health_map(db, ids)
    last_published = WorkflowService.last_published_map(db, ids)

    out = []
    for s in workflows:
        d = WorkflowService.to_dict(s, include_body=False)
        d["tests_health"] = tests_health.get(s.id)
        d["last_published_at"] = last_published.get(s.id)
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
    fields = body.model_dump()
    # Same reasoning as update: a workflow only goes live through POST /publish, so
    # it can't be created already-published and skip the pre-publish gate.
    if fields.get("status") == "published":
        raise HTTPException(
            status_code=400,
            detail=(
                "Create the workflow as a draft, then Publish it — publishing runs the "
                "pre-publish checks and snapshots a version for rollback."
            ),
        )
    try:
        workflow = WorkflowService.create(db, created_by=current_user.email, **fields)
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

    existing = WorkflowService.get(db, workflow_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Workflow not found")

    fields = body.model_dump(exclude_unset=True)
    expected = fields.pop("if_unmodified_since", None)
    if expected:
        current = existing.updated_at.isoformat() if existing.updated_at else None
        # Compare as prefixes: clients round-trip the ISO string we served, but
        # JSON/JS date handling can drop sub-second precision.
        if current and not (current.startswith(expected) or expected.startswith(current)):
            raise HTTPException(
                status_code=409,
                detail=(
                    "This workflow changed since you loaded it (someone else, or the "
                    "authoring assistant, saved it). Reload to see the current version "
                    "before saving again."
                ),
            )

    # Publishing must go through POST /publish, which runs the structural +
    # behavioral gate, requires a request_type, bumps the version, and snapshots
    # an immutable version for rollback. Allowing status="published" here was a
    # way to make a workflow live with none of that.
    if fields.get("status") == "published" and existing.status != "published":
        raise HTTPException(
            status_code=400,
            detail=(
                "Use Publish to make a workflow live — it runs the pre-publish checks "
                "and snapshots a version you can roll back to. Saving with "
                'status="published" would skip both.'
            ),
        )

    try:
        workflow = WorkflowService.update(db, workflow_id, **fields)
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


@router.post("/generate-instructions")
async def generate_instructions_endpoint(
    *,
    body: GenerateInstructionsRequest,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.require_any_role(_WRITE_ROLES)),
    _: None = Depends(_require_feature),
) -> Any:
    """Author (or improve) the runtime playbook for a draft workflow.

    The studio had no way to generate instructions at all — the only path was the
    authoring assistant, so an admin working in the editor could publish a
    workflow whose runtime prompt was a thin generated stub without ever being
    told. Falls back to the deterministic baseline if the LLM is unavailable, so
    this endpoint can't be the reason instructions end up blank.
    """
    from app.workflows.instructions_generator import generate_instructions

    catalog_context = ""
    if body.existing_instructions and body.existing_instructions.strip():
        catalog_context = (
            "EXISTING INSTRUCTIONS THE ADMIN HAS ALREADY WRITTEN — improve and extend "
            "these rather than replacing them; keep their wording and decisions where "
            "they still apply:\n"
            "```markdown\n"
            f"{body.existing_instructions.strip()[:8000]}\n"
            "```\n"
        )

    result = await generate_instructions(
        body.graph_spec,
        request_type=body.request_type,
        goal=body.goal,
        name=body.name,
        catalog_context=catalog_context,
    )
    from app.workflows.instructions_quality import score_instructions

    result["quality"] = score_instructions(
        result["instructions_markdown"], body.graph_spec
    )
    return result


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
    """Advisory evaluation of a workflow (risk + quality + instructions + findings).

    Never blocks anything — it's an author-time signal surfaced in the editor.
    Computed deterministically from the spec (no tools run, no LLM).
    """
    from app.workflows.evaluator import evaluate_spec
    from app.workflows.instructions_quality import score_instructions

    report = evaluate_spec(body.graph_spec or {}, db)
    # Third dimension: the playbook the runtime agent actually follows. Only
    # scored when the caller sends it, so existing callers are unaffected.
    if body.instructions_markdown is not None:
        report["instructions"] = score_instructions(
            body.instructions_markdown, body.graph_spec
        )
    # Fourth: the Capabilities menu line the runtime agent routes from, judged
    # against the published workflows it competes with.
    if body.goal is not None:
        from app.workflows.goal_quality import menu_siblings, score_goal

        report["goal"] = score_goal(
            body.goal,
            key=body.key or "",
            name=(body.graph_spec or {}).get("name"),
            siblings=menu_siblings(
                WorkflowService.list_published(db), exclude_key=body.key or "",
            ),
        )
    return report


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
    _tests_publish_gate(db, workflow_id)
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
    """Snapshot history for a workflow (newest first).

    Includes both published versions and the autosave backups taken before a
    draft was overwritten (e.g. by an authoring-assistant save).
    """
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
    """Restore a snapshot's body into the workflow as a draft.

    ``snapshot_id`` restores one specific snapshot (the only way to reach an
    autosave backup, since those share a version number); ``version`` restores a
    published version.
    """
    try:
        if body.snapshot_id:
            workflow = WorkflowService.restore_snapshot(db, workflow_id, body.snapshot_id)
        elif body.version is not None:
            workflow = WorkflowService.rollback(db, workflow_id, body.version)
        else:
            raise HTTPException(
                status_code=400, detail="Provide either snapshot_id or version.",
            )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return WorkflowService.to_dict(workflow)


# --------------------------------------------------------------------------
# Tests (run the real agent in a sandbox, judged against plain-English expectations)
# --------------------------------------------------------------------------
def _require_tests_enabled() -> None:
    if not getattr(settings, "WORKFLOW_TESTS_ENABLED", True):
        raise HTTPException(
            status_code=403,
            detail=(
                "Workflow tests are disabled in this environment "
                "(Admin → Settings → Workflow tests)."
            ),
        )


def _get_workflow_or_404(db: Session, workflow_id: str):
    workflow = WorkflowService.get(db, workflow_id)
    if workflow is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return workflow


@router.get("/{workflow_id}/tests")
def list_workflow_tests(
    *,
    workflow_id: str,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.require_any_role(_WRITE_ROLES)),
    _: None = Depends(_require_feature),
) -> Any:
    """A workflow's test cases plus the latest run for each, and overall health."""
    from app.services.workflow_test_service import (
        WorkflowTestService, run_to_dict, test_to_dict,
    )

    _get_workflow_or_404(db, workflow_id)
    cases = WorkflowTestService.list_tests(db, workflow_id)
    latest = WorkflowTestService.latest_runs(db, workflow_id)
    return {
        "tests": [
            {
                **test_to_dict(case),
                "latest_run": (
                    run_to_dict(latest[case.id], include_transcript=False)
                    if case.id in latest else None
                ),
            }
            for case in cases
        ],
        "health": WorkflowTestService.health(db, workflow_id),
        "enabled": bool(getattr(settings, "WORKFLOW_TESTS_ENABLED", True)),
        "blocks_publish": bool(getattr(settings, "WORKFLOW_TESTS_BLOCK_PUBLISH", False)),
    }


@router.post("/{workflow_id}/tests")
def create_workflow_test(
    *,
    workflow_id: str,
    body: WorkflowTestUpsert,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.require_any_role(_WRITE_ROLES)),
    _: None = Depends(_require_feature),
    __: None = Depends(_require_authoring_unlocked),
) -> Any:
    from app.services.workflow_test_service import WorkflowTestService, test_to_dict

    _get_workflow_or_404(db, workflow_id)
    if not (body.question or "").strip() or not (body.expected_outcome or "").strip():
        raise HTTPException(
            status_code=400,
            detail="A case needs both a question and an expected outcome to be judged.",
        )
    case = WorkflowTestService.create_test(
        db, workflow_id,
        name=body.name or "",
        question=body.question or "",
        expected_outcome=body.expected_outcome or "",
        enabled=True if body.enabled is None else body.enabled,
        source="user",
        created_by=current_user.email,
    )
    return test_to_dict(case)


@router.put("/{workflow_id}/tests/{test_id}")
def update_workflow_test(
    *,
    workflow_id: str,
    test_id: str,
    body: WorkflowTestUpsert,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.require_any_role(_WRITE_ROLES)),
    _: None = Depends(_require_feature),
    __: None = Depends(_require_authoring_unlocked),
) -> Any:
    from app.services.workflow_test_service import WorkflowTestService, test_to_dict

    case = WorkflowTestService.get_test(db, test_id)
    if case is None or case.workflow_id != workflow_id:
        raise HTTPException(status_code=404, detail="Test case not found")
    updated = WorkflowTestService.update_test(db, test_id, body.model_dump(exclude_none=True))
    return test_to_dict(updated)


@router.delete("/{workflow_id}/tests/{test_id}")
def delete_workflow_test(
    *,
    workflow_id: str,
    test_id: str,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.require_any_role(_WRITE_ROLES)),
    _: None = Depends(_require_feature),
    __: None = Depends(_require_authoring_unlocked),
) -> Any:
    from app.services.workflow_test_service import WorkflowTestService

    case = WorkflowTestService.get_test(db, test_id)
    if case is None or case.workflow_id != workflow_id:
        raise HTTPException(status_code=404, detail="Test case not found")
    WorkflowTestService.delete_test(db, test_id)
    return {"status": "deleted", "id": test_id}


@router.post("/{workflow_id}/tests/generate")
async def generate_workflow_tests(
    *,
    workflow_id: str,
    body: GenerateTestsRequest,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.require_any_role(_WRITE_ROLES)),
    _: None = Depends(_require_feature),
    __: None = Depends(_require_tests_enabled),
) -> Any:
    """Propose cases for this workflow.

    Returns proposals for the author to edit; nothing is persisted unless
    ``save`` is set, and hand-written cases are never replaced.
    """
    from app.services.workflow_test_service import WorkflowTestService, test_to_dict
    from app.workflows.test_generator import generate_test_cases

    workflow = _get_workflow_or_404(db, workflow_id)
    result = await generate_test_cases(
        workflow.graph_spec,
        request_type=workflow.request_type or workflow.key,
        name=workflow.name,
        goal=workflow.goal,
        instructions_markdown=workflow.instructions_markdown,
        count=body.count or 5,
    )
    if body.save:
        _require_authoring_unlocked()
        saved = WorkflowTestService.replace_tests(
            db, workflow_id, result["cases"],
            source="agent", created_by=current_user.email,
        )
        result["tests"] = [test_to_dict(row) for row in saved]
    return result


@router.post("/{workflow_id}/tests/run")
def run_workflow_tests(
    *,
    workflow_id: str,
    body: RunTestsRequest,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.require_any_role(_WRITE_ROLES)),
    _: None = Depends(_require_feature),
    __: None = Depends(_require_tests_enabled),
) -> Any:
    """Start a sandboxed agent run for the given cases (all enabled ones by default).

    Returns immediately with a ``run_group_id`` to poll: each case is a full agent
    conversation, far too slow to hold a request open for.
    """
    from app.services.workflow_test_service import (
        WorkflowTestService, run_to_dict,
    )
    from app.workflows.test_runner import run_group_in_thread

    _get_workflow_or_404(db, workflow_id)
    cases = WorkflowTestService.list_tests(db, workflow_id)
    if body.test_ids:
        wanted = set(body.test_ids)
        cases = [c for c in cases if c.id in wanted]
    else:
        cases = [c for c in cases if c.enabled]
    if not cases:
        raise HTTPException(
            status_code=400,
            detail="No enabled test cases to run. Add a case (or let the assistant propose some) first.",
        )

    # This endpoint invokes the agent, so it is rate-limited per admin on top of
    # the role gate and the sandbox — a loop over Run all is otherwise an easy way
    # to burn a model budget.
    limit = int(getattr(settings, "WORKFLOW_TEST_RUNS_PER_HOUR", 60) or 60)
    recent = WorkflowTestService.recent_run_count(db, current_user.email)
    if recent + len(cases) > limit:
        raise HTTPException(
            status_code=429,
            detail=(
                f"Test run limit reached ({recent} of {limit} cases in the last hour). "
                f"Wait a few minutes or raise the limit in Admin → Settings → Workflow tests."
            ),
        )

    group_id, runs = WorkflowTestService.create_run_group(
        db, workflow_id, cases, triggered_by=current_user.email,
    )
    run_group_in_thread(group_id)
    logger.info(
        "Workflow tests: %s started %d case(s) for workflow %s (group %s)",
        current_user.email, len(runs), workflow_id, group_id,
    )
    return {
        "run_group_id": group_id,
        "runs": [run_to_dict(r, include_transcript=False) for r in runs],
    }


@router.get("/{workflow_id}/tests/runs/{group_id}")
def get_workflow_test_run_group(
    *,
    workflow_id: str,
    group_id: str,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.require_any_role(_WRITE_ROLES)),
    _: None = Depends(_require_feature),
) -> Any:
    """Poll one run group. Transcripts are included so a verdict is reviewable."""
    from app.services.workflow_test_service import (
        WorkflowTestService, run_to_dict,
    )

    runs = WorkflowTestService.get_run_group(db, group_id)
    runs = [r for r in runs if r.workflow_id == workflow_id]
    if not runs:
        raise HTTPException(status_code=404, detail="Test run not found")
    return {
        "run_group_id": group_id,
        "done": all(r.status in ("complete", "error") for r in runs),
        "runs": [run_to_dict(r) for r in runs],
        "health": WorkflowTestService.health(db, workflow_id),
    }


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
