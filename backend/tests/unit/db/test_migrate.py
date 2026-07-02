"""Smoke tests for the lightweight startup migrations (rename + add-column).

These guard the unified Tool Catalog rename so an existing DB with the old
``enabled_for_edh``/``enabled_for_workflow`` columns is upgraded in place
(data preserved) and the new ``enabled_for_workflow_execution`` column is added,
and that running twice is a no-op.
"""
from sqlalchemy import create_engine, inspect, text

from app.db.migrate import backfill_from_schema, run_startup_migrations


def _columns(engine, table):
    return {c["name"] for c in inspect(engine).get_columns(table)}


def test_tool_registry_rename_and_add_column_idempotent():
    engine = create_engine("sqlite://")  # in-memory
    # Simulate the OLD schema with a seeded row.
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE tool_registry ("
            "id TEXT PRIMARY KEY, tool_name TEXT, "
            "enabled_for_edh INTEGER DEFAULT 0, "
            "enabled_for_workflow INTEGER DEFAULT 0)"
        ))
        conn.execute(text(
            "INSERT INTO tool_registry (id, tool_name, enabled_for_edh, enabled_for_workflow) "
            "VALUES ('1', 'demo', 1, 0)"
        ))

    run_startup_migrations(engine)

    cols = _columns(engine, "tool_registry")
    assert "enabled_for_main_agent" in cols
    assert "enabled_for_workflow_agent" in cols
    assert "enabled_for_workflow_execution" in cols
    assert "enabled_for_edh" not in cols
    assert "enabled_for_workflow" not in cols

    # Data survived the rename; the new column defaults to 0.
    with engine.begin() as conn:
        row = conn.execute(text(
            "SELECT enabled_for_main_agent, enabled_for_workflow_agent, "
            "enabled_for_workflow_execution FROM tool_registry WHERE id='1'"
        )).fetchone()
    assert tuple(row) == (1, 0, 0)

    # Running again is a no-op (idempotent).
    run_startup_migrations(engine)
    assert _columns(engine, "tool_registry") == cols


def test_backfill_guards_are_noops_and_never_raise():
    """The cross-schema backfill must be a safe no-op when it can't/shouldn't run.

    Schemas are a Postgres concept, so on SQLite (dev/tests) and for empty or
    invalid schema names the function returns without touching anything and
    without raising — startup must never be blocked by it.
    """
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE t (id INTEGER PRIMARY KEY)"))

    # Empty source disables the backfill.
    backfill_from_schema(engine, source_schema="", target_schema="selfservice")
    # SQLite dialect => no-op regardless of args.
    backfill_from_schema(engine, source_schema="atlas", target_schema="selfservice")
    # Invalid identifiers are guarded (would otherwise be interpolated into DDL).
    backfill_from_schema(engine, source_schema="atlas; DROP TABLE t", target_schema="selfservice")
    # Same source/target is a no-op.
    backfill_from_schema(engine, source_schema="selfservice", target_schema="selfservice")

    # The seed table is untouched by any of the above.
    with engine.begin() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM t")).scalar() == 0
