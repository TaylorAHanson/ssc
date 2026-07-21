"""Per-record storage for an Enforcement Sentinel run's findings.

A single sentinel run can produce tens of thousands of violations and checks.
Storing them inline in ``requests.state_context`` (one JSON column) means a
multi-hundred-MB write that drops the DB connection ("SSL connection has been
closed unexpectedly") and fails the run, and a huge read on the list/detail.

Instead we keep only high-level counts/summary in ``state_context`` and write the
full per-record detail here — one row per finding, inserted in batches. This
never drops the connection, keeps the list tiny, and lets the detail view query,
search, and page the complete set without ever loading everything at once.
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import Column, String, DateTime, JSON, ForeignKey, Index, Text
from sqlalchemy.orm import Mapped

from app.db.base import Base


class SentinelFindingModel(Base):
    """One violation or check produced by a sentinel run."""

    __tablename__ = "sentinel_findings"

    __table_args__ = (
        # The detail view always filters by (run, kind); add policy/severity to
        # support fast server-side facet counts and filtering.
        Index("ix_sentinel_findings_request_kind", "request_id", "kind"),
        Index("ix_sentinel_findings_request_kind_severity", "request_id", "kind", "severity"),
    )

    id: Mapped[str] = Column(String, primary_key=True)
    request_id: Mapped[str] = Column(
        String, ForeignKey("requests.id", ondelete="CASCADE"), nullable=False, index=True,
        comment="The sentinel run this finding belongs to",
    )
    kind: Mapped[str] = Column(
        String, nullable=False, comment="'violation' | 'check'",
    )
    workspace: Mapped[Optional[str]] = Column(String, nullable=True)
    resource_id: Mapped[Optional[str]] = Column(String, nullable=True)
    resource_type: Mapped[Optional[str]] = Column(String, nullable=True)
    policy: Mapped[Optional[str]] = Column(String, nullable=True)
    severity: Mapped[Optional[str]] = Column(String, nullable=True)
    action: Mapped[Optional[str]] = Column(String, nullable=True)
    owner: Mapped[Optional[str]] = Column(String, nullable=True)
    # Lower-cased, concatenated searchable text (resource/policy/owner/workspace/
    # reasons) so the detail view can ILIKE-search server-side without scanning
    # the full JSON.
    search_text: Mapped[Optional[str]] = Column(Text, nullable=True)
    # The full original finding record (what the UI renders / acts on).
    data: Mapped[dict] = Column(JSON, nullable=False)
    created_at: Mapped[datetime] = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
