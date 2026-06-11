"""
Skills API (no-code authoring).

Admin CRUD + draft/publish for Skills (DB-backed workflow definitions). Writes
require Platform/Governance Admin; reads are available to any authenticated
user. The agent reads published Skills through the prompt builder and the
``get_workflow_instructions`` tool, not this API.
"""
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api import deps
from app.core.feature_flags import is_feature_enabled
from app.models.user import User
from app.services.skill_service import SkillService

logger = logging.getLogger(__name__)

router = APIRouter()

_WRITE_ROLES = ["Platform Admin", "Governance Admin"]


def _require_feature() -> None:
    if not is_feature_enabled("skill_authoring"):
        raise HTTPException(status_code=404, detail="Skill authoring is not enabled")


class SkillCreate(BaseModel):
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


class SkillUpdate(BaseModel):
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


class RollbackRequest(BaseModel):
    version: int


class ImportRequest(BaseModel):
    bundle: Dict[str, Any]
    as_status: str = Field(default="draft", description="draft or published")
    overwrite: bool = True


def _validate_graph_spec(spec: Optional[Dict[str, Any]]) -> None:
    """Reject malformed workflow graphs before they are saved/published."""
    if spec is None:
        return
    from app.v2.spec_loader import SpecError, validate_spec_dict

    try:
        validate_spec_dict(spec)
    except SpecError as e:
        raise HTTPException(status_code=400, detail=f"Invalid graph_spec: {e}")


@router.get("")
def list_skills(
    include_drafts: bool = True,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    _: None = Depends(_require_feature),
) -> Any:
    skills = SkillService.list_skills(db, include_drafts=include_drafts)
    return [SkillService.to_dict(s, include_body=False) for s in skills]


@router.post("")
def create_skill(
    *,
    db: Session = Depends(deps.get_db),
    body: SkillCreate,
    current_user: User = Depends(deps.require_any_role(_WRITE_ROLES)),
    _: None = Depends(_require_feature),
) -> Any:
    _validate_graph_spec(body.graph_spec)
    try:
        skill = SkillService.create(db, created_by=current_user.email, **body.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return SkillService.to_dict(skill)


@router.get("/{skill_id}")
def get_skill(
    skill_id: str,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    _: None = Depends(_require_feature),
) -> Any:
    skill = SkillService.get(db, skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    return SkillService.to_dict(skill)


@router.put("/{skill_id}")
def update_skill(
    *,
    skill_id: str,
    db: Session = Depends(deps.get_db),
    body: SkillUpdate,
    current_user: User = Depends(deps.require_any_role(_WRITE_ROLES)),
    _: None = Depends(_require_feature),
) -> Any:
    if "graph_spec" in body.model_fields_set:
        _validate_graph_spec(body.graph_spec)
    try:
        skill = SkillService.update(db, skill_id, **body.model_dump(exclude_unset=True))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return SkillService.to_dict(skill)


@router.post("/validate-spec")
def validate_spec(
    *,
    body: SpecValidateRequest,
    current_user: User = Depends(deps.require_any_role(_WRITE_ROLES)),
    _: None = Depends(_require_feature),
) -> Any:
    """Author-time check of a workflow graph_spec (used by the editor)."""
    _validate_graph_spec(body.graph_spec)
    return {"valid": True}


@router.get("/meta/tools")
def list_workflow_tools(
    current_user: User = Depends(deps.require_any_role(_WRITE_ROLES)),
    _: None = Depends(_require_feature),
) -> Any:
    """Wireable V2 tools (name + side-effect class) for the workflow editor."""
    from app.v2.tool_registry import available_tools

    return available_tools()


@router.post("/test-spec")
def test_spec(
    *,
    body: SpecTestRequest,
    current_user: User = Depends(deps.require_any_role(_WRITE_ROLES)),
    _: None = Depends(_require_feature),
) -> Any:
    """Dry-run a draft workflow against a sample request (no tools run, no DB writes).

    Returns a stage-by-stage projection: which gates auto-approve vs. require a
    human, and the exact arguments each step's tool would receive.
    """
    _validate_graph_spec(body.graph_spec)
    from app.v2.dry_run import project_run

    try:
        return project_run(body.graph_spec, body.sample_context or {})
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Dry-run failed: {e}")


@router.post("/{skill_id}/publish")
def publish_skill(
    *,
    skill_id: str,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.require_any_role(_WRITE_ROLES)),
    _: None = Depends(_require_feature),
) -> Any:
    # A skill carrying a workflow graph must be valid before it can go live.
    existing = SkillService.get(db, skill_id)
    if existing and existing.graph_spec:
        _validate_graph_spec(existing.graph_spec)
    try:
        skill = SkillService.publish(db, skill_id, published_by=current_user.email)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return SkillService.to_dict(skill)


@router.post("/{skill_id}/unpublish")
def unpublish_skill(
    *,
    skill_id: str,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.require_any_role(_WRITE_ROLES)),
    _: None = Depends(_require_feature),
) -> Any:
    try:
        skill = SkillService.unpublish(db, skill_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return SkillService.to_dict(skill)


@router.delete("/{skill_id}")
def delete_skill(
    *,
    skill_id: str,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.require_any_role(_WRITE_ROLES)),
    _: None = Depends(_require_feature),
) -> Any:
    try:
        SkillService.delete(db, skill_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"success": True}


# --------------------------------------------------------------------------
# Version history + rollback
# --------------------------------------------------------------------------
@router.get("/{skill_id}/versions")
def list_skill_versions(
    skill_id: str,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.require_any_role(_WRITE_ROLES)),
    _: None = Depends(_require_feature),
) -> Any:
    """Published-version history for a skill (newest first)."""
    if not SkillService.get(db, skill_id):
        raise HTTPException(status_code=404, detail="Skill not found")
    return [SkillService.version_to_dict(v) for v in SkillService.list_versions(db, skill_id)]


@router.post("/{skill_id}/rollback")
def rollback_skill(
    *,
    skill_id: str,
    body: RollbackRequest,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.require_any_role(_WRITE_ROLES)),
    _: None = Depends(_require_feature),
) -> Any:
    """Restore a prior published version's body into the skill as a new draft."""
    try:
        skill = SkillService.rollback(db, skill_id, body.version)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return SkillService.to_dict(skill)


# --------------------------------------------------------------------------
# Export / import (promote workflows across environments)
# --------------------------------------------------------------------------
@router.get("/export/bundle")
def export_skills(
    ids: Optional[str] = None,
    published_only: bool = False,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.require_any_role(_WRITE_ROLES)),
    _: None = Depends(_require_feature),
) -> Any:
    """Export skills as a portable, env-agnostic JSON bundle (keyed by skill key)."""
    id_list = [i for i in (ids.split(",") if ids else []) if i.strip()]
    return SkillService.export_bundle(db, ids=id_list or None, published_only=published_only)


@router.post("/import/bundle")
def import_skills(
    *,
    body: ImportRequest,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.require_any_role(_WRITE_ROLES)),
    _: None = Depends(_require_feature),
) -> Any:
    """Import a bundle into this environment (upsert by key); defaults to drafts."""
    try:
        return SkillService.import_bundle(
            db, body.bundle, as_status=body.as_status,
            overwrite=body.overwrite, created_by=current_user.email,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
