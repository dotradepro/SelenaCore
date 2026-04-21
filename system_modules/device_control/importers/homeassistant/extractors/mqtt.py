"""MQTT → selena mqtt bridge driver.

MQTT in HA is special: it's infrastructure, not a per-device integration.
Every device discovered via MQTT shares one broker configured at the HA
level. We still extract per-device data (unique_id, discovery topic), but
the real migration blocker is the broker itself: when HA hosts the broker
as the ``core-mosquitto`` add-on, shutting down HA kills the broker and
every MQTT device goes dark — the importer catches that case and asks
the user to supply an alternate broker before committing.
"""
from __future__ import annotations

from typing import Any

from ..types import ExtractionResult, HADevice
from . import register

# Hostnames that unambiguously point at the HA-add-on broker. Anything
# else is assumed to be external and safe to keep running after HA is
# decommissioned.
_HA_ADDON_BROKER_HOSTS = {
    "core-mosquitto",
    "a0d7b954-mosquitto",      # supervisor add-on slug (older builds)
    "homeassistant.local",
    "localhost",
    "127.0.0.1",
    "::1",
}


def _broker_from_context(context: dict[str, Any] | None) -> dict[str, Any] | None:
    """Pull broker config out of extractor context.

    The fetcher injects the MQTT config_entry's data into
    ``context["mqtt_broker"]`` so individual extractor calls don't need
    to rediscover it. ``mqtt_override`` takes precedence so the UI can
    redirect devices to a user-hosted broker without mutating HA state.
    """
    if not context:
        return None
    override = context.get("mqtt_override")
    if override and override.get("broker_host"):
        return dict(override)
    broker = context.get("mqtt_broker")
    if broker and broker.get("broker_host"):
        return dict(broker)
    return None


def _is_ha_hosted_broker(broker_host: str) -> bool:
    return broker_host.strip().lower() in _HA_ADDON_BROKER_HOSTS


def _unique_topic(device: HADevice) -> str | None:
    """Derive the MQTT state topic root from device identifiers.

    HA's MQTT discovery emits [["mqtt", "<unique_id>"]]. The actual
    subscription topics are integration-specific; we pass the unique_id
    through so the bridge driver can build its own topic tree."""
    for ident in device.identifiers:
        if len(ident) >= 2 and ident[0] == "mqtt":
            return ident[1] or None
    return None


def extract(device: HADevice, context: dict[str, Any] | None = None) -> ExtractionResult:
    broker = _broker_from_context(context)
    if broker is None:
        return ExtractionResult(
            status="needs_user_input",
            needs=["mqtt_broker"],
            reason=(
                "No MQTT broker config supplied. SelenaCore needs the "
                "broker host/port to talk to these devices after HA is off."
            ),
        )

    host = str(broker.get("broker_host") or "").strip()
    if _is_ha_hosted_broker(host) and not (context or {}).get("mqtt_override"):
        return ExtractionResult(
            status="needs_user_input",
            needs=["mqtt_broker_override"],
            reason=(
                f"MQTT broker is hosted by Home Assistant ({host}). "
                "Once HA is off the broker dies too. Supply an alternate "
                "broker host (e.g. a standalone Mosquitto) before importing."
            ),
        )

    unique = _unique_topic(device)
    if not unique:
        return ExtractionResult(
            status="unsupported",
            reason="Device has no MQTT unique identifier — cannot route state without it.",
        )

    creds: dict[str, Any] = {
        "broker_host": host,
        "broker_port": int(broker.get("broker_port") or 1883),
        "unique_id": unique,
    }
    for k in ("username", "password", "client_id"):
        if broker.get(k):
            creds[k] = str(broker[k])

    return ExtractionResult(
        status="ok",
        protocol="mqtt",
        entity_type="switch",  # generic — user can PATCH after import
        credentials=creds,
        capabilities=["on", "off"],
    )


register("mqtt", extract)
