"""The warm endpoint's job is to return fast and never make things worse.

It runs on every page load, so a slow or throwing implementation would turn an
optimization into a regression on the app's most common request.
"""
import asyncio

import pytest

from app.api.v1 import agent as agent_api
from app.models.user import User


def _user() -> User:
    return User(
        id="warm@example.com",
        email="warm@example.com",
        full_name="Warm User",
        roles=["Platform Admin"],
        entitlements=["grp"],
    )


@pytest.mark.asyncio
async def test_warm_returns_freshness_metadata(db_session, monkeypatch):
    monkeypatch.setattr(agent_api, "is_feature_enabled", lambda _f: True)

    async def _fake_get(db, user, **kwargs):
        return {"refresh_state": "fresh", "stale": False, "refreshed_at": "2026-07-28T00:00:00"}

    monkeypatch.setattr("app.services.user_context.get_user_context", _fake_get)

    result = await agent_api.warm_user_context_endpoint(db=db_session, current_user=_user())

    assert result == {
        "enabled": True,
        "state": "fresh",
        "stale": False,
        "refreshed_at": "2026-07-28T00:00:00",
    }


@pytest.mark.asyncio
async def test_warm_does_not_propagate_a_provider_failure(db_session, monkeypatch):
    """A broken identity provider must not make page load fail."""
    monkeypatch.setattr(agent_api, "is_feature_enabled", lambda _f: True)

    async def _boom(db, user):
        raise RuntimeError("LMWS unreachable")

    monkeypatch.setattr(agent_api, "warm_user_context", _boom)

    result = await agent_api.warm_user_context_endpoint(db=db_session, current_user=_user())

    assert result["state"] == "error"
    assert result["stale"] is True


@pytest.mark.asyncio
async def test_warm_is_a_noop_when_the_feature_is_off(db_session, monkeypatch):
    monkeypatch.setattr("app.services.user_context.is_feature_enabled", lambda _f: False)

    async def _unexpected(*args, **kwargs):
        raise AssertionError("must not assemble context when the feature is off")

    monkeypatch.setattr("app.services.user_context.get_user_context", _unexpected)

    result = await agent_api.warm_user_context_endpoint(db=db_session, current_user=_user())
    assert result == {"enabled": False}


@pytest.mark.asyncio
async def test_warm_returns_without_waiting_for_the_slow_section(db_session, monkeypatch):
    """The slow group lookup is scheduled, not awaited.

    This is the whole contract: a 30-second provider call must not become a
    30-second page-load request.
    """
    from app.services import user_context as uc

    monkeypatch.setattr(agent_api, "is_feature_enabled", lambda _f: True)
    monkeypatch.setattr(uc, "_open_session", lambda: db_session)
    monkeypatch.setattr("app.db.session.get_lakebase_session", lambda: db_session, raising=False)
    monkeypatch.setattr(db_session, "close", lambda: None, raising=False)
    uc._IN_FLIGHT.clear()

    started = asyncio.Event()

    async def _very_slow_groups(db, identity):
        started.set()
        await asyncio.sleep(30)
        return {"groups": []}

    monkeypatch.setitem(uc.SECTION_BUILDERS, "groups", _very_slow_groups)

    try:
        # Generous relative to the work (a few DB reads), tight relative to the
        # 30s builder — a regression that awaits the refresh fails here.
        result = await asyncio.wait_for(
            agent_api.warm_user_context_endpoint(db=db_session, current_user=_user()),
            timeout=5,
        )
        assert result["enabled"] is True
    finally:
        # Cancel the scheduled refresh so it doesn't outlive the test.
        for task in asyncio.all_tasks():
            if task is not asyncio.current_task():
                task.cancel()
        uc._IN_FLIGHT.clear()


@pytest.mark.asyncio
async def test_debug_endpoint_is_scoped_to_the_caller(db_session, monkeypatch):
    """There is no parameter for whose context to read — only the caller's."""
    import inspect

    params = inspect.signature(agent_api.get_user_context_endpoint).parameters
    assert set(params) == {"db", "current_user"}

    monkeypatch.setattr(agent_api, "is_feature_enabled", lambda _f: True)

    async def _fake_get(db, user, **kwargs):
        assert user.email == "warm@example.com"
        return {"email": user.email, "sections": {}, "stale": False}

    monkeypatch.setattr(agent_api, "get_user_context", _fake_get)

    result = await agent_api.get_user_context_endpoint(db=db_session, current_user=_user())
    assert result["email"] == "warm@example.com"
    assert result["prompt_block"] == ""
