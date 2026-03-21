"""
tests/test_registry_eventbus.py — Registry, EventBus, module_loader, integrity agent tests
"""
from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ---- EventBus ----

class TestEventBus:
    @pytest.mark.asyncio
    async def test_publish_and_receive(self):
        from core.eventbus.bus import EventBus
        bus = EventBus()
        await bus.start()

        received = []

        async def handler(payload):
            received.append(payload)

        bus.subscribe("test.event", handler)
        await bus.publish("test.event", {"key": "value"})
        await asyncio.sleep(0.05)

        assert len(received) == 1
        assert received[0]["key"] == "value"
        await bus.stop()

    @pytest.mark.asyncio
    async def test_unsubscribe(self):
        from core.eventbus.bus import EventBus
        bus = EventBus()
        await bus.start()

        received = []
        async def handler(payload):
            received.append(payload)

        bus.subscribe("test.event", handler)
        bus.unsubscribe("test.event", handler)
        await bus.publish("test.event", {"x": 1})
        await asyncio.sleep(0.05)

        assert len(received) == 0
        await bus.stop()

    @pytest.mark.asyncio
    async def test_wildcard_subscription(self):
        from core.eventbus.bus import EventBus
        bus = EventBus()
        await bus.start()

        received = []
        async def handler(payload):
            received.append(payload)

        bus.subscribe("device.*", handler)
        await bus.publish("device.state_changed", {"state": "on"})
        await asyncio.sleep(0.05)

        assert len(received) >= 1
        await bus.stop()


# ---- DeviceRegistry ----

class TestDeviceRegistry:
    @pytest.mark.asyncio
    async def test_create_and_get(self):
        from core.registry.service import DeviceRegistry
        from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
        from sqlalchemy.orm import sessionmaker
        from core.registry.models import Base

        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async_session = sessionmaker(engine, class_=AsyncSession)
        registry = DeviceRegistry(async_session)

        device_data = {
            "name": "Test Device",
            "device_type": "sensor",
            "protocol": "test",
            "address": "192.168.1.1",
            "state": "active",
        }

        async with async_session() as session:
            created = await registry.create_device(session, device_data)
            assert created.name == "Test Device"

            fetched = await registry.get_device(session, created.id)
            assert fetched is not None
            assert fetched.device_type == "sensor"

        await engine.dispose()


# ---- Module Validator ----

class TestModuleValidator:
    def test_valid_manifest(self):
        from core.module_loader.validator import validate_manifest
        manifest = {
            "name": "test-module",
            "version": "1.0.0",
            "type": "COMMUNITY",
            "port": 8100,
            "permissions": ["devices:read"],
            "min_core_version": "1.0.0",
        }
        errors = validate_manifest(manifest)
        assert errors == []

    def test_invalid_name_rejected(self):
        from core.module_loader.validator import validate_manifest
        manifest = {
            "name": "TEST MODULE!",  # invalid
            "version": "1.0.0",
            "type": "COMMUNITY",
            "port": 8100,
            "permissions": [],
        }
        errors = validate_manifest(manifest)
        assert any("name" in e.lower() for e in errors)

    def test_system_type_rejected_from_upload(self):
        from core.module_loader.validator import validate_manifest
        manifest = {
            "name": "my-module",
            "version": "1.0.0",
            "type": "SYSTEM",  # Only built-in modules can be SYSTEM
            "port": 8100,
            "permissions": [],
        }
        errors = validate_manifest(manifest)
        assert any("system" in e.lower() or "type" in e.lower() for e in errors)

    def test_port_out_of_range_rejected(self):
        from core.module_loader.validator import validate_manifest
        manifest = {
            "name": "my-module",
            "version": "1.0.0",
            "type": "COMMUNITY",
            "port": 9999,  # Not in 8100-8200
            "permissions": [],
        }
        errors = validate_manifest(manifest)
        assert any("port" in e.lower() for e in errors)


# ---- FastMatcher ----

class TestFastMatcher:
    def test_built_in_light_rule(self):
        from system_modules.llm_engine.fast_matcher import FastMatcher
        matcher = FastMatcher(rules=[])
        result = matcher.match("turn on the lights")
        assert result is not None
        assert result.action in ("light.turn_on", "light.on")

    def test_no_match_returns_none(self):
        from system_modules.llm_engine.fast_matcher import FastMatcher
        matcher = FastMatcher(rules=[])
        result = matcher.match("what is the meaning of life")
        assert result is None
