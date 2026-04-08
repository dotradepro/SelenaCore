"""
system_modules/protocol_bridge/bridge.py — Protocol Bridge business logic

Gateway between physical smart home protocols and Device Registry:
  - MQTT: auto-discovery (Home Assistant standard) + state/command
  - Zigbee: via zigbee2mqtt (MQTT)
  - Z-Wave: via zwave-js-ui (optional)
  - HTTP/REST: polling WiFi devices (Shelly, Sonoff DIY, etc.)

Other modules work only with abstract Device Registry devices
and are unaware of the underlying protocols.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Callable

import httpx

logger = logging.getLogger(__name__)

try:
    import aiomqtt
    AIOMQTT_AVAILABLE = True
except ImportError:
    AIOMQTT_AVAILABLE = False
    logger.warning("aiomqtt not installed — MQTT bridge disabled")


# ── MQTT Domain → SelenaCore device type mapping ───────────────────────────────

HA_DOMAIN_TO_TYPE: dict[str, str] = {
    "light":          "light",
    "switch":         "switch",
    "sensor":         "sensor",
    "binary_sensor":  "binary_sensor",
    "climate":        "climate",
    "cover":          "cover",
    "fan":            "fan",
    "lock":           "lock",
    "media_player":   "media_player",
    "camera":         "camera",
}


def _extract_capabilities(config: dict) -> list[str]:
    """Extract device capabilities from HA MQTT discovery config."""
    caps: list[str] = []
    if config.get("command_topic"):
        caps.append("write")
    if config.get("state_topic"):
        caps.append("read")
    if "brightness_command_topic" in config or "brightness_state_topic" in config:
        caps.append("brightness")
    if "color_temp_command_topic" in config:
        caps.append("color_temp")
    if "rgb_command_topic" in config:
        caps.append("rgb")
    if "temperature_command_topic" in config:
        caps.append("set_temperature")
    return caps


class MQTTBridge:
    """Handles MQTT protocol — discovery, state sync, commands."""

    def __init__(
        self,
        host: str,
        port: int,
        username: str | None,
        password: str | None,
        register_device_cb: Callable,
        update_device_state_cb: Callable,
        get_device_by_mqtt_topic_cb: Callable,
        publish_event_cb: Callable,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self._register_device = register_device_cb
        self._update_device_state = update_device_state_cb
        self._get_device_by_mqtt_topic = get_device_by_mqtt_topic_cb
        self._publish_event = publish_event_cb
        self._client: Any = None
        self._task: asyncio.Task | None = None
        self._connected = False
        # Track unique_id → device_id mapping
        self._unique_id_map: dict[str, str] = {}

    @property
    def connected(self) -> bool:
        return self._connected

    async def start(self) -> None:
        if not AIOMQTT_AVAILABLE:
            logger.error("aiomqtt not available — MQTT bridge disabled")
            return
        self._task = asyncio.create_task(self._run(), name="mqtt_bridge")
        logger.info(f"MQTTBridge starting: {self.host}:{self.port}")

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._connected = False

    async def publish(self, topic: str, payload: str) -> None:
        """Publish an MQTT message. Used for device commands."""
        if self._client is None or not self._connected:
            logger.warning(f"MQTT not connected, cannot publish to {topic}")
            return
        try:
            await self._client.publish(topic, payload.encode())
        except Exception as exc:
            logger.error(f"MQTT publish failed: {exc}")

    async def _run(self) -> None:
        reconnect_delay = 5
        while True:
            try:
                connect_kwargs: dict = {
                    "hostname": self.host,
                    "port": self.port,
                }
                if self.username:
                    connect_kwargs["username"] = self.username
                if self.password:
                    connect_kwargs["password"] = self.password

                async with aiomqtt.Client(**connect_kwargs) as client:
                    self._client = client
                    self._connected = True
                    reconnect_delay = 5
                    logger.info(f"MQTT connected: {self.host}:{self.port}")
                    await self._publish_event("protocol_bridge.mqtt_connected", {
                        "host": self.host, "port": self.port
                    })

                    # Subscribe: HA discovery + Zigbee state topics
                    await client.subscribe("homeassistant/+/+/config")
                    await client.subscribe("homeassistant/+/+/+/config")
                    await client.subscribe("zigbee2mqtt/bridge/devices")
                    await client.subscribe("zigbee2mqtt/+")
                    # Wildcard for all device state updates
                    await client.subscribe("#")

                    async for message in client.messages:
                        await self._handle_message(
                            str(message.topic),
                            message.payload,
                        )

            except asyncio.CancelledError:
                break
            except Exception as exc:
                self._connected = False
                logger.error(f"MQTT error: {exc} — reconnecting in {reconnect_delay}s")
                await self._publish_event("protocol_bridge.mqtt_disconnected", {
                    "reason": str(exc)
                })
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, 60)

    async def _handle_message(self, topic: str, payload: bytes) -> None:
        try:
            text = payload.decode("utf-8", errors="replace")
        except Exception:
            return

        # HA MQTT Discovery
        if re.match(r"^homeassistant/\w+/[^/]+/config$", topic) or \
           re.match(r"^homeassistant/\w+/[^/]+/[^/]+/config$", topic):
            await self._handle_discovery(topic, text)
            return

        # Zigbee2MQTT bridge/devices — initial device list
        if topic == "zigbee2mqtt/bridge/devices":
            await self._handle_zigbee_devices(text)
            return

        # Zigbee2MQTT device state update (not bridge topics)
        if topic.startswith("zigbee2mqtt/") and "/set" not in topic \
                and "bridge" not in topic:
            friendly_name = topic[len("zigbee2mqtt/"):]
            await self._handle_zigbee_state(friendly_name, text)
            return

        # Generic MQTT state topic — look up registered device
        device_id = await self._get_device_by_mqtt_topic(topic)
        if device_id:
            try:
                state = json.loads(text)
                now = datetime.now(tz=timezone.utc).isoformat()
                await self._update_device_state(device_id, {
                    **state,
                    "mqtt_last_seen": now,
                })
                await self._publish_event("device.protocol_heartbeat", {
                    "device_id": device_id,
                    "protocol": "mqtt",
                    "timestamp": now,
                })
            except (json.JSONDecodeError, Exception) as exc:
                logger.debug(f"Could not parse MQTT state from {topic}: {exc}")

    async def _handle_discovery(self, topic: str, payload_text: str) -> None:
        try:
            config = json.loads(payload_text)
        except json.JSONDecodeError:
            return

        parts = topic.split("/")
        domain = parts[1] if len(parts) >= 2 else "sensor"
        device_type = HA_DOMAIN_TO_TYPE.get(domain, "sensor")
        unique_id = config.get("unique_id", "")

        if not unique_id:
            return

        if unique_id in self._unique_id_map:
            return  # already registered

        name = config.get("name") or config.get("friendly_name") or unique_id
        capabilities = _extract_capabilities(config)

        device_id = await self._register_device(
            name=name,
            device_type=device_type,
            protocol="mqtt",
            capabilities=capabilities,
            meta={
                "mqtt_state_topic":   config.get("state_topic"),
                "mqtt_command_topic": config.get("command_topic"),
                "mqtt_unique_id":     unique_id,
                "ha_discovery":       True,
            },
        )
        if device_id:
            self._unique_id_map[unique_id] = device_id
            logger.info(f"MQTT discovery: registered {name} as {device_id}")
            await self._publish_event("device.protocol_discovered", {
                "name": name, "protocol": "mqtt", "meta": {"unique_id": unique_id}
            })

    async def _handle_zigbee_devices(self, payload_text: str) -> None:
        try:
            devices = json.loads(payload_text)
        except json.JSONDecodeError:
            return

        count = 0
        for device in devices:
            if device.get("type") == "Coordinator":
                continue
            friendly_name = device.get("friendly_name", "")
            ieee = device.get("ieee_address", "")
            if not friendly_name:
                continue
            key = f"zigbee:{ieee}"
            if key in self._unique_id_map:
                continue
            device_id = await self._register_device(
                name=friendly_name,
                device_type="sensor",
                protocol="zigbee",
                capabilities=[],
                meta={
                    "zigbee_ieee": ieee,
                    "zigbee_name": friendly_name,
                    "zigbee2mqtt": True,
                },
            )
            if device_id:
                self._unique_id_map[key] = device_id
                count += 1

        if count:
            await self._publish_event("protocol_bridge.zigbee_devices", {"count": count})
            logger.info(f"Registered {count} Zigbee devices")

    async def _handle_zigbee_state(self, friendly_name: str, payload_text: str) -> None:
        try:
            state = json.loads(payload_text)
        except json.JSONDecodeError:
            return

        # Find device by friendly_name
        device_id = await self._get_device_by_mqtt_topic(
            f"zigbee2mqtt/{friendly_name}"
        )
        if not device_id:
            return

        now = datetime.now(tz=timezone.utc).isoformat()
        await self._update_device_state(device_id, {
            **state,
            "protocol_last_seen": now,
        })
        await self._publish_event("device.protocol_heartbeat", {
            "device_id": device_id,
            "protocol": "zigbee",
            "timestamp": now,
        })

    async def send_command(self, device: dict, new_state: dict) -> None:
        """Send command to MQTT device."""
        meta = device.get("meta", {})
        protocol = device.get("protocol", "")

        if protocol == "mqtt":
            command_topic = meta.get("mqtt_command_topic")
            if command_topic:
                await self.publish(command_topic, json.dumps(new_state))
        elif protocol == "zigbee":
            friendly_name = meta.get("zigbee_name")
            if friendly_name:
                await self.publish(f"zigbee2mqtt/{friendly_name}/set",
                                   json.dumps(new_state))


class HTTPPoller:
    """Polls REST API devices (Shelly, Sonoff DIY, etc.)."""

    def __init__(
        self,
        poll_interval_sec: int,
        update_device_state_cb: Callable,
        get_http_devices_cb: Callable,
        publish_event_cb: Callable,
    ) -> None:
        self._interval = poll_interval_sec
        self._update_device_state = update_device_state_cb
        self._get_http_devices = get_http_devices_cb
        self._publish_event = publish_event_cb
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._poll_loop(), name="http_poller")

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _poll_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self._interval)
                await self._poll_all()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"HTTP poller error: {exc}")

    async def _poll_all(self) -> None:
        devices = await self._get_http_devices()
        tasks = [self._poll_device(d) for d in devices]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _poll_device(self, device: dict) -> None:
        device_id = device.get("device_id") or device.get("id", "")
        meta = device.get("meta", {})
        poll_url = meta.get("poll_url")
        if not poll_url:
            return
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(poll_url)
                resp.raise_for_status()
                state = resp.json()
                now = datetime.now(tz=timezone.utc).isoformat()
                await self._update_device_state(device_id, {
                    **state,
                    "http_last_seen": now,
                })
                await self._publish_event("device.protocol_heartbeat", {
                    "device_id": device_id,
                    "protocol": "http",
                    "timestamp": now,
                })
        except Exception as exc:
            logger.warning(f"HTTP poll failed for {device_id}: {exc}")


class ProtocolBridge:
    """Top-level orchestrator for all protocol bridges."""

    def __init__(
        self,
        config: dict,
        register_device_cb: Callable,
        update_device_state_cb: Callable,
        get_devices_cb: Callable,
        publish_event_cb: Callable,
    ) -> None:
        self._config = config
        self._register_device = register_device_cb
        self._update_device_state = update_device_state_cb
        self._get_devices = get_devices_cb
        self._publish_event = publish_event_cb
        self._mqtt: MQTTBridge | None = None
        self._poller: HTTPPoller | None = None

    async def start(self) -> None:
        if self._config.get("mqtt_enabled", True) and AIOMQTT_AVAILABLE:
            self._mqtt = MQTTBridge(
                host=self._config.get("mqtt_host", "localhost"),
                port=int(self._config.get("mqtt_port", 1883)),
                username=self._config.get("mqtt_username"),
                password=self._config.get("mqtt_password"),
                register_device_cb=self._register_device,
                update_device_state_cb=self._update_device_state,
                get_device_by_mqtt_topic_cb=self._get_device_by_mqtt_topic,
                publish_event_cb=self._publish_event,
            )
            await self._mqtt.start()

        poll_interval = int(self._config.get("http_poll_interval_sec", 30))
        self._poller = HTTPPoller(
            poll_interval_sec=poll_interval,
            update_device_state_cb=self._update_device_state,
            get_http_devices_cb=self._get_http_devices,
            publish_event_cb=self._publish_event,
        )
        await self._poller.start()
        logger.info("ProtocolBridge started")

    async def stop(self) -> None:
        if self._mqtt:
            await self._mqtt.stop()
        if self._poller:
            await self._poller.stop()
        logger.info("ProtocolBridge stopped")

    async def on_state_changed(self, payload: dict) -> None:
        """Called when device.state_changed event received — send command to device."""
        if self._mqtt is None:
            return
        device_id = payload.get("device_id")
        if not device_id:
            return
        try:
            devices = await self._get_devices()
            device = next((d for d in devices
                           if d.get("device_id") == device_id or d.get("id") == device_id),
                          None)
            if not device:
                return
            if device.get("protocol") not in ("mqtt", "zigbee"):
                return
            new_state = payload.get("new_state", {})
            await self._mqtt.send_command(device, new_state)
        except Exception as exc:
            logger.error(f"Failed to forward state to device {device_id}: {exc}")

    async def handle_command(self, payload: dict) -> None:
        """Forward a logical ``device.command`` payload to MQTT.

        Published by drivers (e.g. ``MqttBridgeDriver``) that delegate the
        actual transport to this module. The payload carries the resolved
        ``command_topic`` so we don't need a Device Registry lookup.
        """
        if self._mqtt is None or not self._mqtt.connected:
            logger.warning(
                "protocol_bridge: device.command ignored, MQTT not connected "
                "(device=%s topic=%s)",
                payload.get("device_id"), payload.get("command_topic"),
            )
            return
        topic = payload.get("command_topic")
        state = payload.get("state")
        if not topic or state is None:
            logger.warning(
                "protocol_bridge: device.command missing topic/state: %s", payload,
            )
            return
        try:
            await self._mqtt.publish(topic, json.dumps(state))
        except Exception as exc:
            logger.error(
                "protocol_bridge: failed to publish device.command to %s: %s",
                topic, exc,
            )

    async def _get_device_by_mqtt_topic(self, topic: str) -> str | None:
        """Find device_id by MQTT state_topic or zigbee friendly_name topic."""
        try:
            devices = await self._get_devices()
            for device in devices:
                meta = device.get("meta", {})
                if meta.get("mqtt_state_topic") == topic:
                    return device.get("device_id") or device.get("id")
                fname = meta.get("zigbee_name")
                if fname and f"zigbee2mqtt/{fname}" == topic:
                    return device.get("device_id") or device.get("id")
        except Exception as exc:
            logger.error(f"get_device_by_mqtt_topic failed: {exc}")
        return None

    async def _get_http_devices(self) -> list[dict]:
        """Return only HTTP/REST protocol devices."""
        try:
            all_devices = await self._get_devices()
            return [d for d in all_devices if d.get("protocol") in ("http", "wifi")]
        except Exception as exc:
            logger.error(f"Failed to get HTTP devices: {exc}")
            return []

    def get_status(self) -> dict:
        return {
            "mqtt": {
                "enabled": self._mqtt is not None,
                "connected": self._mqtt.connected if self._mqtt else False,
                "host": self._config.get("mqtt_host", "localhost"),
            },
            "zigbee": {
                "enabled": self._config.get("zigbee_enabled", False),
            },
            "zwave": {
                "enabled": self._config.get("zwave_enabled", False),
            },
        }
