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


def test_orphaned_workflow_children_are_pruned():
    """Deleting a workflow used to leave its test cases behind. They're unreachable
    (reads join on workflow_id), so a re-created key showed an empty Tests tab while
    the old rows piled up."""
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE workflows (id TEXT PRIMARY KEY, key TEXT)"))
        conn.execute(text(
            "CREATE TABLE workflow_tests (id TEXT PRIMARY KEY, workflow_id TEXT, name TEXT)"
        ))
        conn.execute(text(
            "CREATE TABLE workflow_test_runs (id TEXT PRIMARY KEY, workflow_id TEXT)"
        ))
        conn.execute(text(
            "CREATE TABLE workflow_versions (id TEXT PRIMARY KEY, workflow_id TEXT)"
        ))
        conn.execute(text("INSERT INTO workflows VALUES ('live', 'still_here')"))
        # One live workflow's children, plus children of a workflow since deleted.
        conn.execute(text("INSERT INTO workflow_tests VALUES ('t1', 'live', 'keep me')"))
        conn.execute(text("INSERT INTO workflow_tests VALUES ('t2', 'gone', 'orphan')"))
        conn.execute(text("INSERT INTO workflow_test_runs VALUES ('r1', 'gone')"))
        conn.execute(text("INSERT INTO workflow_versions VALUES ('v1', 'gone')"))

    run_startup_migrations(engine)

    with engine.begin() as conn:
        tests = [r[0] for r in conn.execute(text("SELECT id FROM workflow_tests"))]
        runs = conn.execute(text("SELECT count(*) FROM workflow_test_runs")).scalar()
        versions = conn.execute(text("SELECT count(*) FROM workflow_versions")).scalar()
    assert tests == ["t1"]  # the live workflow's case survives
    assert runs == 0 and versions == 0

    # Idempotent: nothing left to prune, and the survivor stays.
    run_startup_migrations(engine)
    with engine.begin() as conn:
        assert conn.execute(text("SELECT count(*) FROM workflow_tests")).scalar() == 1


def test_the_prune_does_nothing_when_the_workflows_table_is_empty():
    """Guard on a destructive migration that runs on every boot.

    If the app starts while ``workflows`` is empty but its children aren't — a
    half-restored backup, a truncate-and-reseed — then every child looks orphaned
    and the prune would delete the published version snapshots that are the only
    rollback path.
    """
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE workflows (id TEXT PRIMARY KEY, key TEXT)"))
        conn.execute(text(
            "CREATE TABLE workflow_tests (id TEXT PRIMARY KEY, workflow_id TEXT, name TEXT)"
        ))
        conn.execute(text(
            "CREATE TABLE workflow_test_runs (id TEXT PRIMARY KEY, workflow_id TEXT)"
        ))
        conn.execute(text(
            "CREATE TABLE workflow_versions (id TEXT PRIMARY KEY, workflow_id TEXT)"
        ))
        # Children present, parent table not yet populated.
        conn.execute(text("INSERT INTO workflow_tests VALUES ('t1', 'wf1', 'case')"))
        conn.execute(text("INSERT INTO workflow_versions VALUES ('v1', 'wf1')"))

    run_startup_migrations(engine)

    with engine.begin() as conn:
        assert conn.execute(text("SELECT count(*) FROM workflow_tests")).scalar() == 1
        assert conn.execute(text("SELECT count(*) FROM workflow_versions")).scalar() == 1
