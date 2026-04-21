"""Tests for core.ble.arbiter — fair FIFO + persistent reservations.

These tests never touch real BLE; they verify the scheduling guarantees
we care about:
    - two concurrent slots run one at a time
    - arrival order is preserved
    - timeout raises BLEBusy and releases the queue slot
    - persistent reservations can lease the adapter to queued callers
"""
from __future__ import annotations

import asyncio

import pytest

from core.ble.arbiter import BLEArbiter, BLEBusy


@pytest.mark.asyncio
async def test_single_slot_runs_immediately():
    arb = BLEArbiter()
    ran = False
    async with arb.slot("scanner"):
        ran = True
    assert ran
    assert arb.current_owner() is None
    assert arb.queue_depth() == 0


@pytest.mark.asyncio
async def test_second_slot_waits_for_first():
    """If A is inside the slot, B must block until A exits."""
    arb = BLEArbiter()
    order: list[str] = []
    a_entered = asyncio.Event()
    a_proceed = asyncio.Event()

    async def a():
        async with arb.slot("A"):
            order.append("A-in")
            a_entered.set()
            await a_proceed.wait()
            order.append("A-out")

    async def b():
        await a_entered.wait()
        async with arb.slot("B"):
            order.append("B-in")
            order.append("B-out")

    task_a = asyncio.create_task(a())
    task_b = asyncio.create_task(b())
    await a_entered.wait()
    # B is queued and MUST NOT have entered yet.
    await asyncio.sleep(0.05)
    assert order == ["A-in"]
    assert arb.queue_depth() == 1
    assert arb.queued_owners() == ["B"]

    a_proceed.set()
    await asyncio.gather(task_a, task_b)
    assert order == ["A-in", "A-out", "B-in", "B-out"]
    assert arb.queue_depth() == 0


@pytest.mark.asyncio
async def test_fifo_order_preserved_across_many_waiters():
    arb = BLEArbiter()
    order: list[str] = []
    start = asyncio.Event()

    async def worker(name):
        await start.wait()
        async with arb.slot(name):
            order.append(name)
            await asyncio.sleep(0.01)

    tasks = [asyncio.create_task(worker(f"w{i}")) for i in range(5)]
    # Stagger the start so each call hits the arbiter in strict order.
    await asyncio.sleep(0)
    start.set()
    # Wait until all have queued up. First worker needs a moment to
    # enter the slot; others pile up in the queue.
    for _ in range(20):
        if arb.queue_depth() >= 4:
            break
        await asyncio.sleep(0.005)
    await asyncio.gather(*tasks)
    # Arrival order should be preserved.
    assert order == sorted(order)


@pytest.mark.asyncio
async def test_timeout_raises_and_cleans_up_queue():
    arb = BLEArbiter()
    proceed = asyncio.Event()

    async def holder():
        async with arb.slot("holder"):
            await proceed.wait()

    t = asyncio.create_task(holder())
    await asyncio.sleep(0)   # let the holder acquire
    with pytest.raises(BLEBusy, match="timed out"):
        async with arb.slot("waiter", timeout=0.05):
            pass
    # Queue must have dropped the timed-out ticket.
    assert arb.queue_depth() == 0
    proceed.set()
    await t


@pytest.mark.asyncio
async def test_release_wakes_next_waiter_not_the_cancelled_one():
    arb = BLEArbiter()
    order: list[str] = []
    holder_done = asyncio.Event()

    async def holder():
        async with arb.slot("holder"):
            await holder_done.wait()

    async def cancelled_waiter():
        try:
            async with arb.slot("cancelled", timeout=10):
                order.append("cancelled-ran")
        except asyncio.CancelledError:
            order.append("cancelled-cancelled")
            raise

    async def good_waiter():
        async with arb.slot("good"):
            order.append("good-ran")

    t_hold = asyncio.create_task(holder())
    await asyncio.sleep(0)
    t_cancel = asyncio.create_task(cancelled_waiter())
    await asyncio.sleep(0)
    t_good = asyncio.create_task(good_waiter())
    await asyncio.sleep(0)

    # Cancel the middle waiter, then let the holder release.
    t_cancel.cancel()
    holder_done.set()
    with pytest.raises(asyncio.CancelledError):
        await t_cancel
    await asyncio.gather(t_hold, t_good)
    # "good" must have run despite "cancelled" sitting in the queue.
    assert "good-ran" in order
    assert "cancelled-ran" not in order


@pytest.mark.asyncio
async def test_reservation_lets_queued_waiters_run_via_lease():
    arb = BLEArbiter()
    events: list[str] = []
    waiter_ran = asyncio.Event()

    async def waiter():
        async with arb.slot("scanner"):
            events.append("scanner")
            waiter_ran.set()

    async def gateway():
        async with arb.reserve("plejd") as res:
            events.append("plejd-acquired")
            # Schedule a scanner that wants in.
            asyncio.create_task(waiter())
            # Wait a tick so the scanner queues.
            for _ in range(20):
                if arb.queue_depth() >= 1:
                    break
                await asyncio.sleep(0.005)
            assert arb.queue_depth() == 1
            async with res.lease():
                pass   # release → scanner runs → re-acquire
            events.append("plejd-resumed")

    await gateway()
    assert events[0] == "plejd-acquired"
    assert "scanner" in events
    assert events[-1] == "plejd-resumed"
    # Arbiter should be empty at the end.
    assert arb.queue_depth() == 0
    assert arb.current_owner() is None


@pytest.mark.asyncio
async def test_reservation_lease_is_noop_when_queue_empty():
    arb = BLEArbiter()
    async with arb.reserve("plejd") as res:
        async with res.lease():
            # No queued waiter — the lease body runs without releasing.
            assert arb.current_owner() == "plejd"
    assert arb.current_owner() is None
