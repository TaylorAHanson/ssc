"""
Context Catalog document freshness API: mark reviewed, and the Teams reminder
status / run-now / test-message actions for admins.
"""
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api import deps
from app.api.v1.context_catalog import _WRITE_ROLES, _require_feature
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
    if not any(current_user.has_role(r) for r in _WRITE_ROLES):
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


@router.get("/staleness")
def get_staleness_status(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.require_any_role(_WRITE_ROLES)),
    _: None = Depends(_require_feature),
) -> Any:
    """Reminder configuration (never the webhook URL) and every stale published document."""
    return staleness.reminder_status(db)


@router.post("/staleness/run")
def run_stale_reminders_now(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.require_any_role(_WRITE_ROLES)),
    _: None = Depends(_require_feature),
) -> Any:
    """Post reminders now (respects the re-notify interval; ignores the on/off toggle)."""
    logger.info("Context Catalog stale reminders run requested by %s", current_user.email)
    return staleness.run_stale_reminders(db, trigger="manual")


@router.post("/staleness/test")
def send_stale_reminder_test(
    current_user: User = Depends(deps.require_any_role(_WRITE_ROLES)),
    _: None = Depends(_require_feature),
) -> Any:
    """Post a test card to the configured Teams channel, mentioning the caller."""
    result = staleness.send_test_message(current_user.email)
    return {"ok": result.ok, "status_code": result.status_code, "error": result.error}
