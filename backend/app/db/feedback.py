"""
Database model for user-submitted feedback.

Covers three kinds of submissions surfaced from the avatar menu (and the chat
agent): general feedback, feature requests, and bug reports. Bug reports may
carry a snapshot of the user's recent console logs and failed network requests
captured client-side to aid debugging.
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import Column, String, DateTime, Text, JSON

from app.db.base import Base


# Allowed enum-ish values (kept as plain strings for SQLite/Postgres portability).
FEEDBACK_TYPES = ("bug", "feature", "feedback")
FEEDBACK_STATUSES = ("open", "in_progress", "resolved", "closed", "wont_fix")
FEEDBACK_SOURCES = ("web", "chat")


class FeedbackModel(Base):
    """A single feedback / feature-request / bug-report submission."""

    __tablename__ = "feedback"

    id: str = Column(String, primary_key=True, comment="Unique UUID for the submission")
    type: str = Column(
        String, nullable=False, index=True,
        comment="One of: bug, feature, feedback",
    )
    title: str = Column(String, nullable=False, comment="Short summary")
    description: Optional[str] = Column(Text, nullable=True, comment="Full details")
    severity: Optional[str] = Column(
        String, nullable=True, comment="For bugs: low, medium, high, critical",
    )
    status: str = Column(
        String, nullable=False, default="open", index=True,
        comment="Triage status: open, in_progress, resolved, closed, wont_fix",
    )
    source: str = Column(
        String, nullable=False, default="web",
        comment="Where it came from: web (form) or chat (agent tool)",
    )

    # Submitter identity (from auth; not user-entered).
    submitted_by: Optional[str] = Column(String, nullable=True, index=True, comment="Submitter email")
    submitted_by_name: Optional[str] = Column(String, nullable=True, comment="Submitter display name")

    # Diagnostic context (mostly for bugs).
    page_url: Optional[str] = Column(String, nullable=True, comment="URL the user was on")
    user_agent: Optional[str] = Column(Text, nullable=True, comment="Browser user-agent")
    app_version: Optional[str] = Column(String, nullable=True, comment="Frontend build/version")
    console_logs: Optional[list] = Column(JSON, nullable=True, comment="Recent console entries snapshot")
    network_errors: Optional[list] = Column(JSON, nullable=True, comment="Recent failed network requests snapshot")

    # Admin triage.
    admin_notes: Optional[str] = Column(Text, nullable=True, comment="Internal triage notes")

    created_at: datetime = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    updated_at: datetime = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
