"""Databricks preview features (Beta / Public Preview / Private Preview) we track.

``preview_features`` holds one row per feature, merged from two sources: the
workspace Settings v2 metadata API (``setting_name`` set) and the public docs
release-notes feed (feed-only account previews have no ``setting_name``).

``preview_feature_targets`` holds one row per feature x target, where a target
is a target-workspace name or ``__account__`` for account-scoped previews. Each
target moves through ``not_requested -> requested -> approved -> implemented``
on its own, because which workspaces want a feature varies. Status history
lives in the request's facts, so there is no separate history table.
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped

from app.db.base import Base

# The target used for account-scoped previews (there's no account credential, so
# these are implemented by a person and verified from a workspace where possible).
ACCOUNT_TARGET = "__account__"


class PreviewFeatureModel(Base):
    __tablename__ = "preview_features"

    __table_args__ = (
        Index("ix_preview_features_setting_name", "setting_name"),
        Index("ix_preview_features_archived", "archived_at"),
    )

    id: Mapped[str] = Column(String, primary_key=True)
    # Settings v2 name (e.g. ``abac_on_views``). Null for feed-only previews.
    setting_name: Mapped[Optional[str]] = Column(String, nullable=True, unique=True)
    # Stable key for feed-only previews (normalized display name), so a re-sync
    # updates the same row instead of creating a duplicate.
    feed_key: Mapped[Optional[str]] = Column(String, nullable=True, unique=True)
    display_name: Mapped[str] = Column(String, nullable=False)
    description: Mapped[Optional[str]] = Column(Text, nullable=True)
    # BETA | PUBLIC_PREVIEW | PRIVATE_PREVIEW (GA rows are archived).
    phase: Mapped[Optional[str]] = Column(String, nullable=True)
    phase_changed_at: Mapped[Optional[datetime]] = Column(DateTime, nullable=True)
    # workspace | account | unknown
    scope: Mapped[str] = Column(String, nullable=False, default="workspace")
    # api | inferred | admin (admin wins over later syncs)
    scope_source: Mapped[str] = Column(String, nullable=False, default="api")
    # boolean | other (only boolean settings can be switched automatically)
    value_type: Mapped[Optional[str]] = Column(String, nullable=True)
    # Release-note text from the docs feed. Untrusted; rendered as plain text.
    announcement_text: Mapped[Optional[str]] = Column(Text, nullable=True)
    announcement_url: Mapped[Optional[str]] = Column(String, nullable=True)
    announced_at: Mapped[Optional[datetime]] = Column(DateTime, nullable=True)
    docs_link: Mapped[Optional[str]] = Column(String, nullable=True)
    # api | docs (page named after the setting) | feed | sitemap | admin
    # (admin always wins; sitemap is a name match, shown as "suggested")
    docs_link_source: Mapped[Optional[str]] = Column(String, nullable=True)
    # Optional read-only capability probe, e.g. {"type": "rest", "path": "/api/..."}.
    probe: Mapped[Optional[dict]] = Column(JSON, nullable=True)
    first_seen_at: Mapped[datetime] = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_seen_at: Mapped[Optional[datetime]] = Column(DateTime, nullable=True)
    # Consecutive syncs in which no workspace listed this setting.
    missing_syncs: Mapped[int] = Column(Integer, nullable=False, default=0)
    archived_at: Mapped[Optional[datetime]] = Column(DateTime, nullable=True)
    # ga | retired
    archived_reason: Mapped[Optional[str]] = Column(String, nullable=True)
    raw: Mapped[Optional[dict]] = Column(JSON, nullable=True)
    updated_at: Mapped[datetime] = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class PreviewFeatureTargetModel(Base):
    __tablename__ = "preview_feature_targets"

    __table_args__ = (
        UniqueConstraint("feature_id", "target", name="uq_preview_feature_target"),
        Index("ix_preview_feature_targets_status", "status"),
        Index("ix_preview_feature_targets_request", "request_id"),
    )

    id: Mapped[str] = Column(String, primary_key=True)
    feature_id: Mapped[str] = Column(
        String, ForeignKey("preview_features.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    # Target-workspace name, or ACCOUNT_TARGET.
    target: Mapped[str] = Column(String, nullable=False)
    # Listed in that workspace's settings metadata on the last sync.
    available: Mapped[bool] = Column(Boolean, nullable=False, default=False)
    # not_requested | requested | approved | implemented | rejected
    status: Mapped[str] = Column(String, nullable=False, default="not_requested")
    # enable | disable: the direction of the in-flight (or last) request.
    action: Mapped[Optional[str]] = Column(String, nullable=True)
    request_id: Mapped[Optional[str]] = Column(String, nullable=True)
    requested_by: Mapped[Optional[str]] = Column(String, nullable=True)
    approved_by: Mapped[Optional[str]] = Column(String, nullable=True)
    implemented_by: Mapped[Optional[str]] = Column(String, nullable=True)
    # Last read value, e.g. {"effective": true, "set_here": false}.
    observed_value: Mapped[Optional[dict]] = Column(JSON, nullable=True)
    observed_at: Mapped[Optional[datetime]] = Column(DateTime, nullable=True)
    # Why the last read failed (the setting isn't manageable through the API).
    observe_error: Mapped[Optional[str]] = Column(Text, nullable=True)
    # api | probe | attested | none
    verification: Mapped[Optional[str]] = Column(String, nullable=True)
    last_verified_at: Mapped[Optional[datetime]] = Column(DateTime, nullable=True)
    verify_failures: Mapped[int] = Column(Integer, nullable=False, default=0)
    # Was implemented and is now off (or the reverse, for a disable).
    drift: Mapped[bool] = Column(Boolean, nullable=False, default=False)
    note: Mapped[Optional[str]] = Column(Text, nullable=True)
    updated_at: Mapped[datetime] = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
