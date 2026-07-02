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
import re

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

# Bare SQL identifier guard for schema names interpolated into DDL/DML that
# can't be parameterized (schema-qualified names).
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


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
        # Tool false-success detection: an author-declared success condition.
        _add_column(engine, "tool_registry", "success_predicate", "TEXT")
        # Scheduled reports: per-subscription timezone for cron evaluation.
        # Default mirrors the previously-hardcoded scheduling tz so existing
        # subscriptions keep running at the same wall-clock time.
        _add_column(
            engine,
            "report_subscriptions",
            "timezone",
            "VARCHAR DEFAULT 'America/Los_Angeles'",
        )
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


def backfill_from_schema(
    engine: Engine, source_schema: str, target_schema: str
) -> None:
    """One-time, idempotent adoption of legacy data from another schema.

    Copies rows from ``source_schema.<table>`` into ``target_schema.<table>``
    for every table present in *both* schemas, but only when the target table
    is empty (so it never clobbers live data and is a no-op once populated).

    This exists because the app can only ``ALTER``/``create_all`` tables it
    *owns* — which is the SP-owned ``target_schema`` (``DB_SCHEMA``), never a
    legacy schema like ``atlas`` whose tables belong to another role. Rather
    than run the app against un-ownable legacy tables (where every migration
    fails), we let ``create_all`` build the correct, owned tables here and pull
    the old rows across. The copy uses the intersection of columns, so columns
    the legacy schema lacks (e.g. newly-added ``timezone``) simply take their
    model defaults.

    Runs as the app service principal — the only role that can write the
    SP-owned target schema — which is why this can't be a plain SQL script run
    as a human/superuser (they hit the ``SET ROLE`` wall on the SP's objects).
    Postgres only; a no-op on SQLite (dev) since there are no schemas there.
    """
    if not source_schema:
        return
    if engine.dialect.name != "postgresql":
        return  # schemas are a Postgres concept; SQLite dev has none
    if not _IDENT_RE.match(source_schema) or not _IDENT_RE.match(target_schema):
        logger.warning(
            "Skipping backfill: invalid schema identifier(s) source=%r target=%r",
            source_schema,
            target_schema,
        )
        return
    if source_schema == target_schema:
        return

    try:
        insp = inspect(engine)
        source_tables = set(insp.get_table_names(schema=source_schema))
        target_tables = set(insp.get_table_names(schema=target_schema))
    except Exception as e:  # noqa: BLE001
        logger.warning("Backfill: could not inspect schemas (skipping): %s", e)
        return

    shared = source_tables & target_tables
    if not shared:
        logger.info(
            "Backfill: no shared tables between '%s' and '%s'; nothing to copy.",
            source_schema,
            target_schema,
        )
        return

    # Copy parents before children so FK constraints are satisfied as we go
    # (e.g. `requests` before `approvals`). SQLAlchemy's ``sorted_tables``
    # returns tables in dependency order for exactly this reason; anything not
    # modeled falls to the end (alphabetical) as a best effort.
    ordered = _ordered_tables(shared)

    copied_tables = 0
    for table in ordered:
        # Each table is isolated in its own transaction + try/except so a single
        # bad table (e.g. orphaned rows that violate a FK) is logged and skipped
        # rather than aborting the whole startup.
        try:
            src_cols = {c["name"] for c in insp.get_columns(table, schema=source_schema)}
            tgt_cols = {c["name"] for c in insp.get_columns(table, schema=target_schema)}
            common = [c for c in src_cols & tgt_cols]
            if not common:
                continue

            with engine.begin() as conn:
                # Only adopt into an empty target table, so we never overwrite
                # live data and the whole step is safe to re-run on every boot.
                tgt_count = conn.execute(
                    text(f'SELECT COUNT(*) FROM "{target_schema}"."{table}"')
                ).scalar()
                if tgt_count:
                    continue
                src_count = conn.execute(
                    text(f'SELECT COUNT(*) FROM "{source_schema}"."{table}"')
                ).scalar()
                if not src_count:
                    continue

                col_list = ", ".join(f'"{c}"' for c in common)
                conn.execute(
                    text(
                        f'INSERT INTO "{target_schema}"."{table}" ({col_list}) '
                        f'SELECT {col_list} FROM "{source_schema}"."{table}"'
                    )
                )
                logger.info(
                    "Backfill: copied %s row(s) into %s.%s from %s.%s",
                    src_count,
                    target_schema,
                    table,
                    source_schema,
                    table,
                )
                copied_tables += 1

                # Realign owned sequences so post-copy inserts don't collide with
                # adopted PKs. Only touches columns backed by a serial/identity seq.
                for col in common:
                    try:
                        seq = conn.execute(
                            text("SELECT pg_get_serial_sequence(:tbl, :col)"),
                            {"tbl": f'"{target_schema}"."{table}"', "col": col},
                        ).scalar()
                        if not seq:
                            continue
                        conn.execute(
                            text(
                                f'SELECT setval(:seq, '
                                f'COALESCE((SELECT MAX("{col}") FROM "{target_schema}"."{table}"), 1))'
                            ),
                            {"seq": seq},
                        )
                    except Exception as e:  # noqa: BLE001
                        logger.warning(
                            "Backfill: could not realign sequence for %s.%s: %s",
                            table,
                            col,
                            e,
                        )
        except Exception as e:  # noqa: BLE001 - never let one table block startup
            logger.warning("Backfill: skipping table %s (copy failed): %s", table, e)
            continue

    logger.info(
        "Backfill from '%s' -> '%s' complete: adopted %d table(s).",
        source_schema,
        target_schema,
        copied_tables,
    )


def _ordered_tables(shared: set[str]) -> list[str]:
    """Order ``shared`` table names parents-first for FK-safe inserts.

    Uses ``Base.metadata.sorted_tables`` (topological, dependency order). Tables
    not present in the model metadata are appended alphabetically. Falls back to
    plain alphabetical order if metadata can't be imported for any reason.
    """
    try:
        from app.db.base import Base
        import app.db  # noqa: F401 - ensure all models register on the metadata

        model_order = [t.name for t in Base.metadata.sorted_tables]
    except Exception as e:  # noqa: BLE001
        logger.warning("Backfill: metadata unavailable, using name order: %s", e)
        return sorted(shared)

    ranked = [t for t in model_order if t in shared]
    leftovers = sorted(shared - set(ranked))
    return ranked + leftovers
