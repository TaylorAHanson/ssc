"""
Sync state for the Context Catalog's Git integration.

One row per environment (``dev``, ``test``, ``stage``, ``prod``...). It records
what the environment last agreed with its branch on, so the next sync can tell
an edit made in the app from one made in Git:

* ``synced_tree`` maps each catalog file's repo path (relative to the base
  folder) to its Git blob sha at the last sync.
* ``synced_render`` maps the same paths to the sha of the app's own rendering at
  the last sync.

A file whose rendering differs from ``synced_render`` was edited in the app; a
file whose blob differs from ``synced_tree`` was edited in Git. Saves made while
Git is unreachable queue in ``pending_changes`` and are pushed by the poller.
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import Column, String, DateTime, Text, JSON
from sqlalchemy.orm import Mapped

from app.db.base import Base


class ContextCatalogGitStateModel(Base):
    __tablename__ = "context_catalog_git_state"

    environment: Mapped[str] = Column(String, primary_key=True, comment="Environment key this row belongs to")
    repo: Mapped[Optional[str]] = Column(String, nullable=True, comment="owner/repo the state was recorded against")
    branch: Mapped[Optional[str]] = Column(String, nullable=True, comment="Branch the state was recorded against")
    base_path: Mapped[Optional[str]] = Column(String, nullable=True, comment="Repo folder holding the catalog")

    last_synced_sha: Mapped[Optional[str]] = Column(String, nullable=True, comment="Branch commit at the last sync")
    synced_tree: Mapped[Optional[dict]] = Column(JSON, nullable=True, comment="path -> git blob sha at the last sync")
    synced_render: Mapped[Optional[dict]] = Column(JSON, nullable=True, comment="path -> app rendering sha at the last sync")
    synced_index: Mapped[Optional[dict]] = Column(
        JSON, nullable=True, comment="path -> 'doc:<id>' / 'domain:<id>' it defined at the last sync",
    )
    front_matter_extras: Mapped[Optional[dict]] = Column(
        JSON, nullable=True,
        comment="Front-matter keys the app has no column for, kept so they survive a round trip",
    )

    pending_changes: Mapped[Optional[list]] = Column(JSON, nullable=True, comment="Saves not yet committed to Git")
    pending_since: Mapped[Optional[datetime]] = Column(DateTime, nullable=True)

    last_commit_sha: Mapped[Optional[str]] = Column(String, nullable=True, comment="Last commit this env pushed")
    last_commit_at: Mapped[Optional[datetime]] = Column(DateTime, nullable=True)
    last_pull_at: Mapped[Optional[datetime]] = Column(DateTime, nullable=True, comment="Last time Git changes were applied")
    last_sync_at: Mapped[Optional[datetime]] = Column(DateTime, nullable=True)
    last_sync_trigger: Mapped[Optional[str]] = Column(String, nullable=True, comment="save | schedule | manual")
    last_report: Mapped[Optional[dict]] = Column(JSON, nullable=True, comment="Summary of the last successful sync")
    conflicts: Mapped[Optional[list]] = Column(JSON, nullable=True, comment="Recent files changed in both places")

    last_error: Mapped[Optional[str]] = Column(Text, nullable=True)
    last_error_at: Mapped[Optional[datetime]] = Column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
