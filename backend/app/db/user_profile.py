"""
Cached per-user context ("the user model") that the agent reads on every turn.

Assembling what we know about a caller is too slow to do inline: the identity
group lookup alone is a 30s-timeout HTTP call (or a Databricks job with a 300s
poll). So the assembled blob is cached here and served stale-while-revalidate —
a chat turn always gets an answer immediately and any refresh happens in the
background.

``context`` holds the rendered *sections* (identity / activity / groups), each
carrying its own ``built_at`` and ``error`` so one failing provider degrades that
section only instead of the whole profile.
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, Column, DateTime, String, Text

from app.db.base import Base

# Refresh lifecycle for a row. Plain strings for SQLite/Postgres portability.
# 'pending' means the row exists but has never been fully built — a first-time
# user whose cheap sections are populated while the slow lookup is still queued.
REFRESH_STATES = ("pending", "fresh", "refreshing", "error")


class UserProfileModel(Base):
    """The cached user model for one person, keyed by their email."""

    __tablename__ = "user_profiles"

    # Email is the natural key everywhere else in the schema (requests,
    # approvals, training all join on it), so it is the primary key here too
    # rather than a surrogate UUID nobody would ever look up by. Always stored
    # lowercased — see ``normalize_email`` in ``app.services.user_context``.
    email: str = Column(String, primary_key=True, comment="Lowercased user email")

    display_name: Optional[str] = Column(String, nullable=True, comment="Full name from the IdP")
    persona: Optional[str] = Column(String, nullable=True, comment="Derived persona, e.g. Platform Admin")

    roles: Optional[list] = Column(JSON, nullable=True, comment="Internal app roles from role_mappings")
    entitlements: Optional[list] = Column(JSON, nullable=True, comment="SCIM groups / account roles")

    # The assembled sections blob: {"identity": {...}, "activity": {...}, ...}
    # where each section carries "built_at" and optionally "error".
    context: Optional[dict] = Column(JSON, nullable=True, comment="Assembled context sections")

    # --- Cache bookkeeping -------------------------------------------------
    refreshed_at: Optional[datetime] = Column(
        DateTime, nullable=True, comment="When the sections were last rebuilt"
    )
    expires_at: Optional[datetime] = Column(
        DateTime, nullable=True, index=True, comment="refreshed_at + TTL; past this the row is stale"
    )
    refresh_state: str = Column(
        String, nullable=False, default="pending",
        comment="pending | fresh | refreshing | error — 'refreshing' is the cross-replica lock",
    )
    refresh_started_at: Optional[datetime] = Column(
        DateTime, nullable=True, comment="When the in-flight refresh began; used to reclaim a stuck lock",
    )
    last_error: Optional[str] = Column(Text, nullable=True, comment="Last refresh failure, for diagnostics")

    # --- Activity window --------------------------------------------------
    # last_seen_at drives the poller's pre-warm sweep (only warm people who
    # have actually used the app recently) and the retention prune.
    first_seen_at: datetime = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_seen_at: datetime = Column(
        DateTime, default=datetime.utcnow, nullable=False, index=True,
        comment="Last time this user hit the app; scopes pre-warm and pruning",
    )

    created_at: datetime = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: datetime = Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
