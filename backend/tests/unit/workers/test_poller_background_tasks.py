"""Detached upkeep tasks are strongly referenced until they finish.

asyncio keeps only a weak reference to a task, so a fire-and-forget task with no
other reference can be garbage-collected mid-run and die silently.
"""

import asyncio
import gc

from app.services import user_context
from app.workers import poller


def test_spawn_background_holds_task_until_done():
    async def scenario():
        started = asyncio.Event()
        release = asyncio.Event()

        async def job():
            started.set()
            await release.wait()
            return "done"

        task = poller._spawn_background(job())
        task_id = id(task)
        del task
        await started.wait()
        gc.collect()

        held = [t for t in poller._background_tasks if id(t) == task_id]
        assert held, "running task must be referenced by _background_tasks"

        release.set()
        result = await held[0]
        await asyncio.sleep(0)  # let the done-callback run
        return result, any(id(t) == task_id for t in poller._background_tasks)

    result, still_held = asyncio.run(scenario())
    assert result == "done"
    assert not still_held, "finished task must be dropped from _background_tasks"


def test_user_context_refresh_task_is_strongly_referenced(monkeypatch):
    async def scenario():
        release = asyncio.Event()

        async def fake_refresh(identity, sections=None):
            await release.wait()

        monkeypatch.setattr(user_context, "refresh_profile", fake_refresh)
        user_context._schedule_refresh(user_context.UserIdentity(email="u@example.com"))
        await asyncio.sleep(0)
        gc.collect()
        held = len(user_context._REFRESH_TASKS)

        release.set()
        await asyncio.gather(*list(user_context._REFRESH_TASKS))
        await asyncio.sleep(0)
        return held, len(user_context._REFRESH_TASKS)

    held, after = asyncio.run(scenario())
    assert held == 1
    assert after == 0
