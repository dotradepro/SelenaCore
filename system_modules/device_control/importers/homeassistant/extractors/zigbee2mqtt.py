"""Zigbee2MQTT (via MQTT) → selena zigbee2mqtt driver.

Z2M appears in HA as plain MQTT-discovered devices, but we want to route
them to the dedicated ``zigbee2mqtt`` provider instead of the generic
MQTT bridge so friendly-name lookups keep working. The broker-hosting
check is shared with the mqtt extractor — if the broker dies with HA, no
amount of Z2M migration helps.
"""
from __future__ import annotations

from typing import Any

from ..types import ExtractionResult, HADevice
from . import register
from .mqtt import _broker_from_context, _is_ha_hosted_broker


def _friendly_name(device: HADevice) -> str | None:
    """Z2M prefixes friendly names with the base topic in identifiers
    (e.g. [["mqtt", "zigbee2mqtt_0xabc123"]]) and also leaves the bare
    name in ``name``. Prefer the bare name — it's what Z2M's REST API
    uses as the device id."""
    name = (device.name or "").strip()
    return name or None


def extract(device: HADevice, context: dict[str, Any] | None = None) -> ExtractionResult:
    broker = _broker_from_context(context)
    if broker is None:
        return ExtractionResult(
            status="needs_user_input",
            needs=["mqtt_broker"],
            reason=(
                "Zigbee2MQTT routes state through an MQTT broker. Supply the "
                "broker config (the Selena driver will subscribe to "
                "``<base_topic>/<friendly_name>``)."
            ),
        )

    host = str(broker.get("broker_host") or "").strip()
    if _is_ha_hosted_broker(host) and not (context or {}).get("mqtt_override"):
        return ExtractionResult(
            status="needs_user_input",
            needs=["mqtt_broker_override"],
            reason=(
                f"Zigbee2MQTT is routed through an HA-hosted broker ({host}). "
                "Move the broker (and, typically, Z2M itself) to a host that "
                "survives HA shutdown before importing."
            ),
        )

    friendly = _friendly_name(device)
    if not friendly:
        return ExtractionResult(
            status="unsupported",
            reason="Z2M device has no friendly name — cannot address it via MQTT.",
        )

    base_topic = str(broker.get("base_topic") or "zigbee2mqtt")
    creds: dict[str, Any] = {
        "broker_host": host,
        "broker_port": int(broker.get("broker_port") or 1883),
        "base_topic": base_topic,
        "friendly_name": friendly,
    }
    for k in ("username", "password", "client_id"):
        if broker.get(k):
            creds[k] = str(broker[k])

    return ExtractionResult(
        status="ok",
        protocol="zigbee2mqtt",
        entity_type="light",   # majority; user can PATCH if wrong
        credentials=creds,
        capabilities=["on", "off"],
    )


register("zigbee2mqtt", extract)
