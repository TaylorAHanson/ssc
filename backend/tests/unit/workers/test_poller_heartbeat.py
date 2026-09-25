"""The poller's lock heartbeat beats well inside the lock window.

Every run heartbeats (not just PROVISIONING ones), because a request resuming
off a gate can run a long step; the interval must stay under the lock timeout
or the lock lapses between beats and a second worker re-runs the step.
"""
import asyncio

import pytest

import app.workers.poller as poller


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout_minutes, expected", [(5, 100), (30, 300)])
async def test_heartbeat_interval_stays_inside_the_lock(monkeypatch, timeout_minutes, expected):
    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)
        raise asyncio.CancelledError

    monkeypatch.setattr(poller.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(poller.settings, "POLLER_HEARTBEAT_INTERVAL_SECONDS", 300)
    try:
        await poller._heartbeat_lock_loop("req-x", timeout_minutes)
    except asyncio.CancelledError:
        pass
    assert slept == [expected]
