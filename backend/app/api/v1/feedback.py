"""
Feedback API: submit feedback / feature requests / bug reports, and let admins
triage them.

- Submitting is open to any authenticated user.
- Listing / viewing / updating / deleting is restricted to admins.

Gated by the ``feedback`` feature flag.
"""
import logging
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api import deps
from app.core.feature_flags import is_feature_enabled
from app.models.user import User
from app.services.feedback_service import FeedbackService

logger = logging.getLogger(__name__)

router = APIRouter()

_ADMIN_ROLES = ["Platform Admin", "Governance Admin"]


def _require_feature() -> None:
    if not is_feature_enabled("feedback"):
        raise HTTPException(status_code=404, detail="Feedback is not enabled")


class ConsoleEntry(BaseModel):
    level: Optional[str] = None
    message: Optional[str] = None
    ts: Optional[str] = None


class NetworkEntry(BaseModel):
    method: Optional[str] = None
    url: Optional[str] = None
    status: Optional[int] = None
    status_text: Optional[str] = None
    ts: Optional[str] = None


class FeedbackCreate(BaseModel):
    type: str = Field(..., description="bug | feature | feedback")
    title: str = Field(..., min_length=1, max_length=300)
    description: Optional[str] = Field(default=None)
    severity: Optional[str] = Field(default=None, description="For bugs: low|medium|high|critical")
    source: str = Field(default="web", description="web | chat")
    page_url: Optional[str] = None
    user_agent: Optional[str] = None
    app_version: Optional[str] = None
    console_logs: Optional[List[ConsoleEntry]] = None
    network_errors: Optional[List[NetworkEntry]] = None


class FeedbackUpdate(BaseModel):
    status: Optional[str] = Field(default=None, description="open|in_progress|resolved|closed|wont_fix")
    admin_notes: Optional[str] = None
    severity: Optional[str] = None


@router.post("")
def submit_feedback(
    *,
    db: Session = Depends(deps.get_db),
    body: FeedbackCreate,
    current_user: User = Depends(deps.get_current_user),
    _: None = Depends(_require_feature),
) -> Any:
    """Submit feedback. Available to any authenticated user."""
    try:
        fb = FeedbackService.create_feedback(
            db,
            type=body.type,
            title=body.title,
            description=body.description,
            severity=body.severity,
            source=body.source,
            submitted_by=current_user.email,
            submitted_by_name=current_user.full_name,
            page_url=body.page_url,
            user_agent=body.user_agent,
            app_version=body.app_version,
            console_logs=[c.model_dump() for c in body.console_logs] if body.console_logs else None,
            network_errors=[n.model_dump() for n in body.network_errors] if body.network_errors else None,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return FeedbackService.to_dict(fb)


@router.get("")
def list_feedback(
    *,
    db: Session = Depends(deps.get_db),
    type: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    current_user: User = Depends(deps.require_any_role(_ADMIN_ROLES)),
    _: None = Depends(_require_feature),
) -> Any:
    """List feedback (admins only). Lightweight — diagnostics omitted."""
    items = FeedbackService.list_feedback(db, type=type, status=status)
    return [FeedbackService.to_dict(fb, include_diagnostics=False) for fb in items]


@router.get("/{feedback_id}")
def get_feedback(
    *,
    db: Session = Depends(deps.get_db),
    feedback_id: str,
    current_user: User = Depends(deps.require_any_role(_ADMIN_ROLES)),
    _: None = Depends(_require_feature),
) -> Any:
    """Get one feedback item with full diagnostics (admins only)."""
    fb = FeedbackService.get_feedback(db, feedback_id)
    if not fb:
        raise HTTPException(status_code=404, detail="Feedback not found")
    return FeedbackService.to_dict(fb, include_diagnostics=True)


@router.patch("/{feedback_id}")
def update_feedback(
    *,
    db: Session = Depends(deps.get_db),
    feedback_id: str,
    body: FeedbackUpdate,
    current_user: User = Depends(deps.require_any_role(_ADMIN_ROLES)),
    _: None = Depends(_require_feature),
) -> Any:
    """Update status / notes / severity (admins only)."""
    try:
        fb = FeedbackService.update_feedback(
            db,
            feedback_id,
            status=body.status,
            admin_notes=body.admin_notes,
            severity=body.severity,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not fb:
        raise HTTPException(status_code=404, detail="Feedback not found")
    return FeedbackService.to_dict(fb, include_diagnostics=True)


@router.delete("/{feedback_id}")
def delete_feedback(
    *,
    db: Session = Depends(deps.get_db),
    feedback_id: str,
    current_user: User = Depends(deps.require_any_role(_ADMIN_ROLES)),
    _: None = Depends(_require_feature),
) -> Any:
    """Delete a feedback item (admins only)."""
    ok = FeedbackService.delete_feedback(db, feedback_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Feedback not found")
    return {"success": True}
