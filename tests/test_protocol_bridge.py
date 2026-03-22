"""tests/test_protocol_bridge.py — pytest tests for protocol_bridge module"""
from __future__ import annotations

import json
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch, call
from typing import Any

import pytest
import pytest_asyncio


# ── Helpers / stubs ─────────────────────────────────────────────────────────

def make_bridge(
    register_cb=None,
    update_cb=None,
    get_topic_cb=None,
    publish_cb=None,
):
    """Return a MQTTBridge with mocked callbacks (AIOMQTT not needed)."""
    from system_modules.protocol_bridge.bridge import MQTTBridge
    return MQTTBridge(
        host="localhost",
        port=1883,
        username=None,
        password=None,
        register_device_cb=register_cb or AsyncMock(return_value="dev-id-001"),
        update_device_state_cb=update_cb or AsyncMock(),
        get_device_by_mqtt_topic_cb=get_topic_cb or AsyncMock(return_value=None),
        publish_event_cb=publish_cb or AsyncMock(),
    )


def make_protocol_bridge(
    config=None,
    register_cb=None,
    update_cb=None,
    get_devices_cb=None,
    publish_cb=None,
):
    from system_modules.protocol_bridge.bridge import ProtocolBridge
    return ProtocolBridge(
        config=config or {
            "mqtt_enabled": False,
            "http_poll_interval_sec": 30,
        },
        register_device_cb=register_cb or AsyncMock(return_value="dev-id"),
        update_device_state_cb=update_cb or AsyncMock(),
        get_devices_cb=get_devices_cb or AsyncMock(return_value=[]),
        publish_event_cb=publish_cb or AsyncMock(),
    )


# ── _extract_capabilities ────────────────────────────────────────────────────

class TestExtractCapabilities:
    def test_read_write_caps(self):
        from system_modules.protocol_bridge.bridge import _extract_capabilities
        config = {
            "state_topic": "ha/sensor/state",
            "command_topic": "ha/sensor/set",
        }
        caps = _extract_capabilities(config)
        assert "read" in caps
        assert "write" in caps

    def test_read_only(self):
        from system_modules.protocol_bridge.bridge import _extract_capabilities
        config = {"state_topic": "ha/sensor/state"}
        caps = _extract_capabilities(config)
        assert "read" in caps
        assert "write" not in caps

    def test_light_capabilities(self):
        from system_modules.protocol_bridge.bridge import _extract_capabilities
        config = {
            "state_topic": "ha/light/state",
            "command_topic": "ha/light/set",
            "brightness_state_topic": "ha/light/brightness",
            "rgb_command_topic": "ha/light/rgb/set",
        }
        caps = _extract_capabilities(config)
        assert "brightness" in caps
        assert "rgb" in caps

    def test_empty_config(self):
        from system_modules.protocol_bridge.bridge import _extract_capabilities
        assert _extract_capabilities({}) == []


# ── MQTTBridge — _handle_discovery ──────────────────────────────────────────

class TestMQTTBridgeDiscovery:
    @pytest.mark.asyncio
    async def test_ha_discovery_registers_device(self):
        register_cb = AsyncMock(return_value="dev-001")
        publish_cb = AsyncMock()
        bridge = make_bridge(register_cb=register_cb, publish_cb=publish_cb)

        config = {
            "name": "Living Room Light",
            "unique_id": "light_lr_001",
            "state_topic": "ha/light/lr/state",
            "command_topic": "ha/light/lr/set",
        }
        await bridge._handle_discovery(
            "homeassistant/light/living_room/config",
            json.dumps(config),
        )

        register_cb.assert_called_once()
        call_kwargs = register_cb.call_args[1] if register_cb.call_args[1] else {}
        call_args = register_cb.call_args[0] if register_cb.call_args[0] else ()
        # Extract all actual args
        actual = {**call_kwargs}
        if call_args:
            # positional fallback
            keys = ["name", "device_type", "protocol", "capabilities", "meta"]
            actual = dict(zip(keys, call_args))
        assert actual.get("name") == "Living Room Light"
        assert actual.get("protocol") == "mqtt"

    @pytest.mark.asyncio
    async def test_ha_discovery_not_duplicate(self):
        register_cb = AsyncMock(return_value="dev-001")
        bridge = make_bridge(register_cb=register_cb)

        config = {"name": "Sensor A", "unique_id": "sensor_a_001",
                  "state_topic": "ha/sensor/a/state"}
        topic = "homeassistant/sensor/a/config"

        await bridge._handle_discovery(topic, json.dumps(config))
        await bridge._handle_discovery(topic, json.dumps(config))  # duplicate

        assert register_cb.call_count == 1  # only registered once

    @pytest.mark.asyncio
    async def test_ha_discovery_publishes_event(self):
        publish_cb = AsyncMock()
        bridge = make_bridge(publish_cb=publish_cb)

        config = {"name": "Switch X", "unique_id": "switch_x",
                  "command_topic": "ha/switch/x/set"}
        await bridge._handle_discovery("homeassistant/switch/x/config", json.dumps(config))

        publish_cb.assert_called()
        call_type = publish_cb.call_args[0][0]
        assert call_type == "device.protocol_discovered"

    @pytest.mark.asyncio
    async def test_discovery_missing_unique_id_skipped(self):
        register_cb = AsyncMock()
        bridge = make_bridge(register_cb=register_cb)

        config = {"name": "Anonymous device", "state_topic": "ha/x/state"}
        await bridge._handle_discovery("homeassistant/sensor/x/config", json.dumps(config))

        register_cb.assert_not_called()

    @pytest.mark.asyncio
    async def test_discovery_invalid_json_skipped(self):
        register_cb = AsyncMock()
        bridge = make_bridge(register_cb=register_cb)

        await bridge._handle_discovery("homeassistant/sensor/x/config", "NOT JSON{{")

        register_cb.assert_not_called()


# ── MQTTBridge — _handle_zigbee_devices ─────────────────────────────────────

class TestMQTTBridgeZigbee:
    @pytest.mark.asyncio
    async def test_zigbee_devices_registered(self):
        register_cb = AsyncMock(return_value="zb-id-001")
        publish_cb = AsyncMock()
        bridge = make_bridge(register_cb=register_cb, publish_cb=publish_cb)

        devices = [
            {"friendly_name": "motion_sensor_hall", "ieee_address": "0x0011",
             "type": "EndDevice"},
            {"friendly_name": "temp_sensor_kit", "ieee_address": "0x0022",
             "type": "EndDevice"},
            {"friendly_name": "Coordinator", "ieee_address": "0x0000",
             "type": "Coordinator"},
        ]
        await bridge._handle_zigbee_devices(json.dumps(devices))

        # Coordinator must be skipped
        assert register_cb.call_count == 2

    @pytest.mark.asyncio
    async def test_zigbee_devices_not_duplicate(self):
        register_cb = AsyncMock(return_value="zb-id")
        bridge = make_bridge(register_cb=register_cb)

        devices = [{"friendly_name": "sensor_a", "ieee_address": "0x0001",
                    "type": "EndDevice"}]
        payload = json.dumps(devices)

        await bridge._handle_zigbee_devices(payload)
        await bridge._handle_zigbee_devices(payload)  # second call

        assert register_cb.call_count == 1

    @pytest.mark.asyncio
    async def test_zigbee_state_updates_device(self):
        update_cb = AsyncMock()
        device_id = "dev-zigbee-001"
        get_topic_cb = AsyncMock(return_value=device_id)
        publish_cb = AsyncMock()
        bridge = make_bridge(
            update_cb=update_cb,
            get_topic_cb=get_topic_cb,
            publish_cb=publish_cb,
        )

        state = {"temperature": 22.5, "humidity": 60}
        await bridge._handle_zigbee_state("motion_sensor", json.dumps(state))

        update_cb.assert_called_once()
        args = update_cb.call_args[0]
        assert args[0] == device_id
        assert args[1]["temperature"] == 22.5
        assert "protocol_last_seen" in args[1]

        # Should publish heartbeat event
        publish_cb.assert_called()
        event_type = publish_cb.call_args[0][0]
        assert event_type == "device.protocol_heartbeat"


# ── MQTTBridge — send_command ────────────────────────────────────────────────

class TestMQTTBridgeSendCommand:
    @pytest.mark.asyncio
    async def test_send_command_mqtt_device(self):
        bridge = make_bridge()
        bridge._connected = True
        bridge._client = AsyncMock()

        device = {
            "protocol": "mqtt",
            "meta": {"mqtt_command_topic": "ha/light/lr/set"},
        }
        await bridge.send_command(device, {"state": "ON"})

        bridge._client.publish.assert_called_once_with(
            "ha/light/lr/set",
            json.dumps({"state": "ON"}).encode(),
        )

    @pytest.mark.asyncio
    async def test_send_command_zigbee_device(self):
        bridge = make_bridge()
        bridge._connected = True
        bridge._client = AsyncMock()

        device = {
            "protocol": "zigbee",
            "meta": {"zigbee_name": "light_kitchen"},
        }
        await bridge.send_command(device, {"state": "OFF"})

        bridge._client.publish.assert_called_once_with(
            "zigbee2mqtt/light_kitchen/set",
            json.dumps({"state": "OFF"}).encode(),
        )

    @pytest.mark.asyncio
    async def test_send_command_not_connected(self):
        """Should silently warn when not connected, not raise."""
        bridge = make_bridge()
        bridge._connected = False
        bridge._client = None

        # Should not raise
        device = {"protocol": "mqtt", "meta": {"mqtt_command_topic": "some/topic"}}
        await bridge.send_command(device, {"state": "ON"})


# ── MQTTBridge — _handle_message routing ────────────────────────────────────

class TestMQTTBridgeMessageRouting:
    @pytest.mark.asyncio
    async def test_ha_discovery_topic_routed(self):
        bridge = make_bridge()
        bridge._handle_discovery = AsyncMock()

        payload = json.dumps({"name": "X", "unique_id": "x001",
                               "state_topic": "ha/x/state"}).encode()
        await bridge._handle_message("homeassistant/sensor/x/config", payload)

        bridge._handle_discovery.assert_called_once()

    @pytest.mark.asyncio
    async def test_zigbee_bridge_devices_topic_routed(self):
        bridge = make_bridge()
        bridge._handle_zigbee_devices = AsyncMock()

        await bridge._handle_message(
            "zigbee2mqtt/bridge/devices",
            b"[]",
        )
        bridge._handle_zigbee_devices.assert_called_once()

    @pytest.mark.asyncio
    async def test_zigbee_state_topic_routed(self):
        bridge = make_bridge()
        bridge._handle_zigbee_state = AsyncMock()

        await bridge._handle_message(
            "zigbee2mqtt/motion_hall",
            json.dumps({"occupancy": True}).encode(),
        )
        bridge._handle_zigbee_state.assert_called_once_with(
            "motion_hall", '{"occupancy": true}'
        )


# ── HTTPPoller ───────────────────────────────────────────────────────────────

class TestHTTPPoller:
    @pytest.mark.asyncio
    async def test_poll_device_updates_state(self):
        update_cb = AsyncMock()
        publish_cb = AsyncMock()
        device_data = {"temperature": 21.0, "power": 45}

        async def mock_get_devices():
            return [{"device_id": "http-dev-001",
                     "meta": {"poll_url": "http://192.168.1.50/status"}}]

        from system_modules.protocol_bridge.bridge import HTTPPoller

        poller = HTTPPoller(
            poll_interval_sec=30,
            update_device_state_cb=update_cb,
            get_http_devices_cb=mock_get_devices,
            publish_event_cb=publish_cb,
        )

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_response = MagicMock()
            mock_response.raise_for_status = MagicMock()
            mock_response.json = MagicMock(return_value=device_data)

            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            await poller._poll_all()

        update_cb.assert_called_once()
        call_args = update_cb.call_args[0]
        assert call_args[0] == "http-dev-001"
        assert call_args[1]["temperature"] == 21.0
        assert "http_last_seen" in call_args[1]

    @pytest.mark.asyncio
    async def test_poll_device_no_poll_url_skipped(self):
        update_cb = AsyncMock()

        async def mock_get_devices():
            return [{"device_id": "no-url-dev", "meta": {}}]

        from system_modules.protocol_bridge.bridge import HTTPPoller

        poller = HTTPPoller(
            poll_interval_sec=30,
            update_device_state_cb=update_cb,
            get_http_devices_cb=mock_get_devices,
            publish_event_cb=AsyncMock(),
        )
        await poller._poll_all()
        update_cb.assert_not_called()

    @pytest.mark.asyncio
    async def test_poll_device_http_error_no_crash(self):
        update_cb = AsyncMock()

        async def mock_get_devices():
            return [{"device_id": "bad-dev",
                     "meta": {"poll_url": "http://bad.host/status"}}]

        from system_modules.protocol_bridge.bridge import HTTPPoller

        poller = HTTPPoller(
            poll_interval_sec=30,
            update_device_state_cb=update_cb,
            get_http_devices_cb=mock_get_devices,
            publish_event_cb=AsyncMock(),
        )

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get = AsyncMock(side_effect=Exception("connection refused"))
            mock_client_cls.return_value = mock_client

            # Should not raise
            await poller._poll_all()

        update_cb.assert_not_called()


# ── ProtocolBridge — on_state_changed ───────────────────────────────────────

class TestProtocolBridgeOnStateChanged:
    @pytest.mark.asyncio
    async def test_state_changed_routes_to_mqtt(self):
        """device.state_changed → MQTTBridge.send_command if device is MQTT."""
        update_cb = AsyncMock()

        async def get_devices():
            return [{
                "device_id": "dev-mqtt-001",
                "protocol": "mqtt",
                "meta": {"mqtt_command_topic": "ha/switch/1/set"},
            }]

        bridge = make_protocol_bridge(
            config={"mqtt_enabled": False, "http_poll_interval_sec": 30},
            get_devices_cb=get_devices,
            update_cb=update_cb,
        )
        # Manually attach a mock mqtt bridge
        mock_mqtt = MagicMock()
        mock_mqtt.send_command = AsyncMock()
        bridge._mqtt = mock_mqtt

        payload = {"device_id": "dev-mqtt-001", "new_state": {"state": "ON"}}
        await bridge.on_state_changed(payload)

        mock_mqtt.send_command.assert_called_once()

    @pytest.mark.asyncio
    async def test_state_changed_no_mqtt_bridge_no_crash(self):
        """If MQTT bridge not initialized, no crash."""
        bridge = make_protocol_bridge(
            config={"mqtt_enabled": False, "http_poll_interval_sec": 30},
        )
        bridge._mqtt = None

        payload = {"device_id": "dev-001", "new_state": {"state": "OFF"}}
        # Should not raise
        await bridge.on_state_changed(payload)

    @pytest.mark.asyncio
    async def test_state_changed_unknown_device_no_command(self):
        """Device not in registry → no MQTT command sent."""
        async def get_devices():
            return []

        bridge = make_protocol_bridge(
            config={"mqtt_enabled": False, "http_poll_interval_sec": 30},
            get_devices_cb=get_devices,
        )
        mock_mqtt = MagicMock()
        mock_mqtt.send_command = AsyncMock()
        bridge._mqtt = mock_mqtt

        payload = {"device_id": "unknown-dev", "new_state": {"state": "ON"}}
        await bridge.on_state_changed(payload)

        mock_mqtt.send_command.assert_not_called()


# ── ProtocolBridge — get_status ──────────────────────────────────────────────

class TestProtocolBridgeGetStatus:
    @pytest.mark.asyncio
    async def test_get_status_no_mqtt(self):
        bridge = make_protocol_bridge()
        bridge._mqtt = None
        status = bridge.get_status()
        assert "mqtt" in status
        assert status["mqtt"]["connected"] is False

    @pytest.mark.asyncio
    async def test_get_status_with_connected_mqtt(self):
        bridge = make_protocol_bridge()
        mock_mqtt = MagicMock()
        mock_mqtt.connected = True
        mock_mqtt.host = "localhost"
        mock_mqtt.port = 1883
        bridge._mqtt = mock_mqtt

        status = bridge.get_status()
        assert status["mqtt"]["connected"] is True


# ── FastAPI endpoints (integration-style) ───────────────────────────────────

class TestProtocolBridgeAPI:
    @pytest.mark.asyncio
    async def test_health_endpoint(self):
        from httpx import AsyncClient, ASGITransport
        from system_modules.protocol_bridge.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/health")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["module"] == "protocol-bridge"

    @pytest.mark.asyncio
    async def test_config_get_no_password(self):
        from httpx import AsyncClient, ASGITransport
        from system_modules.protocol_bridge.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/config")

        assert resp.status_code == 200
        data = resp.json()
        assert "mqtt_password" not in data

    @pytest.mark.asyncio
    async def test_webhook_events_state_changed(self):
        """POST /webhook/events with device.state_changed calls on_state_changed."""
        from httpx import AsyncClient, ASGITransport
        import system_modules.protocol_bridge.main as pb_main
        from system_modules.protocol_bridge.bridge import ProtocolBridge

        mock_bridge = MagicMock(spec=ProtocolBridge)
        mock_bridge.on_state_changed = AsyncMock()
        mock_bridge.get_status = MagicMock(return_value={
            "mqtt": {"connected": False}, "bridged_devices": 0,
        })
        pb_main._bridge = mock_bridge

        async with AsyncClient(
            transport=ASGITransport(app=pb_main.app), base_url="http://test"
        ) as client:
            resp = await client.post("/webhook/events", json={
                "type": "device.state_changed",
                "payload": {"device_id": "dev-001", "new_state": {"state": "ON"}},
            })

        assert resp.status_code == 200
        mock_bridge.on_state_changed.assert_called_once()

    @pytest.mark.asyncio
    async def test_widget_html_served(self):
        from httpx import AsyncClient, ASGITransport
        from system_modules.protocol_bridge.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/widget")

        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    @pytest.mark.asyncio
    async def test_settings_html_served(self):
        from httpx import AsyncClient, ASGITransport
        from system_modules.protocol_bridge.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/settings")

        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
