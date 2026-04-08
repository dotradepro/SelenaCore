"""
benchmarks/bench_module_bus.py — Module Bus performance benchmarks

Tests:
  - Intent index rebuild speed
  - Intent matching throughput
  - Subscription pattern matching
  - DropOldestQueue throughput
  - Intent conflict detection
  - Circuit breaker overhead
"""
from __future__ import annotations

import asyncio
import re
import time

import pytest

from core.module_bus import (
    BusConnection,
    DropOldestQueue,
    IntentEntry,
    ModuleBus,
    _matches_subscription,
)


class TestDropOldestQueue:
    """Benchmark DropOldestQueue operations."""

    @pytest.mark.asyncio
    async def test_put_get_10k(self) -> None:
        q = DropOldestQueue(maxsize=5000)
        count = 10_000

        start = time.perf_counter()
        for i in range(count):
            q.put_nowait(f"msg-{i}")
        put_time = time.perf_counter() - start

        # Queue should have dropped oldest
        qsize = q.qsize()
        start = time.perf_counter()
        drained = 0
        while not q.empty():
            q.get_nowait()
            drained += 1
        get_time = time.perf_counter() - start

        print(f"\n  DropOldestQueue put 10K: {put_time:.4f}s")
        print(f"    Queue size after overflow: {qsize}")
        print(f"    Drained {drained} in {get_time:.4f}s")
        assert qsize <= 5000

    @pytest.mark.asyncio
    async def test_concurrent_put(self) -> None:
        q = DropOldestQueue(maxsize=1000)
        count = 5_000

        async def _producer(prefix: str, n: int) -> None:
            for i in range(n):
                q.put_nowait(f"{prefix}-{i}")

        start = time.perf_counter()
        await asyncio.gather(
            _producer("a", count),
            _producer("b", count),
            _producer("c", count),
        )
        elapsed = time.perf_counter() - start
        print(f"\n  Concurrent put 3x{count}: {elapsed:.4f}s, qsize={q.qsize()}")
        assert q.qsize() <= 1000


class TestSubscriptionMatching:
    """Benchmark _matches_subscription function."""

    def test_exact_match_throughput(self) -> None:
        count = 100_000
        start = time.perf_counter()
        for _ in range(count):
            _matches_subscription("device.state_changed", "device.state_changed")
        elapsed = time.perf_counter() - start
        rate = count / elapsed
        print(f"\n  Exact match 100K: {elapsed:.4f}s ({rate:.0f} ops/sec)")
        assert rate > 100_000

    def test_wildcard_match_throughput(self) -> None:
        count = 100_000
        start = time.perf_counter()
        for _ in range(count):
            _matches_subscription("device.state_changed", "device.*")
        elapsed = time.perf_counter() - start
        rate = count / elapsed
        print(f"\n  Wildcard match 100K: {elapsed:.4f}s ({rate:.0f} ops/sec)")
        assert rate > 100_000

    def test_star_match_throughput(self) -> None:
        count = 100_000
        start = time.perf_counter()
        for _ in range(count):
            _matches_subscription("anything.here", "*")
        elapsed = time.perf_counter() - start
        rate = count / elapsed
        print(f"\n  Star match 100K: {elapsed:.4f}s ({rate:.0f} ops/sec)")
        assert rate > 100_000

    def test_no_match_throughput(self) -> None:
        count = 100_000
        start = time.perf_counter()
        for _ in range(count):
            _matches_subscription("device.state_changed", "module.started")
        elapsed = time.perf_counter() - start
        rate = count / elapsed
        print(f"\n  No-match 100K: {elapsed:.4f}s ({rate:.0f} ops/sec)")
        assert rate > 100_000


class TestIntentMatching:
    """Benchmark intent pattern matching."""

    def _make_bus_with_intents(self, n_modules: int, n_patterns: int) -> ModuleBus:
        bus = ModuleBus()
        for m in range(n_modules):
            conn = BusConnection(
                module=f"module-{m}",
                ws=None,  # type: ignore
                capabilities={
                    "intents": [
                        {
                            "priority": 50 - m,
                            "patterns": {
                                "uk": [
                                    f".*модуль{m}_патерн{p}.*"
                                    for p in range(n_patterns)
                                ],
                                "en": [
                                    f".*module{m}_pattern{p}.*"
                                    for p in range(n_patterns)
                                ],
                            },
                        }
                    ],
                    "subscriptions": [f"event.module{m}.*"],
                },
                permissions=set(),
                connected_at=time.monotonic(),
                last_pong=time.monotonic(),
            )
            bus._connections[f"module-{m}"] = conn
        bus._rebuild_intent_index()
        return bus

    def test_rebuild_index_10_modules(self) -> None:
        bus = ModuleBus()
        for m in range(10):
            conn = BusConnection(
                module=f"module-{m}",
                ws=None,  # type: ignore
                capabilities={
                    "intents": [
                        {
                            "priority": 50,
                            "patterns": {
                                "en": [f".*pattern{m}_{p}.*" for p in range(20)],
                                "uk": [f".*шаблон{m}_{p}.*" for p in range(20)],
                            },
                        }
                    ],
                },
                permissions=set(),
                connected_at=time.monotonic(),
                last_pong=time.monotonic(),
            )
            bus._connections[f"module-{m}"] = conn

        iterations = 100
        start = time.perf_counter()
        for _ in range(iterations):
            bus._rebuild_intent_index()
        elapsed = time.perf_counter() - start
        avg_ms = (elapsed / iterations) * 1000
        total_entries = len(bus._intent_index)
        print(f"\n  Rebuild index (10 modules, {total_entries} entries): avg={avg_ms:.3f}ms")
        assert avg_ms < 50

    def test_match_intents_5_modules(self) -> None:
        bus = self._make_bus_with_intents(5, 10)
        count = 1_000
        queries = [
            "module2_pattern5 do something",
            "unknown query here",
            "no match at all either",
            "no match at all",
        ]

        start = time.perf_counter()
        total_matches = 0
        for i in range(count):
            text = queries[i % len(queries)]
            matches = bus._match_intents(text)
            total_matches += len(matches)
        elapsed = time.perf_counter() - start
        rate = count / elapsed
        print(f"\n  Intent match 1K queries (5 modules): {elapsed:.4f}s ({rate:.0f} q/sec)")
        print(f"    Total matches: {total_matches}")
        assert rate > 100

    def test_match_intents_20_modules(self) -> None:
        bus = self._make_bus_with_intents(20, 10)
        count = 500

        start = time.perf_counter()
        for i in range(count):
            bus._match_intents(f"module10_pattern5 action {i}")
        elapsed = time.perf_counter() - start
        rate = count / elapsed
        idx_size = len(bus._intent_index)
        print(f"\n  Intent match 500 queries (20 modules, {idx_size} patterns): {elapsed:.4f}s ({rate:.0f} q/sec)")
        assert rate > 50


class TestIntentConflictDetection:
    """Benchmark conflict detection between modules."""

    def test_conflict_detection_10_modules(self) -> None:
        bus = ModuleBus()
        # Fill index with existing patterns
        for m in range(10):
            conn = BusConnection(
                module=f"existing-{m}",
                ws=None,  # type: ignore
                capabilities={
                    "intents": [
                        {
                            "priority": 50,
                            "patterns": {
                                "en": [f".*weather.*", f".*light.*", f".*temp.*"],
                            },
                        }
                    ],
                },
                permissions=set(),
                connected_at=time.monotonic(),
                last_pong=time.monotonic(),
            )
            bus._connections[f"existing-{m}"] = conn
        bus._rebuild_intent_index()

        new_intents = [
            {
                "priority": 50,
                "patterns": {
                    "en": [".*weather.*", ".*humidity.*", ".*forecast.*"],
                },
            }
        ]

        iterations = 100
        start = time.perf_counter()
        for _ in range(iterations):
            bus._detect_intent_conflicts("new-module", new_intents)
        elapsed = time.perf_counter() - start
        avg_ms = (elapsed / iterations) * 1000
        warnings = bus._detect_intent_conflicts("new-module", new_intents)
        print(f"\n  Conflict detection (10 modules): avg={avg_ms:.3f}ms, warnings={len(warnings)}")
        assert avg_ms < 10


class TestEventDeliveryToBus:
    """Benchmark event delivery to multiple bus-connected modules."""

    @pytest.mark.asyncio
    async def test_deliver_to_10_subscribers(self) -> None:
        bus = ModuleBus()
        for m in range(10):
            conn = BusConnection(
                module=f"sub-{m}",
                ws=None,  # type: ignore
                capabilities={
                    "subscriptions": ["device.*", "sensor.*"],
                },
                permissions=set(),
                connected_at=time.monotonic(),
                last_pong=time.monotonic(),
            )
            bus._connections[f"sub-{m}"] = conn

        count = 1_000
        start = time.perf_counter()
        for i in range(count):
            await bus.deliver_event_to_bus(
                source="core",
                event_type="device.state_changed",
                payload={"device_id": f"dev-{i}", "state": {"on": True}},
            )
        elapsed = time.perf_counter() - start
        rate = count / elapsed
        print(f"\n  Deliver to 10 subs (1K events): {elapsed:.4f}s ({rate:.0f} events/sec)")
        assert rate > 100

    @pytest.mark.asyncio
    async def test_deliver_no_match(self) -> None:
        bus = ModuleBus()
        for m in range(10):
            conn = BusConnection(
                module=f"sub-{m}",
                ws=None,  # type: ignore
                capabilities={
                    "subscriptions": ["module.specific.*"],
                },
                permissions=set(),
                connected_at=time.monotonic(),
                last_pong=time.monotonic(),
            )
            bus._connections[f"sub-{m}"] = conn

        count = 5_000
        start = time.perf_counter()
        for i in range(count):
            await bus.deliver_event_to_bus(
                source="core",
                event_type="device.state_changed",
                payload={"i": i},
            )
        elapsed = time.perf_counter() - start
        rate = count / elapsed
        print(f"\n  Deliver no-match 5K: {elapsed:.4f}s ({rate:.0f} events/sec)")
        assert rate > 500
