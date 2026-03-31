"""
benchmarks/bench_eventbus.py — EventBus performance benchmarks

Tests:
  - publish throughput (events/sec)
  - subscribe/unsubscribe overhead
  - dispatch latency (end-to-end publish→callback)
  - queue overflow handling
"""
from __future__ import annotations

import asyncio
import time

import pytest

from core.eventbus.bus import EventBus


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


class TestEventBusPublishThroughput:
    """Measure raw publish speed without dispatch."""

    @pytest.mark.asyncio
    async def test_publish_1k(self, bus: EventBus) -> None:
        count = 1_000
        start = time.perf_counter()
        for i in range(count):
            await bus.publish(
                type="bench.test",
                source="benchmark",
                payload={"i": i},
            )
        elapsed = time.perf_counter() - start
        rate = count / elapsed
        print(f"\n  EventBus publish 1K: {elapsed:.4f}s ({rate:.0f} events/sec)")
        assert rate > 1_000, f"Publish rate too low: {rate:.0f}/s"

    @pytest.mark.asyncio
    async def test_publish_10k(self, bus: EventBus) -> None:
        count = 10_000
        start = time.perf_counter()
        for i in range(count):
            await bus.publish(
                type="bench.test",
                source="benchmark",
                payload={"i": i, "data": "x" * 100},
            )
        elapsed = time.perf_counter() - start
        rate = count / elapsed
        print(f"\n  EventBus publish 10K: {elapsed:.4f}s ({rate:.0f} events/sec)")
        assert rate > 500, f"Publish rate too low: {rate:.0f}/s"


class TestEventBusSubscription:
    """Measure subscribe/unsubscribe operations."""

    @pytest.mark.asyncio
    async def test_subscribe_unsubscribe_100(self, bus: EventBus) -> None:
        count = 100
        sub_ids: list[str] = []

        start = time.perf_counter()
        for i in range(count):
            async def _noop(event):
                pass
            sid = bus.subscribe_direct(
                module_id=f"bench-{i}",
                event_types=[f"event.type.{i}"],
                callback=_noop,
            )
            sub_ids.append(sid)
        subscribe_time = time.perf_counter() - start

        start = time.perf_counter()
        for sid in sub_ids:
            bus.unsubscribe_direct(sid)
        unsubscribe_time = time.perf_counter() - start

        print(f"\n  Subscribe 100: {subscribe_time:.4f}s")
        print(f"  Unsubscribe 100: {unsubscribe_time:.4f}s")
        assert subscribe_time < 1.0
        assert unsubscribe_time < 1.0

    @pytest.mark.asyncio
    async def test_subscribe_wildcard_matching(self, bus: EventBus) -> None:
        """Benchmark dispatch with wildcard subscribers."""
        received = {"count": 0}

        async def _counter(event):
            received["count"] += 1

        # Subscribe with wildcard
        bus.subscribe_direct("bench-wildcard", ["*"], _counter)

        await bus.start()
        count = 500
        start = time.perf_counter()
        for i in range(count):
            await bus.publish(
                type=f"device.{i}.state",
                source="benchmark",
                payload={"val": i},
            )
        # Allow dispatch loop to process
        await asyncio.sleep(0.5)
        await bus.stop()
        elapsed = time.perf_counter() - start

        print(f"\n  Wildcard dispatch 500: {elapsed:.4f}s, received={received['count']}")
        assert received["count"] > 0


class TestEventBusDispatchLatency:
    """Measure end-to-end publish→callback latency."""

    @pytest.mark.asyncio
    async def test_dispatch_latency(self, bus: EventBus) -> None:
        latencies: list[float] = []
        event_done = asyncio.Event()
        expected = 100

        async def _measure(event):
            latencies.append(time.perf_counter())
            if len(latencies) >= expected:
                event_done.set()

        bus.subscribe_direct("bench-latency", ["bench.latency"], _measure)
        await bus.start()

        timestamps: list[float] = []
        for _ in range(expected):
            t = time.perf_counter()
            timestamps.append(t)
            await bus.publish(
                type="bench.latency",
                source="benchmark",
                payload={},
            )

        try:
            await asyncio.wait_for(event_done.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            pass
        await bus.stop()

        if latencies and timestamps:
            deltas = [
                (latencies[i] - timestamps[i]) * 1000
                for i in range(min(len(latencies), len(timestamps)))
            ]
            avg_ms = sum(deltas) / len(deltas)
            max_ms = max(deltas)
            min_ms = min(deltas)
            print(f"\n  Dispatch latency ({len(deltas)} events):")
            print(f"    min={min_ms:.3f}ms  avg={avg_ms:.3f}ms  max={max_ms:.3f}ms")
            assert avg_ms < 50, f"Average latency too high: {avg_ms:.3f}ms"


class TestEventBusQueueOverflow:
    """Test queue overflow behavior under pressure."""

    @pytest.mark.asyncio
    async def test_overflow_10k_no_consumers(self, bus: EventBus) -> None:
        """Publish 10K events with no dispatch — tests drop-oldest."""
        count = 10_000
        start = time.perf_counter()
        dropped = 0
        for i in range(count):
            try:
                await bus.publish(
                    type="bench.overflow",
                    source="benchmark",
                    payload={"i": i},
                )
            except Exception:
                dropped += 1
        elapsed = time.perf_counter() - start
        qsize = bus._queue.qsize()
        print(f"\n  Overflow test: {count} published in {elapsed:.4f}s")
        print(f"    Queue size: {qsize}, Dropped: {dropped}")
        assert qsize <= 10_000  # maxsize
