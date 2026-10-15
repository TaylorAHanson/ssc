"""list_workspaces answers from the workspace cache and falls back to the live OBO query."""
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.core.config import settings
from app.services import workspace_cache
from app.tools.governance.list_workspaces import list_workspaces

LIVE_ROWS = [{"workspace_id": "9", "workspace_name": "live-ws", "status": "RUNNING"}]
CACHED = [
    {"workspace_id": "1", "workspace_name": "finance", "status": "RUNNING"},
    {"workspace_id": "2", "workspace_name": "supply-chain", "status": "FAILED"},
]


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setattr(settings, "WORKSPACE_CACHE_ENABLED", True)
    monkeypatch.setattr(settings, "WORKSPACE_CACHE_MAX_AGE_MINUTES", 180)


@pytest.fixture
def live():
    with patch("app.tools.governance.list_workspaces.DatabricksProvider") as provider_cls:
        provider_cls.return_value.execute_sql = AsyncMock(return_value={"rows": LIVE_ROWS})
        yield provider_cls.return_value.execute_sql


def _cache(age_minutes):
    """Patch the cache read to hold CACHED, synced ``age_minutes`` ago (None = empty)."""
    cached_at = (
        None if age_minutes is None
        else datetime.now(timezone.utc) - timedelta(minutes=age_minutes)
    )

    def read(status=None):
        if cached_at is None:
            return [], None
        return [r for r in CACHED if not status or r["status"] == status], cached_at

    return patch.object(workspace_cache, "read_cached_workspaces", side_effect=read)


def _call(**args):
    return asyncio.run(list_workspaces.execute(_obo_token="user-token", **args))


def test_fresh_cache_answers_without_a_query(live):
    with _cache(age_minutes=5):
        out = _call()

    assert out["source"] == "cache"
    assert out["count"] == 2 and [w["workspace_id"] for w in out["workspaces"]] == ["1", "2"]
    assert datetime.fromisoformat(out["cached_at"]).tzinfo is not None
    assert "query" not in out
    live.assert_not_called()


def test_status_filter_applies_to_the_cache(live):
    with _cache(age_minutes=5):
        out = _call(status="FAILED")

    assert out["source"] == "cache"
    assert [w["workspace_name"] for w in out["workspaces"]] == ["supply-chain"]
    live.assert_not_called()


def test_stale_cache_falls_back_to_live(live, monkeypatch):
    monkeypatch.setattr(settings, "WORKSPACE_CACHE_MAX_AGE_MINUTES", 60)
    with _cache(age_minutes=61):
        out = _call(status="RUNNING")

    assert out["source"] == "live" and out["workspaces"] == LIVE_ROWS
    query = live.call_args.args[0]
    assert "WHERE status = 'RUNNING'" in query
    assert live.call_args.kwargs["obo_token"] == "user-token"
    assert live.call_args.kwargs["require_obo"] is True


def test_refresh_skips_a_fresh_cache(live):
    with _cache(age_minutes=1):
        out = _call(refresh=True)

    assert out["source"] == "live"
    live.assert_awaited_once()


def test_empty_cache_falls_back_to_live(live):
    with _cache(age_minutes=None):
        out = _call()

    assert out["source"] == "live" and out["count"] == 1


def test_cache_switched_off_goes_live_without_reading_it(live, monkeypatch):
    monkeypatch.setattr(settings, "WORKSPACE_CACHE_ENABLED", False)
    with _cache(age_minutes=1) as read:
        out = _call()

    assert out["source"] == "live"
    read.assert_not_called()


def test_unreadable_cache_falls_back_to_live(live):
    with patch.object(workspace_cache, "read_cached_workspaces", side_effect=RuntimeError("db down")):
        out = _call()

    assert out["source"] == "live"


def test_failed_live_query_answers_from_a_stale_cache(live, monkeypatch):
    monkeypatch.setattr(settings, "WORKSPACE_CACHE_MAX_AGE_MINUTES", 60)
    live.side_effect = RuntimeError("warehouse unavailable")
    with _cache(age_minutes=90):
        out = _call()

    assert out["source"] == "cache" and out["stale"] is True
    assert "warehouse unavailable" in out["live_error"]
    assert "error" not in out


def test_failed_live_query_with_no_cache_reports_the_error(live):
    live.side_effect = RuntimeError("warehouse unavailable")
    with _cache(age_minutes=None):
        out = _call()

    assert out["workspaces"] == [] and "warehouse unavailable" in out["error"]


def test_name_contains_ignores_case_and_separators(live):
    with _cache(age_minutes=5):
        out = _call(name_contains="Supply Chain")

    assert [w["workspace_name"] for w in out["workspaces"]] == ["supply-chain"]
    assert out["count"] == 1 and "truncated" not in out


def test_name_contains_also_filters_the_live_answer(live):
    with _cache(age_minutes=None):
        out = _call(name_contains="nope")

    assert out["source"] == "live" and out["workspaces"] == [] and out["count"] == 0


def test_limit_caps_the_list_and_reports_the_total(live):
    with _cache(age_minutes=5):
        out = _call(limit=1)

    assert out["count"] == 1 and len(out["workspaces"]) == 1
    assert out["total_matches"] == 2 and out["truncated"] is True


def test_refresh_is_advertised_to_the_agent():
    props = list_workspaces.input_schema["properties"]
    assert props["refresh"]["type"] == "boolean"
    assert "cache" in list_workspaces.description
