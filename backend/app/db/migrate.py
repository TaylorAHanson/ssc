"""
Lightweight, idempotent startup migrations.

This project creates tables via ``Base.metadata.create_all`` (no Alembic). When
a model is renamed, ``create_all`` would otherwise create a fresh, empty table
and orphan the old data. These helpers run *before* ``create_all`` to rename
existing tables/columns in place so data survives the rename.

Each step is guarded by an inspector check so it is safe to run on every boot
and on fresh databases (where the old tables never existed).
"""
from __future__ import annotations

import logging

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)


def _rename_table(engine: Engine, old: str, new: str) -> None:
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    if old in tables and new not in tables:
        with engine.begin() as conn:
            conn.execute(text(f'ALTER TABLE {old} RENAME TO {new}'))
        logger.info("Migration: renamed table %s -> %s", old, new)


def _rename_column(engine: Engine, table: str, old: str, new: str) -> None:
    insp = inspect(engine)
    if table not in set(insp.get_table_names()):
        return
    cols = {c["name"] for c in insp.get_columns(table)}
    if old in cols and new not in cols:
        with engine.begin() as conn:
            conn.execute(text(f'ALTER TABLE {table} RENAME COLUMN {old} TO {new}'))
        logger.info("Migration: renamed %s.%s -> %s", table, old, new)


def _add_column(engine: Engine, table: str, column: str, ddl_type: str) -> None:
    """Add ``column`` to ``table`` if it doesn't exist yet (idempotent).

    ``create_all`` only creates missing *tables*, never missing *columns* on an
    existing table — so a new column on an existing model needs this. The DDL
    type string must be valid on both SQLite (local) and Postgres (Lakebase);
    ``INTEGER``, ``TIMESTAMP``, and ``TEXT`` all are.
    """
    insp = inspect(engine)
    if table not in set(insp.get_table_names()):
        return  # fresh DB: create_all will build the table with the column
    cols = {c["name"] for c in insp.get_columns(table)}
    if column in cols:
        return
    with engine.begin() as conn:
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}"))
    logger.info("Migration: added column %s.%s", table, column)


def _add_index(engine: Engine, index_name: str, table: str, columns: list[str]) -> None:
    """Create ``index_name`` on ``table(columns)`` if missing (idempotent).

    ``create_all`` builds model-declared indexes only when it creates the table,
    so an index added to an existing model needs an explicit ``CREATE INDEX IF
    NOT EXISTS`` here. The name must match SQLAlchemy's generated name
    (``ix_<table>_<column>`` for single-column ``index=True``) so the two paths
    don't create duplicate indexes. ``IF NOT EXISTS`` is valid on SQLite and
    Postgres alike.
    """
    insp = inspect(engine)
    if table not in set(insp.get_table_names()):
        return  # fresh DB: create_all will build the index from the model
    existing = {ix.get("name") for ix in insp.get_indexes(table)}
    if index_name in existing:
        return
    cols = ", ".join(columns)
    with engine.begin() as conn:
        conn.execute(text(f"CREATE INDEX IF NOT EXISTS {index_name} ON {table} ({cols})"))
    logger.info("Migration: created index %s on %s(%s)", index_name, table, cols)


def run_startup_migrations(engine: Engine) -> None:
    """Apply in-place schema renames. Safe to call on every startup."""
    try:
        # skills -> workflows terminology rename.
        _rename_table(engine, "skills", "workflows")
        _rename_table(engine, "skill_versions", "workflow_versions")
        _rename_column(engine, "workflow_versions", "skill_id", "workflow_id")
        _rename_column(engine, "workflow_versions", "skill_key", "workflow_key")
        # Context Catalog retrieval-usage signal columns.
        _add_column(engine, "context_documents", "retrieval_count", "INTEGER DEFAULT 0")
        _add_column(engine, "context_documents", "last_retrieved_at", "TIMESTAMP")
        # Tool Registry: unified catalog rename to the three usage contexts.
        _rename_column(engine, "tool_registry", "enabled_for_edh", "enabled_for_main_agent")
        _rename_column(engine, "tool_registry", "enabled_for_workflow", "enabled_for_workflow_agent")
        _add_column(engine, "tool_registry", "enabled_for_workflow_execution", "INTEGER DEFAULT 0")
        _add_column(engine, "tool_registry", "exposed_via_mcp", "INTEGER DEFAULT 0")
        # Performance: hot-path indexes for existing DBs (fresh DBs get these from
        # the models via create_all). Names mirror SQLAlchemy's generated names.
        _add_index(engine, "ix_events_request_id_type", "events", ["request_id", "event_type"])
        _add_index(engine, "ix_requests_status", "requests", ["status"])
        _add_index(engine, "ix_requests_requester_email", "requests", ["requester_email"])
        _add_index(engine, "ix_requests_locked_until", "requests", ["locked_until"])
        _add_index(engine, "ix_approvals_request_id", "approvals", ["request_id"])
        _add_index(engine, "ix_approvals_assigned_to_email", "approvals", ["assigned_to_email"])
        _add_index(engine, "ix_approvals_assigned_to_role", "approvals", ["assigned_to_role"])
        _add_index(engine, "ix_approvals_status", "approvals", ["status"])
    except Exception as e:  # noqa: BLE001 - never block startup on a migration
        logger.warning("Startup migration step failed (continuing): %s", e)
