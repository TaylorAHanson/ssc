"""
Context Catalog AI review API: start reviews (one document or Review all), read
them, and apply a review's proposed rewrite. Catalog admins only.
"""
import logging
import uuid
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api import deps
from app.api.v1.context_catalog import (
    _WRITE_CAPABILITY,
    _record_git_change,
    _require_direct_editing,
    _require_feature,
)
from app.db.context_catalog import ContextDocumentReviewModel
from app.models.user import User
from app.services import context_catalog_review as reviews
from app.services.context_catalog_service import ContextCatalogService

logger = logging.getLogger(__name__)

router = APIRouter()


class RunReviewsRequest(BaseModel):
    document_ids: Optional[List[str]] = Field(default=None, description="Review these; omit for Review all")
    domain_id: Optional[str] = Field(default=None, description="Review all within this domain and its sub-domains")
    skip_unchanged: bool = Field(default=True, description="Skip documents unchanged since their last review")


class ApplyReviewRequest(BaseModel):
    title: bool = False
    body: bool = False
    tags: bool = False


def _token(request: Request) -> Optional[str]:
    return getattr(request.state, "token", None)


@router.get("/reviews")
def list_reviews(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.require_capability(_WRITE_CAPABILITY)),
    _: None = Depends(_require_feature),
) -> Any:
    """The newest review of every document, plus the progress of the newest Review all."""
    reviews.recover_interrupted(db)
    return {
        "reviews": reviews.latest_reviews(db),
        "progress": reviews.progress(db),
        "confluence": reviews.confluence_status(),
    }


@router.post("/reviews/run")
def run_reviews(
    *,
    request: Request,
    body: RunReviewsRequest,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.require_capability(_WRITE_CAPABILITY)),
    _: None = Depends(_require_feature),
) -> Any:
    reviews.recover_interrupted(db)
    if body.document_ids:
        ids = [d for d in body.document_ids if ContextCatalogService.get_document(db, d)]
        skipped = 0
    else:
        if body.domain_id and not ContextCatalogService.get_domain(db, body.domain_id):
            raise HTTPException(status_code=404, detail="Domain not found")
        ids, skipped = reviews.select_documents(db, domain_id=body.domain_id, skip_unchanged=body.skip_unchanged)
    batch_id = str(uuid.uuid4())
    rows = reviews.queue_reviews(db, ids, requested_by=current_user.email, batch_id=batch_id)
    reviews.start([r.id for r in rows], _token(request))
    logger.info("Context Catalog AI review: queued %d document(s)", len(rows),
                extra={"data": {"batch_id": batch_id, "skipped_unchanged": skipped,
                                "already_running": len(ids) - len(rows)}})
    return {
        "batch_id": batch_id,
        "queued": len(rows),
        "skipped_unchanged": skipped,
        "already_running": len(ids) - len(rows),
    }


@router.post("/documents/{document_id}/ai-review")
def review_document(
    document_id: str,
    request: Request,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.require_capability(_WRITE_CAPABILITY)),
    _: None = Depends(_require_feature),
) -> Any:
    doc = ContextCatalogService.get_document(db, document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    reviews.recover_interrupted(db)
    rows = reviews.queue_reviews(db, [document_id], requested_by=current_user.email)
    if not rows:
        raise HTTPException(status_code=409, detail="This document is already being reviewed")
    reviews.start([rows[0].id], _token(request))
    return {"review": reviews.review_to_dict(rows[0], doc, db=db)}


@router.get("/documents/{document_id}/ai-review")
def get_document_review(
    document_id: str,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.require_capability(_WRITE_CAPABILITY)),
    _: None = Depends(_require_feature),
) -> Any:
    doc = ContextCatalogService.get_document(db, document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    reviews.recover_interrupted(db)
    review = reviews.latest_for_document(db, document_id)
    return {"review": reviews.review_to_dict(review, doc, db=db) if review else None}


@router.post("/reviews/{review_id}/apply")
def apply_review(
    review_id: str,
    body: ApplyReviewRequest,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.require_capability(_WRITE_CAPABILITY)),
    _: None = Depends(_require_feature),
    __: None = Depends(_require_direct_editing),
) -> Any:
    """Apply the chosen parts of a review's proposal (title, body, tags) to its document."""
    try:
        doc = reviews.apply_review(db, review_id, current_user.email, title=body.title, body=body.body,
                                   tags=body.tags)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except reviews.ReviewConflict as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    _record_git_change(db, current_user, "Update", f'document "{doc.title}" (AI review suggestion)')
    review = db.get(ContextDocumentReviewModel, review_id)
    return {
        "document": ContextCatalogService.document_to_dict(doc),
        "review": reviews.review_to_dict(review, doc, db=db) if review else None,
    }
