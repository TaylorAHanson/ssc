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


def run_startup_migrations(engine: Engine) -> None:
    """Apply in-place schema renames. Safe to call on every startup."""
    try:
        # skills -> workflows terminology rename.
        _rename_table(engine, "skills", "workflows")
        _rename_table(engine, "skill_versions", "workflow_versions")
        _rename_column(engine, "workflow_versions", "skill_id", "workflow_id")
        _rename_column(engine, "workflow_versions", "skill_key", "workflow_key")
    except Exception as e:  # noqa: BLE001 - never block startup on a migration
        logger.warning("Startup migration step failed (continuing): %s", e)
