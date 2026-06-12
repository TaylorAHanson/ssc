"""Smoke tests for the lightweight startup migrations (rename + add-column).

These guard the unified Tool Catalog rename so an existing DB with the old
``enabled_for_edh``/``enabled_for_workflow`` columns is upgraded in place
(data preserved) and the new ``enabled_for_workflow_execution`` column is added,
and that running twice is a no-op.
"""
from sqlalchemy import create_engine, inspect, text

from app.db.migrate import run_startup_migrations


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
