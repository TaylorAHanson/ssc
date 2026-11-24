"""
Context Catalog document freshness API: mark reviewed, the catalog-wide
"needs review" list, and the Teams reminder status / run-now / request-review /
test-message actions for admins.
"""
import logging
from typing import Any, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api import deps
from app.api.v1.context_catalog import _WRITE_CAPABILITY, _require_feature
from app.db.context_catalog import ContextDocumentModel
from app.models.user import User
from app.services import context_catalog_staleness as staleness
from app.services.context_catalog_service import ContextCatalogService

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/documents/{document_id}/review")
def mark_document_reviewed(
    document_id: str,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
    _: None = Depends(_require_feature),
) -> Any:
    """Confirm a document is still accurate without editing it.

    Allowed for catalog admins and for the document's owner (or, when it has
    none, its domain's primary owner).
    """
    document = ContextCatalogService.get_document(db, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    if not current_user.can(_WRITE_CAPABILITY):
        owner, _source = staleness.effective_owner(
            document, ContextCatalogService.get_domain(db, document.domain_id)
        )
        if not owner or owner != staleness.normalize_owner(current_user.email):
            raise HTTPException(
                status_code=403,
                detail="Only the document's owner or a catalog admin can mark it reviewed",
            )
    document = staleness.mark_reviewed(db, document_id, current_user.email)
    return ContextCatalogService.document_to_dict(document)


@router.get("/review-queue")
def get_review_queue(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.require_capability(_WRITE_CAPABILITY)),
    _: None = Depends(_require_feature),
) -> Any:
    """Published documents that are stale or never marked reviewed, with their domain and the reason."""
    return staleness.documents_needing_review(db)


@router.get("/staleness")
def get_staleness_status(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.require_capability(_WRITE_CAPABILITY)),
    _: None = Depends(_require_feature),
) -> Any:
    """Reminder configuration (never the webhook URL) and every stale published document."""
    return staleness.reminder_status(db)


@router.post("/staleness/run")
def run_stale_reminders_now(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.require_capability(_WRITE_CAPABILITY)),
    _: None = Depends(_require_feature),
) -> Any:
    """Post reminders now (respects the re-notify interval; ignores the on/off toggle)."""
    logger.info("Context Catalog stale reminders run requested by %s", current_user.email)
    return staleness.run_stale_reminders(db, trigger="manual")


class ReviewRequestIn(BaseModel):
    # Omit (or null) for every published document that is stale or never reviewed.
    document_ids: Optional[List[str]] = Field(default=None, max_length=1000)


@router.post("/staleness/request-review")
def request_document_reviews(
    payload: Optional[ReviewRequestIn] = Body(default=None),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.require_capability(_WRITE_CAPABILITY)),
    _: None = Depends(_require_feature),
) -> Any:
    """Ask owners in Teams to review documents now, whatever the reminder schedule.

    No body: every published document that is stale or has never been marked
    reviewed, skipping ones reminded within the re-notify interval (use this to
    start a first round of ownership). ``document_ids``: exactly those
    documents, in any state, even if they were reminded recently.
    """
    ids = payload.document_ids if payload else None
    if ids is not None:
        if not ids:
            raise HTTPException(status_code=422, detail="document_ids is empty")
        known = {
            row.id for row in db.query(ContextDocumentModel.id).filter(ContextDocumentModel.id.in_(ids)).all()
        }
        missing = sorted(set(ids) - known)
        if missing:
            raise HTTPException(status_code=404, detail=f"Document(s) not found: {', '.join(missing[:10])}")
    return staleness.request_reviews(db, document_ids=ids, requested_by=current_user.email)


@router.post("/staleness/test")
def send_stale_reminder_test(
    current_user: User = Depends(deps.require_capability(_WRITE_CAPABILITY)),
    _: None = Depends(_require_feature),
) -> Any:
    """Post a test card to the configured Teams channel, mentioning the caller."""
    result = staleness.send_test_message(current_user.email)
    return {"ok": result.ok, "status_code": result.status_code, "error": result.error}
