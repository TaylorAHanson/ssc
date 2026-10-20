"""The poller runs the metadata reconciler only when it's on, one pass at a time, and survives its failures."""
import asyncio
import logging

import pytest

from app.core.config import settings
from app.services.metadata import reconciler
from app.workers import poller


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr(poller, "_metadata_reconcile_last_issue", None)
    monkeypatch.setattr(reconciler, "_bad_mode", None)


def _features(monkeypatch, **flags):
    monkeypatch.setitem(poller._yaml_config, "features", {**(poller._yaml_config.get("features") or {}), **flags})


@pytest.mark.parametrize("flag,mode,enabled", [
    (True, "plan", True),
    (True, "enforce", True),
    (True, "bogus", True),
    (True, "off", False),
    (False, "enforce", False),
])
def test_reconciler_runs_only_with_the_feature_on_and_mode_not_off(monkeypatch, flag, mode, enabled):
    _features(monkeypatch, metadata_v2=flag)
    monkeypatch.setattr(settings, "METADATA_RECONCILE_MODE", mode)

    assert poller._metadata_reconcile_enabled() is enabled


def test_a_broken_mode_lookup_disables_the_reconciler_without_raising(monkeypatch):
    _features(monkeypatch, metadata_v2=True)

    def boom():
        raise RuntimeError("settings unreadable")

    monkeypatch.setattr(reconciler, "current_mode", boom)

    assert poller._metadata_reconcile_enabled() is False


def test_a_pass_calls_reconcile_task(monkeypatch):
    calls = []

    async def fake_task():
        calls.append("ran")

    monkeypatch.setattr(reconciler, "reconcile_task", fake_task)
    asyncio.run(poller._safe_metadata_reconcile(asyncio.Semaphore(1)))

    assert calls == ["ran"]


def test_a_pass_is_skipped_while_the_previous_one_runs(monkeypatch):
    calls = []

    async def fake_task():
        calls.append("ran")

    monkeypatch.setattr(reconciler, "reconcile_task", fake_task)

    async def scenario():
        semaphore = asyncio.Semaphore(1)
        async with semaphore:
            await poller._safe_metadata_reconcile(semaphore)

    asyncio.run(scenario())
    assert calls == []


def test_not_implemented_is_tolerated_and_logged_once(monkeypatch, caplog):
    async def not_yet():
        raise NotImplementedError

    monkeypatch.setattr(reconciler, "reconcile_task", not_yet)

    with caplog.at_level(logging.INFO, logger="app.workers.poller"):
        for _ in range(3):
            asyncio.run(poller._safe_metadata_reconcile(asyncio.Semaphore(1)))

    assert sum("metadata reconciler" in r.getMessage() for r in caplog.records) == 1


def test_any_error_is_logged_once_and_never_escapes(monkeypatch, caplog):
    async def broken():
        raise RuntimeError("warehouse down")

    monkeypatch.setattr(reconciler, "reconcile_task", broken)

    with caplog.at_level(logging.ERROR, logger="app.workers.poller"):
        for _ in range(3):
            asyncio.run(poller._safe_metadata_reconcile(asyncio.Semaphore(1)))

    assert sum("warehouse down" in r.getMessage() for r in caplog.records) == 1
