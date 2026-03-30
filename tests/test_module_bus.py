"""
tests/test_module_bus.py — Module Bus unit tests
"""
from __future__ import annotations

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestModuleBus:
    def test_singleton(self):
        from core.module_bus import get_module_bus, _bus
        bus1 = get_module_bus()
        bus2 = get_module_bus()
        assert bus1 is bus2

    def test_is_connected_false(self):
        from core.module_bus import ModuleBus
        bus = ModuleBus()
        assert bus.is_connected("nonexistent") is False

    def test_list_modules_empty(self):
        from core.module_bus import ModuleBus
        bus = ModuleBus()
        assert bus.list_modules() == []

    def test_get_module_capabilities_none(self):
        from core.module_bus import ModuleBus
        bus = ModuleBus()
        assert bus.get_module_capabilities("nonexistent") is None


class TestDropOldestQueue:
    def test_put_and_get(self):
        from core.module_bus import DropOldestQueue
        q = DropOldestQueue(maxsize=3)
        q.put_nowait("a")
        q.put_nowait("b")
        assert q.qsize() == 2
        assert not q.empty()

    def test_overflow_drops_oldest(self):
        from core.module_bus import DropOldestQueue
        q = DropOldestQueue(maxsize=2)
        q.put_nowait("a")
        q.put_nowait("b")
        q.put_nowait("c")  # should drop "a"
        assert q.qsize() == 2
        assert q.get_nowait() == "b"
        assert q.get_nowait() == "c"


class TestMatchesSubscription:
    def test_exact_match(self):
        from core.module_bus import _matches_subscription
        assert _matches_subscription("device.state_changed", "device.state_changed") is True

    def test_no_match(self):
        from core.module_bus import _matches_subscription
        assert _matches_subscription("device.state_changed", "module.started") is False

    def test_wildcard_all(self):
        from core.module_bus import _matches_subscription
        assert _matches_subscription("anything.here", "*") is True

    def test_wildcard_prefix(self):
        from core.module_bus import _matches_subscription
        assert _matches_subscription("device.state_changed", "device.*") is True
        assert _matches_subscription("device.offline", "device.*") is True
        assert _matches_subscription("module.started", "device.*") is False

    def test_wildcard_prefix_exact(self):
        from core.module_bus import _matches_subscription
        assert _matches_subscription("device", "device.*") is True


class TestIntentIndex:
    def test_rebuild_and_match(self):
        from core.module_bus import ModuleBus, BusConnection
        import time

        bus = ModuleBus()
        conn = BusConnection(
            module="weather",
            ws=MagicMock(),
            capabilities={
                "intents": [
                    {
                        "patterns": {"en": ["weather", "forecast"], "uk": ["погода"]},
                        "priority": 50,
                    }
                ],
                "subscriptions": [],
            },
            permissions=set(),
            connected_at=time.monotonic(),
            last_pong=time.monotonic(),
        )
        bus._connections["weather"] = conn
        bus._rebuild_intent_index()

        matches = bus._match_intents("what's the weather", "en")
        assert len(matches) >= 1
        assert matches[0].module == "weather"

    def test_no_match(self):
        from core.module_bus import ModuleBus, BusConnection
        import time

        bus = ModuleBus()
        conn = BusConnection(
            module="weather",
            ws=MagicMock(),
            capabilities={
                "intents": [
                    {"patterns": {"en": ["weather"]}, "priority": 50}
                ],
            },
            permissions=set(),
            connected_at=time.monotonic(),
            last_pong=time.monotonic(),
        )
        bus._connections["weather"] = conn
        bus._rebuild_intent_index()

        matches = bus._match_intents("play music", "en")
        assert matches == []

    def test_language_fallback_to_en(self):
        from core.module_bus import ModuleBus, BusConnection
        import time

        bus = ModuleBus()
        conn = BusConnection(
            module="weather",
            ws=MagicMock(),
            capabilities={
                "intents": [
                    {"patterns": {"en": ["weather"]}, "priority": 50}
                ],
            },
            permissions=set(),
            connected_at=time.monotonic(),
            last_pong=time.monotonic(),
        )
        bus._connections["weather"] = conn
        bus._rebuild_intent_index()

        # No "fr" patterns — should fallback to "en"
        matches = bus._match_intents("weather", "fr")
        assert len(matches) >= 1

    def test_priority_sorting(self):
        from core.module_bus import ModuleBus, BusConnection
        import time

        bus = ModuleBus()
        for name, prio in [("low-prio", 90), ("high-prio", 10)]:
            conn = BusConnection(
                module=name,
                ws=MagicMock(),
                capabilities={
                    "intents": [
                        {"patterns": {"en": ["test"]}, "priority": prio}
                    ],
                },
                permissions=set(),
                connected_at=time.monotonic(),
                last_pong=time.monotonic(),
            )
            bus._connections[name] = conn

        bus._rebuild_intent_index()
        matches = bus._match_intents("test", "en")
        assert len(matches) == 2
        assert matches[0].module == "high-prio"
        assert matches[1].module == "low-prio"


class TestCircuitBreaker:
    def test_circuit_initially_closed(self):
        from core.module_bus import ModuleBus, BusConnection
        import time

        bus = ModuleBus()
        conn = BusConnection(
            module="test",
            ws=MagicMock(),
            capabilities={},
            permissions=set(),
            connected_at=time.monotonic(),
            last_pong=time.monotonic(),
        )
        bus._connections["test"] = conn
        assert bus._is_circuit_open("test") is False

    def test_open_circuit(self):
        from core.module_bus import ModuleBus, BusConnection
        import time

        bus = ModuleBus()
        conn = BusConnection(
            module="test",
            ws=MagicMock(),
            capabilities={},
            permissions=set(),
            connected_at=time.monotonic(),
            last_pong=time.monotonic(),
        )
        bus._connections["test"] = conn
        bus._open_circuit("test")
        assert bus._is_circuit_open("test") is True

    def test_circuit_not_open_for_unknown(self):
        from core.module_bus import ModuleBus
        bus = ModuleBus()
        assert bus._is_circuit_open("unknown") is False


class TestIntentConflictDetection:
    def test_detect_conflict(self):
        from core.module_bus import ModuleBus, BusConnection
        import time

        bus = ModuleBus()
        conn = BusConnection(
            module="existing",
            ws=MagicMock(),
            capabilities={
                "intents": [
                    {"patterns": {"en": ["weather"]}, "priority": 50}
                ],
            },
            permissions=set(),
            connected_at=time.monotonic(),
            last_pong=time.monotonic(),
        )
        bus._connections["existing"] = conn
        bus._rebuild_intent_index()

        warnings = bus._detect_intent_conflicts(
            "new-module",
            [{"patterns": {"en": ["weather forecast"]}, "priority": 50}],
        )
        assert len(warnings) >= 1
        assert "intent_conflict" in warnings[0]

    def test_no_conflict(self):
        from core.module_bus import ModuleBus
        bus = ModuleBus()
        warnings = bus._detect_intent_conflicts(
            "new-module",
            [{"patterns": {"en": ["completely_unique"]}, "priority": 50}],
        )
        assert warnings == []
