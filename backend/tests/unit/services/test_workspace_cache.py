"""The account workspace cache: refresh from system.access.workspaces_latest, then read."""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.exceptions import PermanentError, RetryableError
from app.db.base import Base
from app.db.workspace_cache import CachedWorkspaceModel
from app.services import workspace_cache


@pytest.fixture
def session_factory(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine, tables=[CachedWorkspaceModel.__table__])
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(workspace_cache, "get_lakebase_session", factory)
    monkeypatch.setattr(workspace_cache, "_unreadable_warned", False)
    monkeypatch.setattr(settings, "WORKSPACE_CACHE_ENABLED", True)
    monkeypatch.setattr(settings, "WORKSPACE_CACHE_MAX_AGE_MINUTES", 180)
    yield factory
    engine.dispose()


def _provider(rows=(), schema=None, error=None):
    provider = MagicMock()
    rows = [dict(r) for r in rows]
    cols = schema if schema is not None else (list(rows[0]) if rows else [])
    provider.execute_sql = AsyncMock(
        side_effect=error, return_value=None if error else {"rows": rows, "schema": cols},
    )
    return provider


def _ws(wid, name, url, status="RUNNING", **extra):
    return {
        "account_id": "acc-1", "workspace_id": wid, "workspace_name": name,
        "workspace_url": url, "create_time": "2025-01-02T03:04:05.000Z", "status": status,
        **extra,
    }


def _refresh(provider):
    return asyncio.run(workspace_cache.refresh_workspace_cache(provider))


def test_refresh_writes_rows_and_reads_them_back(session_factory):
    provider = _provider([
        _ws("2", "supply-chain-prod", "https://b.cloud.databricks.com/"),
        _ws("1", "finance-dev", "https://a.cloud.databricks.com", status="PROVISIONING"),
    ])

    assert _refresh(provider) == 2

    sql = provider.execute_sql.call_args.args[0]
    assert "FROM system.access.workspaces_latest" in sql
    rows, cached_at = workspace_cache.read_cached_workspaces()
    assert [r["workspace_name"] for r in rows] == ["finance-dev", "supply-chain-prod"]
    assert rows[0] == {
        "account_id": "acc-1", "workspace_id": "1", "workspace_name": "finance-dev",
        "workspace_url": "https://a.cloud.databricks.com",
        "create_time": "2025-01-02T03:04:05+00:00", "status": "PROVISIONING",
    }
    assert cached_at.tzinfo is not None
    assert datetime.now(timezone.utc) - cached_at < timedelta(minutes=1)


def test_status_filter_keeps_cached_at_even_when_nothing_matches(session_factory):
    _refresh(_provider([
        _ws("1", "a", "https://a"), _ws("2", "b", "https://b", status="FAILED"),
    ]))

    failed, _ = workspace_cache.read_cached_workspaces("FAILED")
    banned, cached_at = workspace_cache.read_cached_workspaces("BANNED")

    assert [r["workspace_id"] for r in failed] == ["2"]
    assert banned == [] and cached_at is not None


def test_region_and_cloud_are_picked_up_when_the_table_has_them(session_factory):
    _refresh(_provider([_ws("1", "a", "https://a", region="us-west-2", cloud="AWS")]))

    rows, _ = workspace_cache.read_cached_workspaces()
    assert rows[0]["region"] == "us-west-2" and rows[0]["cloud"] == "AWS"


def test_each_sync_replaces_the_previous_rows(session_factory):
    _refresh(_provider([_ws("1", "old", "https://old"), _ws("2", "kept", "https://kept")]))
    _refresh(_provider([_ws("2", "kept-renamed", "https://kept"), _ws("3", "new", "https://new")]))

    rows, _ = workspace_cache.read_cached_workspaces()
    assert {r["workspace_id"]: r["workspace_name"] for r in rows} == {
        "2": "kept-renamed", "3": "new",
    }
    db = session_factory()
    try:
        assert len({r.synced_at for r in db.query(CachedWorkspaceModel).all()}) == 1
    finally:
        db.close()


@pytest.mark.parametrize("error", [
    PermanentError("[INSUFFICIENT_PERMISSIONS] User does not have USE SCHEMA on Schema 'system.access'"),
    PermissionError("no OBO"),
    RetryableError("SQL execution timed out after 120s"),
])
def test_failed_read_keeps_the_existing_cache(session_factory, error):
    _refresh(_provider([_ws("1", "a", "https://a")]))

    assert _refresh(_provider(error=error)) is None

    rows, cached_at = workspace_cache.read_cached_workspaces()
    assert [r["workspace_id"] for r in rows] == ["1"] and cached_at is not None


def test_unreadable_table_warns_once_until_it_recovers(session_factory, caplog):
    denied = PermanentError("[INSUFFICIENT_PERMISSIONS] no SELECT on system.access.workspaces_latest")
    with caplog.at_level(logging.DEBUG, logger=workspace_cache.logger.name):
        _refresh(_provider(error=denied))
        _refresh(_provider(error=denied))
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert "USE SCHEMA on system.access" in warnings[0].getMessage()

        _refresh(_provider([_ws("1", "a", "https://a")]))
        assert "readable again" in caplog.text
        caplog.clear()
        _refresh(_provider(error=denied))
        assert [r for r in caplog.records if r.levelno == logging.WARNING]


def test_empty_result_or_missing_id_column_does_not_wipe_the_cache(session_factory):
    _refresh(_provider([_ws("1", "a", "https://a")]))

    assert _refresh(_provider([])) is None
    assert _refresh(_provider([{"workspace_name": "x", "status": "RUNNING"}])) is None

    rows, _ = workspace_cache.read_cached_workspaces()
    assert [r["workspace_id"] for r in rows] == ["1"]


def test_empty_cache_reads_as_nothing(session_factory):
    assert workspace_cache.read_cached_workspaces() == ([], None)
    assert workspace_cache.is_empty()
    assert workspace_cache.cached_by_host() == {}


def test_freshness_follows_the_max_age_setting(session_factory, monkeypatch):
    now = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(settings, "WORKSPACE_CACHE_MAX_AGE_MINUTES", 60)

    assert workspace_cache.is_fresh(now - timedelta(minutes=59), now)
    assert not workspace_cache.is_fresh(now - timedelta(minutes=61), now)
    assert not workspace_cache.is_fresh(None, now)


def test_cached_by_host_normalizes_urls_and_respects_the_switch(session_factory, monkeypatch):
    _refresh(_provider([_ws("7", "a", "https://ADB-7.azuredatabricks.net/")]))

    assert workspace_cache.cached_by_host()["adb-7.azuredatabricks.net"]["workspace_id"] == "7"
    monkeypatch.setattr(settings, "WORKSPACE_CACHE_ENABLED", False)
    assert workspace_cache.cached_by_host() == {}
