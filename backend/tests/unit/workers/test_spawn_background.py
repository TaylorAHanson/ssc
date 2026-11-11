"""Background jobs start from the event loop or from a sync endpoint's worker thread."""
import asyncio
import threading

import pytest

from app.workers import poller


@pytest.fixture(autouse=True)
def _no_main_loop(monkeypatch):
    monkeypatch.setattr(poller, "_main_loop", None)


def test_on_the_loop_returns_a_tracked_task():
    ran = []

    async def job():
        ran.append(threading.current_thread().name)

    async def main():
        task = poller._spawn_background(job(), "t")
        assert isinstance(task, asyncio.Task)
        await task

    asyncio.run(main())
    assert ran


def test_from_a_worker_thread_runs_on_the_server_loop():
    seen = {}

    async def job():
        seen["loop"] = asyncio.get_running_loop()

    async def main():
        loop = asyncio.get_running_loop()
        poller.set_main_loop(loop)
        # A sync endpoint runs in a worker thread, where there is no running loop.
        assert await asyncio.to_thread(poller._spawn_background, job(), "review") is None
        for _ in range(50):
            if "loop" in seen:
                break
            await asyncio.sleep(0.01)
        assert seen["loop"] is loop

    asyncio.run(main())


def test_with_no_server_loop_runs_on_its_own_thread():
    done = threading.Event()

    async def job():
        done.set()

    assert poller._spawn_background(job(), "script") is None
    assert done.wait(2)
