"""Opt-in REPLICA IDENTITY FULL at startup, for a Lakebase Change Data Feed.

The feed captures only tables with full replica identity, and only the table
owner (the app's service principal) can set it, so the app does it on boot for
the tables an operator lists in LAKEBASE_CDF_TABLES.
"""
import logging
from unittest.mock import MagicMock

from sqlalchemy import create_engine, text

import app.db  # noqa: F401 - registers every model on Base.metadata
from app.db.base import Base
from app.db.migrate import (
    DEFAULT_CDF_TABLES,
    apply_replica_identity_full,
    resolve_cdf_tables,
)


def test_none_and_blank_mean_off():
    assert resolve_cdf_tables("") == []
    assert resolve_cdf_tables(None) == []
    assert resolve_cdf_tables("none") == []
    assert resolve_cdf_tables(" NONE , ") == []


def test_default_expands_and_combines_with_extra_tables():
    assert resolve_cdf_tables("default") == list(DEFAULT_CDF_TABLES)
    # Order kept, duplicates dropped, extras appended after the default set.
    assert resolve_cdf_tables(" requests, default ,failures") == (
        ["requests"] + [t for t in DEFAULT_CDF_TABLES if t != "requests"] + ["failures"]
    )


def test_every_default_table_is_a_real_model_table():
    """A model rename would otherwise drop the table from the feed silently."""
    assert set(DEFAULT_CDF_TABLES) <= set(Base.metadata.tables)


def test_default_set_leaves_out_langgraph_checkpoints():
    assert not any(t.startswith("checkpoint") for t in DEFAULT_CDF_TABLES)


def test_sqlite_is_a_no_op(caplog):
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE requests (id TEXT PRIMARY KEY)"))

    with caplog.at_level(logging.INFO, logger="app.db.migrate"):
        result = apply_replica_identity_full(engine, tables_setting="default")

    assert not any(result.values())
    assert "not Postgres" in caplog.text


def test_unset_setting_does_nothing_even_on_postgres():
    engine = MagicMock()
    engine.dialect.name = "postgresql"

    result = apply_replica_identity_full(engine, tables_setting="none", schema="selfservice")

    assert not any(result.values())
    engine.connect.assert_not_called()
    engine.begin.assert_not_called()


def _pg_engine(identities, fail_on=()):
    """A mock Postgres engine: ``identities`` maps table -> pg_class.relreplident."""
    engine = MagicMock()
    engine.dialect.name = "postgresql"
    read_conn = MagicMock()
    read_conn.execute.return_value.fetchall.return_value = list(identities.items())
    engine.connect.return_value.__enter__.return_value = read_conn

    statements = []
    write_conn = MagicMock()

    def _execute(stmt, *args, **kwargs):
        sql = str(stmt)
        statements.append(sql)
        if any(f'"{t}" REPLICA IDENTITY' in sql for t in fail_on):
            raise RuntimeError("must be owner of table")

    write_conn.execute.side_effect = _execute
    engine.begin.return_value.__enter__.return_value = write_conn
    return engine, read_conn, statements


def test_postgres_alters_only_tables_not_already_full(caplog):
    engine, read_conn, statements = _pg_engine(
        {"requests": "d", "events": "f", "approvals": "d", "checkpoints": "d"}
    )

    with caplog.at_level(logging.INFO, logger="app.db.migrate"):
        result = apply_replica_identity_full(
            engine, tables_setting="requests,events,approvals", schema="selfservice",
        )

    assert result["altered"] == ["requests", "approvals"]
    assert result["already_full"] == ["events"]
    assert not result["failed"] and not result["missing"]
    # The lookup is scoped to the app schema.
    assert read_conn.execute.call_args.args[1] == {"schema": "selfservice"}
    alters = [s for s in statements if "ALTER TABLE" in s]
    assert alters == [
        'ALTER TABLE "selfservice"."requests" REPLICA IDENTITY FULL',
        'ALTER TABLE "selfservice"."approvals" REPLICA IDENTITY FULL',
    ]
    # Each ALTER caps its lock wait so a busy table can't stall startup.
    assert statements.count("SET LOCAL lock_timeout = '5s'") == 2
    assert "set now ['requests', 'approvals']" in caplog.text


def test_postgres_logs_and_continues_past_failures_missing_and_invalid_names(caplog):
    engine, _, statements = _pg_engine({"requests": "d", "approvals": "d"}, fail_on=("requests",))

    with caplog.at_level(logging.INFO, logger="app.db.migrate"):
        result = apply_replica_identity_full(
            engine,
            tables_setting='requests,no_such_table,approvals,bad"; DROP TABLE x',
            schema="selfservice",
        )

    assert result["failed"] == ["requests"]
    assert result["altered"] == ["approvals"]
    assert result["missing"] == ["no_such_table"]
    assert result["invalid"] == ['bad"; DROP TABLE x']
    assert not any("DROP" in s for s in statements)
    assert "must be owner of table" in caplog.text
    assert "no_such_table" in caplog.text


def test_postgres_lookup_failure_never_raises(caplog):
    engine = MagicMock()
    engine.dialect.name = "postgresql"
    engine.connect.side_effect = RuntimeError("connection refused")

    with caplog.at_level(logging.WARNING, logger="app.db.migrate"):
        result = apply_replica_identity_full(engine, tables_setting="default", schema="selfservice")

    assert result["failed"] == list(DEFAULT_CDF_TABLES)
    engine.begin.assert_not_called()
    assert "connection refused" in caplog.text
