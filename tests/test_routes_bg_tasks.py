"""Regression test for the strong-reference set used by fire-and-forget
background tasks in device_control/routes.py.

The asyncio docs are explicit: tasks created with ``create_task`` are kept
alive only by a weak reference inside the event loop. If the spawning
function returns and nothing else holds the task, the GC can collect it
mid-execution. ``_BG_TASKS`` exists exactly to prevent that for the Matter
pattern-regen path.
"""
from __future__ import annotations

import asyncio
import gc

import pytest

from system_modules.device_control.routes import _BG_TASKS, _spawn_bg


@pytest.mark.asyncio
async def test_bg_task_strong_ref_survives_gc():
    """A task with no caller-side reference must NOT be GC'd before it runs."""
    started = asyncio.Event()
    finished = asyncio.Event()

    async def slow():
        started.set()
        await asyncio.sleep(0.05)
        finished.set()

    # Spawn and immediately drop our local reference. Then trigger GC.
    _spawn_bg(slow(), name="test_bg")
    gc.collect()

    await asyncio.wait_for(started.wait(), timeout=1.0)
    await asyncio.wait_for(finished.wait(), timeout=1.0)


@pytest.mark.asyncio
async def test_bg_task_self_removes_from_set_on_completion():
    """Done-callback must clean up the set so it doesn't grow unboundedly."""
    snapshot_before = set(_BG_TASKS)

    async def quick():
        return None

    task = _spawn_bg(quick(), name="cleanup_test")
    assert task in _BG_TASKS  # registered immediately
    await task
    # add_done_callback runs synchronously when the task is awaited, but
    # asyncio queues callbacks via call_soon — yield once so they fire.
    await asyncio.sleep(0)
    assert task not in _BG_TASKS
    # Set returned to baseline.
    assert set(_BG_TASKS) == snapshot_before


@pytest.mark.asyncio
async def test_bg_task_exception_is_logged_not_raised():
    """A failing background task must not crash the event loop or leak
    into other tasks — exceptions are surfaced via the done-callback path."""
    async def boom():
        raise RuntimeError("intentional")

    task = _spawn_bg(boom(), name="boom")
    # Awaiting the task surfaces the exception locally — but the live
    # contract is "fire-and-forget caller never awaits". Verify that the
    # task does complete (i.e. it ran) and is no longer in the set.
    with pytest.raises(RuntimeError, match="intentional"):
        await task
    await asyncio.sleep(0)
    assert task not in _BG_TASKS
