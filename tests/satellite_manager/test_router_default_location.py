"""Targeted test for the default_location path in IntentRouter._disambiguate_device.

We only exercise the resolver branch — the full route() pipeline pulls in
the embedding classifier + LLM which we don't want to spin up for a unit
test. The resolver is isolated enough that we can call it directly with a
stubbed DeviceRegistry.
"""
from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

if sys.version_info < (3, 10):
    pytest.skip(
        "intent_router needs Python 3.10+ (SQLAlchemy Mapped[...] annotations)",
        allow_module_level=True,
    )

pytest.importorskip("sqlalchemy")


class FakeDevice:
    def __init__(self, device_id: str, name: str, entity_type: str, location: str):
        self.device_id = device_id
        self.name = name
        self.entity_type = entity_type
        self.location = location


class FakeRegistry:
    def __init__(self, devices: list[FakeDevice]) -> None:
        self._devices = devices
        self.last_query: dict | None = None

    async def query(self, entity_type=None, location=None, keyword=None):
        self.last_query = {"entity_type": entity_type, "location": location}
        return [
            d for d in self._devices
            if (entity_type is None or d.entity_type == entity_type)
            and (location is None or d.location == location)
        ]


class FakeSession:
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False


def _fake_session_factory():
    return FakeSession()


class FakeSandbox:
    def __init__(self, session_factory):
        self._session_factory = session_factory


async def test_default_location_fills_missing_location():
    """When classifier did not extract a location and the satellite's room
    is passed via default_location, the resolver scopes its DB query to it."""
    from system_modules.llm_engine.intent_router import IntentResult, IntentRouter

    devices = [
        FakeDevice("dev_k1", "lamp", "light", "kitchen"),
        FakeDevice("dev_b1", "lamp", "light", "bedroom"),
    ]
    registry = FakeRegistry(devices)

    def _make_registry(_session): return registry
    with patch(
        "system_modules.llm_engine.intent_router.DeviceRegistry",
        side_effect=_make_registry,
        create=False,
    ), patch(
        "core.module_loader.sandbox.get_sandbox",
        return_value=FakeSandbox(_fake_session_factory),
    ):
        router = IntentRouter()
        result = IntentResult(
            intent="device.on", params={"entity": "light"}, source="test", lang="en",
        )
        out = await router._disambiguate_device(result, "en", default_location="kitchen")

    # Resolver scoped the registry query to kitchen
    assert registry.last_query == {"entity_type": "light", "location": "kitchen"}
    # Single kitchen device → injected as device_id
    assert out.params.get("device_id") == "dev_k1"
    # Location written back into params so downstream modules see it
    assert out.params.get("location") == "kitchen"


async def test_explicit_location_overrides_default_location():
    """User said 'в спальні' — classifier extracted location=bedroom. The
    satellite's kitchen default must NOT override it."""
    from system_modules.llm_engine.intent_router import IntentResult, IntentRouter

    devices = [
        FakeDevice("dev_k1", "lamp", "light", "kitchen"),
        FakeDevice("dev_b1", "lamp", "light", "bedroom"),
    ]
    registry = FakeRegistry(devices)

    def _make_registry(_session): return registry
    with patch(
        "system_modules.llm_engine.intent_router.DeviceRegistry",
        side_effect=_make_registry,
        create=False,
    ), patch(
        "core.module_loader.sandbox.get_sandbox",
        return_value=FakeSandbox(_fake_session_factory),
    ):
        router = IntentRouter()
        result = IntentResult(
            intent="device.on",
            params={"entity": "light", "location": "bedroom"},
            source="test", lang="en",
        )
        out = await router._disambiguate_device(result, "en", default_location="kitchen")

    assert registry.last_query == {"entity_type": "light", "location": "bedroom"}
    assert out.params.get("device_id") == "dev_b1"
    assert out.params.get("location") == "bedroom"  # unchanged


async def test_no_default_no_location_falls_through_to_clarification():
    """Without either source of location, and N matches across rooms, the
    resolver should still set result.clarification (existing behavior)."""
    from system_modules.llm_engine.intent_router import IntentResult, IntentRouter

    devices = [
        FakeDevice("dev_k1", "lamp", "light", "kitchen"),
        FakeDevice("dev_b1", "lamp", "light", "bedroom"),
    ]
    registry = FakeRegistry(devices)

    def _make_registry(_session): return registry
    with patch(
        "system_modules.llm_engine.intent_router.DeviceRegistry",
        side_effect=_make_registry,
        create=False,
    ), patch(
        "core.module_loader.sandbox.get_sandbox",
        return_value=FakeSandbox(_fake_session_factory),
    ):
        router = IntentRouter()
        result = IntentResult(
            intent="device.on", params={"entity": "light"}, source="test", lang="en",
        )
        out = await router._disambiguate_device(result, "en", default_location=None)

    assert registry.last_query == {"entity_type": "light", "location": None}
    assert out.clarification is not None
    assert out.clarification["reason"] == "ambiguous_device"
    assert set(out.clarification["rooms"]) == {"kitchen", "bedroom"}
