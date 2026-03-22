"""tests/test_import_adapters.py — pytest tests for import_adapters module."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest


# ── HA Adapter ────────────────────────────────────────────────────────────────

class TestHAAdapter:
    def _make_adapter(self, url="http://ha.local:8123", token="tok"):
        from system_modules.import_adapters.ha_adapter import HomeAssistantAdapter
        return HomeAssistantAdapter(url, token)

    def test_invalid_scheme_raises(self):
        from system_modules.import_adapters.ha_adapter import HomeAssistantAdapter
        with pytest.raises(ValueError, match="scheme"):
            HomeAssistantAdapter("ftp://ha.local", "tok")

    def test_invalid_scheme_no_scheme_raises(self):
        from system_modules.import_adapters.ha_adapter import HomeAssistantAdapter
        with pytest.raises(ValueError):
            HomeAssistantAdapter("ha.local:8123", "tok")

    @pytest.mark.asyncio
    async def test_test_connection_success(self):
        adapter = self._make_adapter()
        req = httpx.Request("GET", "http://ha.local:8123/api/")
        mock_resp = httpx.Response(200, request=req)
        with patch("httpx.AsyncClient") as mc:
            inst = AsyncMock()
            mc.return_value.__aenter__.return_value = inst
            mc.return_value.__aexit__ = AsyncMock(return_value=False)
            inst.get.return_value = mock_resp
            result = await adapter.test_connection()
        assert result is True

    @pytest.mark.asyncio
    async def test_test_connection_failure_on_exception(self):
        adapter = self._make_adapter()
        with patch("httpx.AsyncClient") as mc:
            inst = AsyncMock()
            mc.return_value.__aenter__.return_value = inst
            mc.return_value.__aexit__ = AsyncMock(return_value=False)
            inst.get.side_effect = httpx.ConnectError("refused")
            result = await adapter.test_connection()
        assert result is False

    @pytest.mark.asyncio
    async def test_test_connection_failure_on_non_200(self):
        adapter = self._make_adapter()
        req = httpx.Request("GET", "http://ha.local:8123/api/")
        mock_resp = httpx.Response(401, request=req)
        with patch("httpx.AsyncClient") as mc:
            inst = AsyncMock()
            mc.return_value.__aenter__.return_value = inst
            mc.return_value.__aexit__ = AsyncMock(return_value=False)
            inst.get.return_value = mock_resp
            result = await adapter.test_connection()
        assert result is False

    @pytest.mark.asyncio
    async def test_get_entities_parses_response(self):
        from system_modules.import_adapters.ha_adapter import HAEntity
        adapter = self._make_adapter()
        states = [
            {
                "entity_id": "light.kitchen",
                "state": "on",
                "attributes": {"friendly_name": "Kitchen Light", "brightness": 200},
            },
            {
                "entity_id": "sensor.temperature",
                "state": "22.3",
                "attributes": {"friendly_name": "Temp Sensor"},
            },
        ]
        req = httpx.Request("GET", "http://ha.local:8123/api/states")
        mock_resp = httpx.Response(200, json=states, request=req)
        with patch("httpx.AsyncClient") as mc:
            inst = AsyncMock()
            mc.return_value.__aenter__.return_value = inst
            mc.return_value.__aexit__ = AsyncMock(return_value=False)
            inst.get.return_value = mock_resp
            entities = await adapter.get_entities()
        assert len(entities) == 2
        assert entities[0].entity_id == "light.kitchen"
        assert entities[0].domain == "light"
        assert entities[0].friendly_name == "Kitchen Light"

    def test_to_selena_devices_converts_correctly(self):
        from system_modules.import_adapters.ha_adapter import HAEntity, HomeAssistantAdapter
        adapter = HomeAssistantAdapter("http://ha.local:8123", "tok")
        entities = [
            HAEntity(
                entity_id="switch.fan",
                friendly_name="Fan",
                state="off",
                domain="switch",
                attributes={"icon": "mdi:fan"},
            )
        ]
        devices = adapter.to_selena_devices(entities)
        assert len(devices) == 1
        dev = devices[0]
        assert dev["name"] == "Fan"
        assert dev["protocol"] == "ha_rest"
        assert dev["meta"]["source"] == "home_assistant"
        assert dev["meta"]["entity_id"] == "switch.fan"

    @pytest.mark.asyncio
    async def test_call_service_success(self):
        adapter = self._make_adapter()
        req = httpx.Request("POST", "http://ha.local:8123/api/services/light/turn_on")
        mock_resp = httpx.Response(200, request=req)
        with patch("httpx.AsyncClient") as mc:
            inst = AsyncMock()
            mc.return_value.__aenter__.return_value = inst
            mc.return_value.__aexit__ = AsyncMock(return_value=False)
            inst.post.return_value = mock_resp
            ok = await adapter.call_service("light", "turn_on", {"entity_id": "light.kitchen"})
        assert ok is True


# ── Hue Adapter ───────────────────────────────────────────────────────────────

class TestHueAdapter:
    def _make_adapter(self):
        from system_modules.import_adapters.hue_adapter import HueAdapter, HueBridge
        bridge = HueBridge(bridge_id="abc123", ip="192.168.1.10", username="test-app-key")
        return HueAdapter(bridge), bridge

    @pytest.mark.asyncio
    async def test_get_lights_parses_response(self):
        adapter, _ = self._make_adapter()
        data = {
            "data": [
                {
                    "id": "light-uuid-1",
                    "metadata": {"name": "Living Room"},
                    "on": {"on": True},
                    "dimming": {"brightness": 80},
                    "status": {"connectivity": {"status": "connected"}},
                },
                {
                    "id": "light-uuid-2",
                    "metadata": {"name": "Bedroom"},
                    "on": {"on": False},
                    "dimming": {"brightness": 0},
                    "status": {"connectivity": {"status": "disconnected"}},
                    "color": {"xy": {"x": 0.3, "y": 0.4}},
                },
            ]
        }
        req = httpx.Request("GET", "https://192.168.1.10/clip/v2/resource/light")
        mock_resp = httpx.Response(200, json=data, request=req)
        with patch("httpx.AsyncClient") as mc:
            inst = AsyncMock()
            mc.return_value.__aenter__.return_value = inst
            mc.return_value.__aexit__ = AsyncMock(return_value=False)
            inst.get.return_value = mock_resp
            lights = await adapter.get_lights()
        assert len(lights) == 2
        assert lights[0].name == "Living Room"
        assert lights[0].on is True
        assert lights[0].brightness == 80
        assert lights[0].reachable is True
        assert lights[1].color_xy == (0.3, 0.4)

    @pytest.mark.asyncio
    async def test_set_light_state(self):
        adapter, bridge = self._make_adapter()
        req = httpx.Request("PUT", f"https://{bridge.ip}/clip/v2/resource/light/light-uuid-1")
        mock_resp = httpx.Response(200, request=req)
        with patch("httpx.AsyncClient") as mc:
            inst = AsyncMock()
            mc.return_value.__aenter__.return_value = inst
            mc.return_value.__aexit__ = AsyncMock(return_value=False)
            inst.put.return_value = mock_resp
            ok = await adapter.set_light_state("light-uuid-1", on=True, brightness=100)
        assert ok is True

    @pytest.mark.asyncio
    async def test_set_light_state_no_payload(self):
        adapter, _ = self._make_adapter()
        ok = await adapter.set_light_state("light-uuid-1")
        assert ok is True  # no-op returns True

    def test_to_selena_devices_converts(self):
        from system_modules.import_adapters.hue_adapter import HueAdapter, HueBridge, HueLight
        bridge = HueBridge(bridge_id="x", ip="10.0.0.1", username="key")
        adapter = HueAdapter(bridge)
        lights = [HueLight(
            light_id="abc", name="Hall", on=True, brightness=99,
            color_xy=None, reachable=True, raw={}
        )]
        devs = adapter.to_selena_devices(lights)
        assert len(devs) == 1
        assert devs[0]["protocol"] == "hue_clip_v2"
        assert devs[0]["meta"]["source"] == "philips_hue"
        assert devs[0]["state"] == "on"

    @pytest.mark.asyncio
    async def test_discover_bridges_returns_list(self):
        from system_modules.import_adapters.hue_adapter import HueAdapter
        data = [{"id": "aabbccdd", "internalipaddress": "192.168.1.10"}]
        req = httpx.Request("GET", "https://discovery.meethue.com/")
        mock_resp = httpx.Response(200, json=data, request=req)
        with patch("httpx.AsyncClient") as mc:
            inst = AsyncMock()
            mc.return_value.__aenter__.return_value = inst
            mc.return_value.__aexit__ = AsyncMock(return_value=False)
            inst.get.return_value = mock_resp
            bridges = await HueAdapter.discover_bridges()
        assert len(bridges) == 1
        assert bridges[0]["id"] == "aabbccdd"

    @pytest.mark.asyncio
    async def test_discover_bridges_handles_error(self):
        from system_modules.import_adapters.hue_adapter import HueAdapter
        with patch("httpx.AsyncClient") as mc:
            inst = AsyncMock()
            mc.return_value.__aenter__.return_value = inst
            mc.return_value.__aexit__ = AsyncMock(return_value=False)
            inst.get.side_effect = httpx.ConnectError("unreachable")
            bridges = await HueAdapter.discover_bridges()
        assert bridges == []


# ── Tuya Adapter ──────────────────────────────────────────────────────────────

class TestTuyaAdapter:
    def test_to_selena_devices(self):
        from system_modules.import_adapters.tuya_adapter import TuyaAdapter, TuyaDevice
        adapter = TuyaAdapter()
        devs = adapter.to_selena_devices([
            TuyaDevice(device_id="d1", ip="10.0.0.5", local_key="key", name="Plug 1"),
        ])
        assert len(devs) == 1
        assert devs[0]["protocol"] == "tuya_local"
        assert devs[0]["meta"]["source"] == "tuya"
        assert devs[0]["meta"]["device_id"] == "d1"

    @pytest.mark.asyncio
    async def test_scan_no_tinytuya_returns_empty(self):
        from system_modules.import_adapters.tuya_adapter import TuyaAdapter
        adapter = TuyaAdapter()
        with patch.dict("sys.modules", {"tinytuya": None}):
            devs = await adapter.scan_network(timeout=0.1)
        # tinytuya not installed → empty list
        assert isinstance(devs, list)


# ── ImportManager ─────────────────────────────────────────────────────────────

class TestImportManager:
    def _make_manager(self):
        from system_modules.import_adapters.importer import ImportManager
        return ImportManager(publish_event_cb=AsyncMock(), core_api_url="http://core:7070", module_token="tok")

    def test_initial_status(self):
        mgr = self._make_manager()
        s = mgr.get_status()
        assert s["status"] == "idle"
        assert s["session_id"] is None
        assert s["imported_count"] == 0

    def test_initial_history_empty(self):
        mgr = self._make_manager()
        assert mgr.get_history() == []

    @pytest.mark.asyncio
    async def test_import_ha_dry_run(self):
        from system_modules.import_adapters.ha_adapter import HAEntity
        mgr = self._make_manager()

        entities = [
            HAEntity(entity_id="light.x", friendly_name="X", state="on", domain="light", attributes={}),
            HAEntity(entity_id="switch.y", friendly_name="Y", state="off", domain="switch", attributes={}),
        ]
        mock_adapter = AsyncMock()
        mock_adapter.get_entities = AsyncMock(return_value=entities)
        mock_adapter.to_selena_devices = MagicMock(return_value=[{}, {}])

        with patch("system_modules.import_adapters.importer.HomeAssistantAdapter", return_value=mock_adapter):
            session = await mgr.import_ha("http://ha.local:8123", "tok", dry_run=True)

        assert session.imported_count == 2
        assert session.status.value == "completed"
        assert session.source.value == "home_assistant"

    @pytest.mark.asyncio
    async def test_import_ha_publishes_events(self):
        from system_modules.import_adapters.ha_adapter import HAEntity
        publish = AsyncMock()
        from system_modules.import_adapters.importer import ImportManager
        mgr = ImportManager(publish_event_cb=publish)

        entities = [HAEntity(entity_id="sensor.t", friendly_name="Temp", state="22", domain="sensor", attributes={})]
        mock_adapter = AsyncMock()
        mock_adapter.get_entities = AsyncMock(return_value=entities)
        mock_adapter.to_selena_devices = MagicMock(return_value=[{}])

        with patch("system_modules.import_adapters.importer.HomeAssistantAdapter", return_value=mock_adapter):
            await mgr.import_ha("http://ha.local:8123", "tok", dry_run=True)

        event_types = [c[0][0] for c in publish.call_args_list]
        assert "import.started" in event_types
        assert "import.progress" in event_types
        assert "import.completed" in event_types

    @pytest.mark.asyncio
    async def test_import_ha_failure_publishes_failed_event(self):
        publish = AsyncMock()
        from system_modules.import_adapters.importer import ImportManager
        mgr = ImportManager(publish_event_cb=publish)

        mock_adapter = AsyncMock()
        mock_adapter.get_entities = AsyncMock(side_effect=Exception("unreachable"))
        mock_adapter.to_selena_devices = MagicMock(return_value=[])

        with patch("system_modules.import_adapters.importer.HomeAssistantAdapter", return_value=mock_adapter):
            with pytest.raises(Exception, match="unreachable"):
                await mgr.import_ha("http://ha.local:8123", "tok", dry_run=True)

        event_types = [c[0][0] for c in publish.call_args_list]
        assert "import.failed" in event_types

    @pytest.mark.asyncio
    async def test_import_tuya_dry_run(self):
        from system_modules.import_adapters.tuya_adapter import TuyaDevice
        mgr = self._make_manager()

        tuya_devs = [TuyaDevice(device_id="d1", ip="10.0.0.1", local_key="k", name="Plug")]
        mock_adapter = AsyncMock()
        mock_adapter.scan_network = AsyncMock(return_value=tuya_devs)
        mock_adapter.to_selena_devices = MagicMock(return_value=[{}])

        with patch("system_modules.import_adapters.importer.TuyaAdapter", return_value=mock_adapter):
            session = await mgr.import_tuya(scan_timeout=1.0, dry_run=True)

        assert session.imported_count == 1
        assert session.source.value == "tuya"
        assert session.status.value == "completed"

    @pytest.mark.asyncio
    async def test_import_hue_dry_run(self):
        from system_modules.import_adapters.hue_adapter import HueLight
        mgr = self._make_manager()

        lights = [HueLight(light_id="l1", name="Hall", on=True, brightness=80, color_xy=None, reachable=True, raw={})]
        mock_adapter = AsyncMock()
        mock_adapter.get_lights = AsyncMock(return_value=lights)
        mock_adapter.to_selena_devices = MagicMock(return_value=[{}])

        with patch("system_modules.import_adapters.importer.HueAdapter", return_value=mock_adapter):
            session = await mgr.import_hue("192.168.1.10", "app-key", dry_run=True)

        assert session.imported_count == 1
        assert session.source.value == "philips_hue"

    @pytest.mark.asyncio
    async def test_history_accumulates(self):
        from system_modules.import_adapters.ha_adapter import HAEntity
        mgr = self._make_manager()

        mock_adapter = AsyncMock()
        mock_adapter.get_entities = AsyncMock(return_value=[])
        mock_adapter.to_selena_devices = MagicMock(return_value=[])

        with patch("system_modules.import_adapters.importer.HomeAssistantAdapter", return_value=mock_adapter):
            await mgr.import_ha("http://ha:8123", "tok", dry_run=True)
            await mgr.import_ha("http://ha:8123", "tok", dry_run=True)

        hist = mgr.get_history()
        assert len(hist) == 2


# ── API ───────────────────────────────────────────────────────────────────────

class TestImportAPI:
    def _make_app(self):
        import system_modules.import_adapters.main as m
        from system_modules.import_adapters.importer import ImportManager
        mgr = ImportManager(publish_event_cb=AsyncMock())
        m._manager = mgr
        return m.app, mgr

    @pytest.mark.asyncio
    async def test_health(self):
        from httpx import AsyncClient, ASGITransport
        app, _ = self._make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    @pytest.mark.asyncio
    async def test_status(self):
        from httpx import AsyncClient, ASGITransport
        app, _ = self._make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/import/status")
        assert r.status_code == 200
        assert "status" in r.json()

    @pytest.mark.asyncio
    async def test_history(self):
        from httpx import AsyncClient, ASGITransport
        app, _ = self._make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/import/history")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    @pytest.mark.asyncio
    async def test_import_ha_endpoint(self):
        from httpx import AsyncClient, ASGITransport
        import system_modules.import_adapters.main as m
        from system_modules.import_adapters.importer import ImportManager, ImportSession, ImportSource, ImportStatus
        mgr = ImportManager(publish_event_cb=AsyncMock())
        mock_session = ImportSession(session_id="s1", source=ImportSource.HOME_ASSISTANT, status=ImportStatus.COMPLETED, imported_count=3)
        mgr.import_ha = AsyncMock(return_value=mock_session)
        m._manager = mgr
        async with AsyncClient(transport=ASGITransport(app=m.app), base_url="http://test") as c:
            r = await c.post("/import/ha", json={"base_url": "http://ha:8123", "token": "tok"})
        assert r.status_code == 200
        assert r.json()["imported_count"] == 3

    @pytest.mark.asyncio
    async def test_import_tuya_endpoint(self):
        from httpx import AsyncClient, ASGITransport
        import system_modules.import_adapters.main as m
        from system_modules.import_adapters.importer import ImportManager, ImportSession, ImportSource, ImportStatus
        mgr = ImportManager(publish_event_cb=AsyncMock())
        mock_session = ImportSession(session_id="s2", source=ImportSource.TUYA, status=ImportStatus.COMPLETED, imported_count=2)
        mgr.import_tuya = AsyncMock(return_value=mock_session)
        m._manager = mgr
        async with AsyncClient(transport=ASGITransport(app=m.app), base_url="http://test") as c:
            r = await c.post("/import/tuya", json={"scan_timeout": 2.0})
        assert r.status_code == 200
        assert r.json()["source"] == "tuya"

    @pytest.mark.asyncio
    async def test_import_hue_endpoint(self):
        from httpx import AsyncClient, ASGITransport
        import system_modules.import_adapters.main as m
        from system_modules.import_adapters.importer import ImportManager, ImportSession, ImportSource, ImportStatus
        mgr = ImportManager(publish_event_cb=AsyncMock())
        mock_session = ImportSession(session_id="s3", source=ImportSource.PHILIPS_HUE, status=ImportStatus.COMPLETED, imported_count=5)
        mgr.import_hue = AsyncMock(return_value=mock_session)
        m._manager = mgr
        async with AsyncClient(transport=ASGITransport(app=m.app), base_url="http://test") as c:
            r = await c.post("/import/hue", json={"bridge_ip": "192.168.1.10", "username": "key"})
        assert r.status_code == 200
        assert r.json()["imported_count"] == 5

    @pytest.mark.asyncio
    async def test_import_ha_endpoint_error_502(self):
        from httpx import AsyncClient, ASGITransport
        import system_modules.import_adapters.main as m
        from system_modules.import_adapters.importer import ImportManager
        mgr = ImportManager(publish_event_cb=AsyncMock())
        mgr.import_ha = AsyncMock(side_effect=Exception("Connection refused"))
        m._manager = mgr
        async with AsyncClient(transport=ASGITransport(app=m.app), base_url="http://test") as c:
            r = await c.post("/import/ha", json={"base_url": "http://ha:8123", "token": "tok"})
        assert r.status_code == 502

    @pytest.mark.asyncio
    async def test_widget_served(self):
        from httpx import AsyncClient, ASGITransport
        app, _ = self._make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/widget.html")
        assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_settings_served(self):
        from httpx import AsyncClient, ASGITransport
        app, _ = self._make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/settings.html")
        assert r.status_code == 200
