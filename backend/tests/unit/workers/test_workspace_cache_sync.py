"""The data-asset sync also refreshes the workspace cache, and the two fail independently."""
import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.db.base import Base
from app.db.data_asset import DataAssetModel
from app.services import workspace_cache
from app.workers.tasks import sync_data_assets

ASSET_ROW = {
    "catalog": "main", "schema": "sales", "table_name": "orders", "type": "MANAGED",
    "description": None, "owner": "a@b.com", "created_at": None, "tags": [],
}
WS_ROW = {
    "account_id": "acc", "workspace_id": "42", "workspace_name": "supply-chain-prod",
    "workspace_url": "https://sc.cloud.databricks.com", "status": "RUNNING",
    "create_time": None,
}


@pytest.fixture
def session_factory(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(sync_data_assets, "get_lakebase_session", factory)
    monkeypatch.setattr(workspace_cache, "get_lakebase_session", factory)
    monkeypatch.setattr(workspace_cache, "_unreadable_warned", False)
    monkeypatch.setattr(sync_data_assets, "_next_sync_time", None)
    monkeypatch.setattr(sync_data_assets, "_boot_checked", False)
    monkeypatch.setattr(settings, "WORKSPACE_CACHE_ENABLED", True)
    monkeypatch.setattr(settings, "SCAN_CATALOGS", "", raising=False)
    monkeypatch.setattr(settings, "DATA_ASSET_SYNC_CRON", "0 * * * *")
    yield factory
    engine.dispose()


def _provider(monkeypatch, *, assets_error=None, workspaces_error=None):
    async def execute_sql(query, **_kwargs):
        if "workspaces_latest" in query:
            if workspaces_error:
                raise workspaces_error
            return {"rows": [dict(WS_ROW)], "schema": list(WS_ROW)}
        if assets_error:
            raise assets_error
        return {"rows": [dict(ASSET_ROW)], "schema": list(ASSET_ROW)}

    provider = MagicMock()
    provider.execute_sql = AsyncMock(side_effect=execute_sql)
    monkeypatch.setattr(
        "app.core.workspaces.get_governance_uc_provider", lambda: provider
    )
    return provider


def _seed_fresh_asset(factory):
    db = factory()
    db.add(DataAssetModel(
        id="main.sales.orders", catalog="main", schema="sales", table_name="orders",
        type="MANAGED", last_synced_at=datetime.now(timezone.utc).replace(tzinfo=None),
    ))
    db.commit()
    db.close()


def _assets(factory):
    db = factory()
    try:
        return [a.id for a in db.query(DataAssetModel).all()]
    finally:
        db.close()


def _workspace_ids():
    rows, _ = workspace_cache.read_cached_workspaces()
    return [r["workspace_id"] for r in rows]


def test_one_sync_run_writes_assets_and_workspaces(session_factory, monkeypatch):
    provider = _provider(monkeypatch)

    asyncio.run(sync_data_assets.sync_data_assets_task(force=True))

    assert _assets(session_factory) == ["main.sales.orders"]
    assert _workspace_ids() == ["42"]
    assert provider.execute_sql.await_count == 2


def test_asset_sync_failure_still_refreshes_workspaces(session_factory, monkeypatch):
    _provider(monkeypatch, assets_error=RuntimeError("information_schema exploded"))

    asyncio.run(sync_data_assets.sync_data_assets_task(force=True))

    assert _assets(session_factory) == []
    assert _workspace_ids() == ["42"]


def test_workspace_failure_does_not_fail_the_asset_sync(session_factory, monkeypatch):
    _provider(monkeypatch, workspaces_error=RuntimeError("unexpected"))

    asyncio.run(sync_data_assets.sync_data_assets_task(force=True))

    assert _assets(session_factory) == ["main.sales.orders"]
    assert _workspace_ids() == []


def test_switched_off_cache_is_not_refreshed(session_factory, monkeypatch):
    provider = _provider(monkeypatch)
    monkeypatch.setattr(settings, "WORKSPACE_CACHE_ENABLED", False)

    asyncio.run(sync_data_assets.sync_data_assets_task(force=True))

    assert _workspace_ids() == []
    assert provider.execute_sql.await_count == 1


def test_boot_with_fresh_assets_still_warms_an_empty_workspace_cache(session_factory, monkeypatch):
    provider = _provider(monkeypatch)
    _seed_fresh_asset(session_factory)

    asyncio.run(sync_data_assets.sync_data_assets_task())

    assert _workspace_ids() == ["42"]
    queries = [c.args[0] for c in provider.execute_sql.await_args_list]
    assert len(queries) == 1 and "workspaces_latest" in queries[0]


def test_boot_with_both_caches_filled_runs_nothing(session_factory, monkeypatch):
    provider = _provider(monkeypatch)
    asyncio.run(workspace_cache.refresh_workspace_cache(provider))
    _seed_fresh_asset(session_factory)
    provider.execute_sql.reset_mock()

    asyncio.run(sync_data_assets.sync_data_assets_task())

    provider.execute_sql.assert_not_awaited()
