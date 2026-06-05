"""
Service layer for user feedback (feedback / feature requests / bug reports).

Keeps business logic out of the API router and the agent tool, both of which
call into here. Mirrors the Context Catalog service conventions: static methods,
``db`` first arg, ValueError for bad input, dict serializers for the API.
"""
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.db.feedback import (
    FeedbackModel,
    FEEDBACK_TYPES,
    FEEDBACK_STATUSES,
    FEEDBACK_SOURCES,
)

logger = logging.getLogger(__name__)

# Defensive caps so a runaway client can't persist huge blobs.
_MAX_CONSOLE_ENTRIES = 100
_MAX_NETWORK_ENTRIES = 50
_MAX_TEXT = 8000


class FeedbackService:
    """CRUD + serialization for feedback submissions."""

    @staticmethod
    def create_feedback(
        db: Session,
        *,
        type: str,
        title: str,
        description: Optional[str] = None,
        severity: Optional[str] = None,
        source: str = "web",
        submitted_by: Optional[str] = None,
        submitted_by_name: Optional[str] = None,
        page_url: Optional[str] = None,
        user_agent: Optional[str] = None,
        app_version: Optional[str] = None,
        console_logs: Optional[list] = None,
        network_errors: Optional[list] = None,
    ) -> FeedbackModel:
        ftype = (type or "").strip().lower()
        if ftype not in FEEDBACK_TYPES:
            raise ValueError(
                f"Invalid feedback type '{type}'. Must be one of: {', '.join(FEEDBACK_TYPES)}"
            )
        if not (title or "").strip():
            raise ValueError("title is required")

        if source not in FEEDBACK_SOURCES:
            source = "web"

        # Bugs are the only type that carries a severity; ignore it elsewhere.
        sev = (severity or "").strip().lower() or None
        if ftype != "bug":
            sev = None

        fb = FeedbackModel(
            id=str(uuid.uuid4()),
            type=ftype,
            title=title.strip()[:300],
            description=(description or "").strip()[:_MAX_TEXT] or None,
            severity=sev,
            status="open",
            source=source,
            submitted_by=submitted_by,
            submitted_by_name=submitted_by_name,
            page_url=(page_url or None),
            user_agent=(user_agent or None),
            app_version=(app_version or None),
            console_logs=(console_logs or None) if ftype == "bug" else None,
            network_errors=(network_errors or None) if ftype == "bug" else None,
        )
        # Trim oversized diagnostic arrays.
        if isinstance(fb.console_logs, list):
            fb.console_logs = fb.console_logs[-_MAX_CONSOLE_ENTRIES:]
        if isinstance(fb.network_errors, list):
            fb.network_errors = fb.network_errors[-_MAX_NETWORK_ENTRIES:]

        db.add(fb)
        db.commit()
        db.refresh(fb)
        logger.info(
            "Feedback created: id=%s type=%s source=%s by=%s",
            fb.id, fb.type, fb.source, fb.submitted_by,
        )
        return fb

    @staticmethod
    def list_feedback(
        db: Session,
        *,
        type: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 200,
    ) -> List[FeedbackModel]:
        q = db.query(FeedbackModel)
        if type:
            q = q.filter(FeedbackModel.type == type.strip().lower())
        if status:
            q = q.filter(FeedbackModel.status == status.strip().lower())
        return q.order_by(FeedbackModel.created_at.desc()).limit(limit).all()

    @staticmethod
    def get_feedback(db: Session, feedback_id: str) -> Optional[FeedbackModel]:
        return db.query(FeedbackModel).filter(FeedbackModel.id == feedback_id).first()

    @staticmethod
    def update_feedback(
        db: Session,
        feedback_id: str,
        *,
        status: Optional[str] = None,
        admin_notes: Optional[str] = None,
        severity: Optional[str] = None,
    ) -> Optional[FeedbackModel]:
        fb = FeedbackService.get_feedback(db, feedback_id)
        if not fb:
            return None
        if status is not None:
            s = status.strip().lower()
            if s not in FEEDBACK_STATUSES:
                raise ValueError(
                    f"Invalid status '{status}'. Must be one of: {', '.join(FEEDBACK_STATUSES)}"
                )
            fb.status = s
        if admin_notes is not None:
            fb.admin_notes = admin_notes.strip()[:_MAX_TEXT] or None
        if severity is not None:
            fb.severity = severity.strip().lower() or None
        fb.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(fb)
        return fb

    @staticmethod
    def delete_feedback(db: Session, feedback_id: str) -> bool:
        fb = FeedbackService.get_feedback(db, feedback_id)
        if not fb:
            return False
        db.delete(fb)
        db.commit()
        return True

    @staticmethod
    def to_dict(fb: FeedbackModel, *, include_diagnostics: bool = True) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "id": fb.id,
            "type": fb.type,
            "title": fb.title,
            "description": fb.description,
            "severity": fb.severity,
            "status": fb.status,
            "source": fb.source,
            "submitted_by": fb.submitted_by,
            "submitted_by_name": fb.submitted_by_name,
            "page_url": fb.page_url,
            "app_version": fb.app_version,
            "created_at": fb.created_at.isoformat() if fb.created_at else None,
            "updated_at": fb.updated_at.isoformat() if fb.updated_at else None,
            "admin_notes": fb.admin_notes,
        }
        if include_diagnostics:
            data["user_agent"] = fb.user_agent
            data["console_logs"] = fb.console_logs or []
            data["network_errors"] = fb.network_errors or []
        else:
            data["has_diagnostics"] = bool(fb.console_logs or fb.network_errors)
        return data
