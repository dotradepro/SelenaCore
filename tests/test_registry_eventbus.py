"""
tests/test_registry_eventbus.py — Registry, EventBus, module_loader, FastMatcher tests
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

        async def handler(event):
            received.append(event)

        bus.subscribe_direct("test-module", ["test.event"], handler)
        await bus.publish("test.event", "test-source", {"key": "value"})
        await asyncio.sleep(0.1)

        assert len(received) == 1
        assert received[0].payload["key"] == "value"
        await bus.stop()

    @pytest.mark.asyncio
    async def test_unsubscribe(self):
        from core.eventbus.bus import EventBus
        bus = EventBus()
        await bus.start()

        received = []

        async def handler(event):
            received.append(event)

        sub_id = bus.subscribe_direct("test-module", ["test.event"], handler)
        bus.unsubscribe_direct(sub_id)
        await bus.publish("test.event", "test-source", {"x": 1})
        await asyncio.sleep(0.1)

        assert len(received) == 0
        await bus.stop()

    @pytest.mark.asyncio
    async def test_wildcard_subscription(self):
        from core.eventbus.bus import EventBus
        bus = EventBus()
        await bus.start()

        received = []

        async def handler(event):
            received.append(event)

        bus.subscribe_direct("test-module", ["*"], handler)
        await bus.publish("device.state_changed", "test-source", {"state": "on"})
        await asyncio.sleep(0.1)

        assert len(received) >= 1
        await bus.stop()

    @pytest.mark.asyncio
    async def test_webhook_subscription(self):
        from core.eventbus.bus import EventBus
        bus = EventBus()
        sub = bus.subscribe("mod-1", ["device.*"], "http://localhost:8100/webhook", secret="s3cret")
        assert sub.module_id == "mod-1"
        assert sub.webhook_url == "http://localhost:8100/webhook"
        assert sub.secret == "s3cret"

    @pytest.mark.asyncio
    async def test_event_create(self):
        from core.eventbus.bus import Event
        event = Event.create(type="test.event", source="core", payload={"a": 1})
        assert event.type == "test.event"
        assert event.source == "core"
        d = event.to_dict()
        assert "event_id" in d
        assert d["type"] == "test.event"


# ---- DeviceRegistry ----

class TestDeviceRegistry:
    @pytest.mark.asyncio
    async def test_create_and_get(self):
        from core.registry.service import DeviceRegistry
        from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
        from core.registry.models import Base

        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            async with session.begin():
                registry = DeviceRegistry(session)
                created = await registry.create(
                    name="Test Device",
                    type="sensor",
                    protocol="test",
                    capabilities=["temperature"],
                    meta={"address": "192.168.1.1"},
                )
                assert created.name == "Test Device"
                assert created.type == "sensor"

                fetched = await registry.get(created.device_id)
                assert fetched is not None
                assert fetched.name == "Test Device"

        await engine.dispose()

    @pytest.mark.asyncio
    async def test_update_state(self):
        from core.registry.service import DeviceRegistry
        from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
        from core.registry.models import Base

        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            async with session.begin():
                registry = DeviceRegistry(session)
                device = await registry.create(
                    name="Light",
                    type="actuator",
                    protocol="test",
                    capabilities=["on_off"],
                    meta={},
                )
                updated = await registry.update_state(device.device_id, {"on": True})
                assert updated.get_state() == {"on": True}

        await engine.dispose()

    @pytest.mark.asyncio
    async def test_delete(self):
        from core.registry.service import DeviceRegistry, DeviceNotFoundError
        from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
        from core.registry.models import Base

        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            async with session.begin():
                registry = DeviceRegistry(session)
                device = await registry.create(
                    name="Temp", type="sensor", protocol="test",
                    capabilities=[], meta={},
                )
                await registry.delete(device.device_id)
                assert await registry.get(device.device_id) is None

        await engine.dispose()

    @pytest.mark.asyncio
    async def test_delete_nonexistent_raises(self):
        from core.registry.service import DeviceRegistry, DeviceNotFoundError
        from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
        from core.registry.models import Base

        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            async with session.begin():
                registry = DeviceRegistry(session)
                with pytest.raises(DeviceNotFoundError):
                    await registry.delete("nonexistent-id")

        await engine.dispose()


# ---- Module Validator ----

class TestModuleValidator:
    def test_valid_manifest(self):
        from core.module_loader.validator import validate_manifest
        manifest = {
            "name": "test-module",
            "version": "1.0.0",
            "type": "UI",
            "api_version": "1",
            "permissions": ["devices.read"],
        }
        result = validate_manifest(manifest)
        assert result.valid is True
        assert result.errors == []

    def test_valid_manifest_with_deprecated_port(self):
        from core.module_loader.validator import validate_manifest
        manifest = {
            "name": "test-module",
            "version": "1.0.0",
            "type": "UI",
            "api_version": "1",
            "port": 8100,
            "permissions": ["devices.read"],
        }
        result = validate_manifest(manifest)
        assert result.valid is True  # port is deprecated but ignored

    def test_missing_required_fields(self):
        from core.module_loader.validator import validate_manifest
        result = validate_manifest({"name": "test"})
        assert result.valid is False
        assert any("api_version" in e for e in result.errors)

    def test_invalid_name_rejected(self):
        from core.module_loader.validator import validate_manifest
        manifest = {
            "name": "TEST MODULE!",
            "version": "1.0.0",
            "type": "UI",
            "api_version": "1",
            "permissions": [],
        }
        result = validate_manifest(manifest)
        assert result.valid is False
        assert any("name" in e.lower() for e in result.errors)

    def test_system_type_with_port_rejected(self):
        from core.module_loader.validator import validate_manifest
        manifest = {
            "name": "my-module",
            "version": "1.0.0",
            "type": "SYSTEM",
            "api_version": "1",
            "port": 8100,
            "permissions": [],
        }
        result = validate_manifest(manifest)
        assert result.valid is False
        assert any("system" in e.lower() or "port" in e.lower() for e in result.errors)

    def test_invalid_version_rejected(self):
        from core.module_loader.validator import validate_manifest
        manifest = {
            "name": "my-module",
            "version": "not-semver",
            "type": "UI",
            "api_version": "1",
            "permissions": [],
        }
        result = validate_manifest(manifest)
        assert result.valid is False
        assert any("version" in e.lower() for e in result.errors)

    def test_unknown_permission_rejected(self):
        from core.module_loader.validator import validate_manifest
        manifest = {
            "name": "my-module",
            "version": "1.0.0",
            "type": "UI",
            "api_version": "1",
            "permissions": ["devices.read", "admin.nuke"],
        }
        result = validate_manifest(manifest)
        assert result.valid is False
        assert any("permission" in e.lower() for e in result.errors)

    def test_bus_permissions_valid(self):
        from core.module_loader.validator import validate_manifest
        manifest = {
            "name": "my-module",
            "version": "1.0.0",
            "type": "UI",
            "api_version": "1",
            "permissions": ["devices.read", "devices.control", "events.publish", "modules.list"],
        }
        result = validate_manifest(manifest)
        assert result.valid is True


# ---- FastMatcher ----

class TestFastMatcher:
    def test_built_in_light_rule(self):
        from system_modules.llm_engine.fast_matcher import FastMatcher
        matcher = FastMatcher(rules_file="/nonexistent/path.yaml")
        result = matcher.match("turn on light")
        assert result is not None
        assert result.intent == "turn_on_light"
        assert result.action["type"] == "device.update_state"

    def test_no_match_returns_none(self):
        from system_modules.llm_engine.fast_matcher import FastMatcher
        matcher = FastMatcher(rules_file="/nonexistent/path.yaml")
        result = matcher.match("what is the meaning of life")
        assert result is None

    def test_privacy_rule(self):
        from system_modules.llm_engine.fast_matcher import FastMatcher
        matcher = FastMatcher(rules_file="/nonexistent/path.yaml")
        result = matcher.match("privacy on")
        assert result is not None
        assert result.intent == "privacy_on"

    def test_empty_string_returns_none(self):
        from system_modules.llm_engine.fast_matcher import FastMatcher
        matcher = FastMatcher(rules_file="/nonexistent/path.yaml")
        assert matcher.match("") is None
        assert matcher.match("   ") is None
