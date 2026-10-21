"""Metadata Manager desired state (ADR-0006): tags and descriptions as Lakebase rows.

``metadata_objects`` (one row per securable) and ``metadata_tags`` (one row per tag
on an object) are the source of truth. Reads never replay anything.

Every write goes through a change set (``metadata_change_sets``) and leaves one
``metadata_events`` row per tag or description changed, and per object created,
deleted or restored, in the same transaction. The log serves history, revert and
approval evidence; current state is never computed from it.

``metadata_observed`` is a snapshot of what Unity Catalog holds, filled by the
observed sweep, so the UI never queries UC live. The reconciler diffs desired
against observed, records each run and statement (``metadata_reconcile_runs`` /
``_items``), and drains ``metadata_reconcile_queue``. ``metadata_principals``
scopes machine callers. ``metadata_sync_state`` holds watermarks and flags.

``fqn_key`` is the lowercased ``catalog``, ``catalog.schema`` or
``catalog.schema.object``, with ``::column`` appended for a column.
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, Column, DateTime, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped

from app.db.base import Base

OBJECT_ACTIVE = "active"
OBJECT_DELETED = "deleted"

STATE_EXISTS = "exists"
STATE_PENDING = "pending"
STATE_ORPHANED = "orphaned"

ACTOR_USER = "user"
ACTOR_SERVICE_PRINCIPAL = "service_principal"
ACTOR_SYSTEM = "system"

# Where a write came from (stamped on tags, comments, change sets and events).
SOURCE_UI = "ui"
SOURCE_API = "api"
SOURCE_MCP = "mcp"
SOURCE_AGENT = "agent"
SOURCE_ADOPT = "adopt"
SOURCE_IMPORT = "import"
SOURCE_SYSTEM = "system"
SOURCE_REVERT = "revert"
SOURCES = (SOURCE_UI, SOURCE_API, SOURCE_MCP, SOURCE_AGENT, SOURCE_ADOPT, SOURCE_IMPORT, SOURCE_SYSTEM,
           SOURCE_REVERT)

# Event actions.
EVENT_TAG_SET = "tag_set"
EVENT_TAG_UNSET = "tag_unset"
EVENT_COMMENT_SET = "comment_set"
EVENT_COMMENT_CLEAR = "comment_clear"
EVENT_OBJECT_CREATE = "object_create"
EVENT_OBJECT_DELETE = "object_delete"
EVENT_OBJECT_RESTORE = "object_restore"
EVENT_OBJECT_STATE = "object_state"


class MetadataObjectModel(Base):
    __tablename__ = "metadata_objects"

    __table_args__ = (
        Index("ix_metadata_objects_catalog", "catalog"),
        Index("ix_metadata_objects_environment", "environment"),
        Index("ix_metadata_objects_object_state", "object_state"),
        Index("ix_metadata_objects_drift", "drift_detected_at"),
        Index("ix_metadata_objects_parent", "parent_key"),
        Index("ix_metadata_objects_updated_at", "updated_at"),
        Index("ix_metadata_objects_securable_type", "securable_type"),
    )

    id: Mapped[str] = Column(String, primary_key=True)
    fqn_key: Mapped[str] = Column(String, nullable=False, unique=True)
    # catalog | schema | table | view | materialized_view | streaming_table | metric_view | column
    securable_type: Mapped[str] = Column(String, nullable=False)
    catalog: Mapped[str] = Column(String, nullable=False)
    schema_name: Mapped[Optional[str]] = Column(String, nullable=True)
    object_name: Mapped[Optional[str]] = Column(String, nullable=True)
    column_name: Mapped[Optional[str]] = Column(String, nullable=True)
    # fqn_key of the containing object (column -> relation -> schema -> catalog); NULL for a catalog.
    parent_key: Mapped[Optional[str]] = Column(String, nullable=True)
    # The name as first written (original case), e.g. "Sales.Bronze.Orders.Email".
    display_name: Mapped[str] = Column(Text, nullable=False)
    environment: Mapped[Optional[str]] = Column(String, nullable=True)

    # NULL = no description.
    comment: Mapped[Optional[str]] = Column(Text, nullable=True)
    comment_set_by: Mapped[Optional[str]] = Column(String, nullable=True)
    comment_set_by_type: Mapped[Optional[str]] = Column(String, nullable=True)
    comment_source: Mapped[Optional[str]] = Column(String, nullable=True)
    comment_set_at: Mapped[Optional[datetime]] = Column(DateTime, nullable=True)
    comment_change_set_id: Mapped[Optional[str]] = Column(String, nullable=True)
    # 0 = never set; every comment write bumps it.
    comment_version: Mapped[int] = Column(Integer, nullable=False, default=0)

    status: Mapped[str] = Column(String, nullable=False, default=OBJECT_ACTIVE)
    object_state: Mapped[str] = Column(String, nullable=False, default=STATE_PENDING)
    orphaned_at: Mapped[Optional[datetime]] = Column(DateTime, nullable=True)

    created_at: Mapped[datetime] = Column(DateTime, default=datetime.utcnow)
    created_by: Mapped[Optional[str]] = Column(String, nullable=True)
    created_by_type: Mapped[Optional[str]] = Column(String, nullable=True)
    # Any change to the row or its tags.
    updated_at: Mapped[datetime] = Column(DateTime, default=datetime.utcnow)

    last_applied_at: Mapped[Optional[datetime]] = Column(DateTime, nullable=True)
    # pending | applied | failed | skipped
    last_apply_status: Mapped[Optional[str]] = Column(String, nullable=True)
    last_apply_error: Mapped[Optional[str]] = Column(Text, nullable=True)
    last_observed_at: Mapped[Optional[datetime]] = Column(DateTime, nullable=True)
    # Set while desired and observed differ (in plan mode, or after a failed apply); NULL when in sync.
    drift_detected_at: Mapped[Optional[datetime]] = Column(DateTime, nullable=True)
    # sha256 of the canonical desired tags + comment; the reconciler compares it with applied_hash.
    desired_hash: Mapped[Optional[str]] = Column(String, nullable=True)
    applied_hash: Mapped[Optional[str]] = Column(String, nullable=True)


class MetadataTagModel(Base):
    __tablename__ = "metadata_tags"

    __table_args__ = (
        UniqueConstraint("object_id", "key", name="uq_metadata_tags_object_key"),
        Index("ix_metadata_tags_key_value", "key", "value"),
        Index("ix_metadata_tags_object", "object_id"),
    )

    id: Mapped[int] = Column(Integer, primary_key=True, autoincrement=True)
    object_id: Mapped[str] = Column(String, nullable=False)
    key: Mapped[str] = Column(String, nullable=False)
    # NULL = key-only tag (UC's "" is normalized to NULL).
    value: Mapped[Optional[str]] = Column(String, nullable=True)
    set_by: Mapped[Optional[str]] = Column(String, nullable=True)
    set_by_type: Mapped[Optional[str]] = Column(String, nullable=True)
    source: Mapped[Optional[str]] = Column(String, nullable=True)
    set_at: Mapped[datetime] = Column(DateTime, default=datetime.utcnow)
    change_set_id: Mapped[Optional[str]] = Column(String, nullable=True)
    # Optimistic-lock counter for this tag; starts at 1 and every write bumps it.
    version: Mapped[int] = Column(Integer, nullable=False, default=1)


class MetadataChangeSetModel(Base):
    """One atomic write: a direct edit, an approved request, an adopt, an import or a revert."""

    __tablename__ = "metadata_change_sets"

    __table_args__ = (
        Index("ix_metadata_change_sets_created_at", "created_at"),
        Index("ix_metadata_change_sets_actor", "actor"),
        Index("uq_metadata_change_sets_idempotency", "actor", "idempotency_key", unique=True),
        Index("ix_metadata_change_sets_request", "request_id"),
    )

    id: Mapped[str] = Column(String, primary_key=True)
    actor: Mapped[str] = Column(String, nullable=False)
    actor_type: Mapped[str] = Column(String, nullable=False, default=ACTOR_USER)
    source: Mapped[str] = Column(String, nullable=False)
    reason: Mapped[Optional[str]] = Column(Text, nullable=True)
    idempotency_key: Mapped[Optional[str]] = Column(String, nullable=True)
    # The approval request this change set records, when it went through the workflow.
    request_id: Mapped[Optional[str]] = Column(String, nullable=True)
    reverts_change_set_id: Mapped[Optional[str]] = Column(String, nullable=True)
    reverted_by: Mapped[Optional[str]] = Column(String, nullable=True)
    # {"objects": n, "tags_set": n, "tags_unset": n, "comments": n, "created": n, "environments": [...]}
    summary: Mapped[dict] = Column(JSON, nullable=False, default=dict)
    # The ChangeResult returned to the caller, replayed for a repeated idempotency key.
    result: Mapped[Optional[dict]] = Column(JSON, nullable=True)
    created_at: Mapped[datetime] = Column(DateTime, default=datetime.utcnow)


class MetadataEventModel(Base):
    __tablename__ = "metadata_events"

    __table_args__ = (
        Index("ix_metadata_events_object", "object_id"),
        Index("ix_metadata_events_change_set", "change_set_id"),
        Index("ix_metadata_events_fqn", "fqn_key"),
    )

    # Monotonic: max(id) is the change-log watermark.
    id: Mapped[int] = Column(Integer, primary_key=True, autoincrement=True)
    change_set_id: Mapped[str] = Column(String, nullable=False)
    object_id: Mapped[str] = Column(String, nullable=False)
    fqn_key: Mapped[str] = Column(String, nullable=False)
    # tag_set | tag_unset | comment_set | comment_clear | object_create | object_delete | object_restore | object_state
    action: Mapped[str] = Column(String, nullable=False)
    # Tag key for tag events.
    key: Mapped[Optional[str]] = Column(String, nullable=True)
    # {"value": ..., "version": n} for tags and comments; the row's fields for object events.
    before: Mapped[Optional[dict]] = Column(JSON, nullable=True)
    after: Mapped[Optional[dict]] = Column(JSON, nullable=True)
    actor: Mapped[Optional[str]] = Column(String, nullable=True)
    actor_type: Mapped[Optional[str]] = Column(String, nullable=True)
    source: Mapped[str] = Column(String, nullable=False)
    created_at: Mapped[datetime] = Column(DateTime, default=datetime.utcnow)


class MetadataObservedModel(Base):
    """What Unity Catalog held at ``observed_at``. Never written by a user change."""

    __tablename__ = "metadata_observed"

    __table_args__ = (
        Index("ix_metadata_observed_catalog", "catalog"),
        Index("ix_metadata_observed_parent", "parent_key"),
        Index("ix_metadata_observed_last_altered", "last_altered"),
        Index("ix_metadata_observed_securable_type", "securable_type"),
    )

    fqn_key: Mapped[str] = Column(String, primary_key=True)
    securable_type: Mapped[str] = Column(String, nullable=False)
    catalog: Mapped[str] = Column(String, nullable=False)
    schema_name: Mapped[Optional[str]] = Column(String, nullable=True)
    object_name: Mapped[Optional[str]] = Column(String, nullable=True)
    column_name: Mapped[Optional[str]] = Column(String, nullable=True)
    parent_key: Mapped[Optional[str]] = Column(String, nullable=True)
    display_name: Mapped[str] = Column(Text, nullable=False)
    # information_schema table_type as reported (MANAGED, EXTERNAL, VIEW, MATERIALIZED_VIEW, ...).
    table_type: Mapped[Optional[str]] = Column(String, nullable=True)
    data_type: Mapped[Optional[str]] = Column(String, nullable=True)
    ordinal_position: Mapped[Optional[int]] = Column(Integer, nullable=True)
    owner: Mapped[Optional[str]] = Column(String, nullable=True)
    comment: Mapped[Optional[str]] = Column(Text, nullable=True)
    # {"key": "value" | null}; "" from UC is stored as null.
    tags: Mapped[dict] = Column(JSON, nullable=False, default=dict)
    # False once a sweep no longer sees the object (it was dropped).
    present: Mapped[bool] = Column(Boolean, nullable=False, default=True)
    created: Mapped[Optional[datetime]] = Column(DateTime, nullable=True)
    last_altered: Mapped[Optional[datetime]] = Column(DateTime, nullable=True)
    observed_at: Mapped[datetime] = Column(DateTime, default=datetime.utcnow)
    # First sweep that no longer saw the object; NULL while it exists.
    missing_since: Mapped[Optional[datetime]] = Column(DateTime, nullable=True)


class MetadataReconcileQueueModel(Base):
    """Objects waiting for the reconciler. One live row per relation-level key."""

    __tablename__ = "metadata_reconcile_queue"

    __table_args__ = (
        UniqueConstraint("fqn_key", name="uq_metadata_reconcile_queue_fqn"),
        Index("ix_metadata_reconcile_queue_not_before", "not_before"),
    )

    id: Mapped[int] = Column(Integer, primary_key=True, autoincrement=True)
    # A catalog, schema or relation key (columns are reconciled with their relation).
    fqn_key: Mapped[str] = Column(String, nullable=False)
    # change | new_object | notify | retry | promote
    reason: Mapped[str] = Column(String, nullable=False, default="change")
    requested_by: Mapped[Optional[str]] = Column(String, nullable=True)
    change_set_id: Mapped[Optional[str]] = Column(String, nullable=True)
    attempts: Mapped[int] = Column(Integer, nullable=False, default=0)
    last_error: Mapped[Optional[str]] = Column(Text, nullable=True)
    enqueued_at: Mapped[datetime] = Column(DateTime, default=datetime.utcnow)
    not_before: Mapped[datetime] = Column(DateTime, default=datetime.utcnow)


class MetadataReconcileRunModel(Base):
    __tablename__ = "metadata_reconcile_runs"

    __table_args__ = (Index("ix_metadata_reconcile_runs_started_at", "started_at"),)

    id: Mapped[str] = Column(String, primary_key=True)
    # queue | sweep | manual | new_object | notify
    trigger: Mapped[str] = Column(String, nullable=False)
    # off | plan | enforce
    mode: Mapped[str] = Column(String, nullable=False)
    # running | completed | partial | failed | paused | skipped
    status: Mapped[str] = Column(String, nullable=False, default="running")
    actor: Mapped[Optional[str]] = Column(String, nullable=True)
    objects_checked: Mapped[int] = Column(Integer, nullable=False, default=0)
    objects_drifted: Mapped[int] = Column(Integer, nullable=False, default=0)
    statements_planned: Mapped[int] = Column(Integer, nullable=False, default=0)
    statements_applied: Mapped[int] = Column(Integer, nullable=False, default=0)
    statements_failed: Mapped[int] = Column(Integer, nullable=False, default=0)
    removals_planned: Mapped[int] = Column(Integer, nullable=False, default=0)
    skipped_catalogs: Mapped[list] = Column(JSON, nullable=False, default=list)
    paused_reason: Mapped[Optional[str]] = Column(Text, nullable=True)
    error: Mapped[Optional[str]] = Column(Text, nullable=True)
    summary: Mapped[dict] = Column(JSON, nullable=False, default=dict)
    started_at: Mapped[datetime] = Column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[Optional[datetime]] = Column(DateTime, nullable=True)


class MetadataReconcileItemModel(Base):
    """One planned or executed statement of a run."""

    __tablename__ = "metadata_reconcile_items"

    __table_args__ = (
        Index("ix_metadata_reconcile_items_run", "run_id"),
        Index("ix_metadata_reconcile_items_fqn", "fqn_key"),
    )

    id: Mapped[int] = Column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = Column(String, nullable=False)
    object_id: Mapped[Optional[str]] = Column(String, nullable=True)
    fqn_key: Mapped[str] = Column(String, nullable=False)
    securable_type: Mapped[Optional[str]] = Column(String, nullable=True)
    # set | unset | comment
    operation: Mapped[str] = Column(String, nullable=False)
    sql: Mapped[str] = Column(Text, nullable=False, default="")
    # Tags this statement removes (counted by the circuit breaker).
    removals: Mapped[int] = Column(Integer, nullable=False, default=0)
    # {"tags": {...}, "keys": [...], "comment": ...}: what the statement sets, for the run detail.
    payload: Mapped[Optional[dict]] = Column(JSON, nullable=True)
    # planned | applied | noop | failed | skipped
    status: Mapped[str] = Column(String, nullable=False)
    detail: Mapped[Optional[str]] = Column(Text, nullable=True)
    created_at: Mapped[datetime] = Column(DateTime, default=datetime.utcnow)


class MetadataPrincipalModel(Base):
    """A machine caller (Databricks service principal) and the scope it may edit."""

    __tablename__ = "metadata_principals"

    # Lowercased application (client) id.
    application_id: Mapped[str] = Column(String, primary_key=True)
    display_name: Mapped[str] = Column(String, nullable=False, default="")
    description: Mapped[Optional[str]] = Column(Text, nullable=True)
    enabled: Mapped[bool] = Column(Boolean, nullable=False, default=True)
    # Name globs: "sales_*", "sales_dev.bronze", "*.bronze.*". Empty = nothing.
    scopes: Mapped[list] = Column(JSON, nullable=False, default=list)
    # Tag-key globs the principal may write. Empty = any key its key policies allow.
    allowed_keys: Mapped[list] = Column(JSON, nullable=False, default=list)
    # ChangeOp names. Empty = all.
    allowed_operations: Mapped[list] = Column(JSON, nullable=False, default=list)
    # Environment keys. Empty = all.
    environments: Mapped[list] = Column(JSON, nullable=False, default=list)
    # NULL = METADATA_MAX_OBJECTS_PER_CHANGE.
    max_objects_per_change: Mapped[Optional[int]] = Column(Integer, nullable=True)
    can_skip_approval: Mapped[bool] = Column(Boolean, nullable=False, default=False)
    # Header shape seen on the last call (which identity headers were present), never values of tokens.
    last_seen_at: Mapped[Optional[datetime]] = Column(DateTime, nullable=True)
    last_seen_headers: Mapped[Optional[dict]] = Column(JSON, nullable=True)
    created_at: Mapped[datetime] = Column(DateTime, default=datetime.utcnow)
    created_by: Mapped[Optional[str]] = Column(String, nullable=True)
    updated_at: Mapped[datetime] = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by: Mapped[Optional[str]] = Column(String, nullable=True)


class MetadataSyncStateModel(Base):
    """Small key-value store: the new-object watermark, the last sweep, the circuit-breaker flag."""

    __tablename__ = "metadata_sync_state"

    key: Mapped[str] = Column(String, primary_key=True)
    value: Mapped[Optional[dict]] = Column(JSON, nullable=True)
    updated_at: Mapped[datetime] = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
