"""Startup migration for Context Catalog document owner + freshness columns."""
from sqlalchemy import create_engine, inspect, text

from app.db.migrate import run_startup_migrations

NEW_COLUMNS = {"owner", "content_updated_at", "last_reviewed_at", "last_reviewed_by", "stale_notified_at"}


def _columns(engine):
    return {c["name"] for c in inspect(engine).get_columns("context_documents")}


def test_adds_columns_backfills_once_and_is_idempotent():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE context_documents ("
            "id TEXT PRIMARY KEY, domain_id TEXT, title TEXT, status TEXT, "
            "created_by TEXT, created_at TIMESTAMP, updated_at TIMESTAMP)"
        ))
        conn.execute(text(
            "INSERT INTO context_documents (id, domain_id, title, status, created_by, created_at, updated_at) "
            "VALUES ('d1', 'x', 'Doc', 'published', 'author@corp.com', '2026-01-01 00:00:00', '2026-02-01 00:00:00'),"
            "       ('d2', 'x', 'No author', 'published', NULL, '2026-01-01 00:00:00', '2026-03-01 00:00:00')"
        ))

    run_startup_migrations(engine)

    assert NEW_COLUMNS <= _columns(engine)
    with engine.begin() as conn:
        rows = {r[0]: r[1:] for r in conn.execute(text(
            "SELECT id, owner, content_updated_at FROM context_documents"
        ))}
    assert rows["d1"][0] == "author@corp.com"
    assert str(rows["d1"][1]).startswith("2026-02-01")
    assert rows["d2"][0] is None

    # An owner cleared after the upgrade is not re-filled on the next boot.
    with engine.begin() as conn:
        conn.execute(text("UPDATE context_documents SET owner = NULL WHERE id = 'd1'"))
    cols = _columns(engine)
    run_startup_migrations(engine)
    assert _columns(engine) == cols
    with engine.begin() as conn:
        assert conn.execute(text("SELECT owner FROM context_documents WHERE id='d1'")).scalar() is None


def test_fresh_database_is_left_to_create_all():
    engine = create_engine("sqlite://")
    run_startup_migrations(engine)  # no context_documents table yet: nothing to do, no error
    assert "context_documents" not in inspect(engine).get_table_names()
