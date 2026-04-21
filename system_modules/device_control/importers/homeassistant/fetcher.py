"""Join HA's device / entity / area / config_entry registries into our
normalised ``HADevice`` shape.

Read-only — every command uses a ``config/*/list`` or ``config/*/get``
message type. We keep the join in one place so extractors can assume a
fully-populated ``HADevice`` and avoid sprinkling HA wire-format knowledge
across each extractor module.

Broker detection for MQTT/Z2M extractors also lives here because it
requires inspecting config_entries — mixing that logic into the
per-device extractors would re-query for every MQTT device.
"""
from __future__ import annotations

import logging
from typing import Any

from .client import HAClient
from .types import HADevice, HAEntity

logger = logging.getLogger(__name__)

# HA integration domains we have an extractor for. Everything else still
# appears in the readiness report, but marked "unsupported".
_SUPPORTED = {"tuya", "esphome", "hue", "mqtt", "zigbee2mqtt", "zwave_js"}


async def fetch_all(client: HAClient) -> dict[str, Any]:
    """Pull registries + config entries and return the normalised bundle.

    Returns:
        {
            "devices":     list[HADevice],
            "mqtt_broker": dict | None,   # broker config for MQTT / Z2M
            "ha_version":  str | None,
        }
    """
    device_rows = await client.send_command("config/device_registry/list") or []
    entity_rows = await client.send_command("config/entity_registry/list") or []
    area_rows = await client.send_command("config/area_registry/list") or []
    entries = await _fetch_config_entries(client)

    devices = _join(device_rows, entity_rows, area_rows, entries)
    mqtt_broker = _extract_mqtt_broker(entries)
    return {
        "devices": devices,
        "mqtt_broker": mqtt_broker,
        "ha_version": client.ha_version,
    }


async def _fetch_config_entries(client: HAClient) -> dict[str, dict[str, Any]]:
    """Return config entries keyed by ``entry_id``.

    HA split ``config_entries/get`` (single) and ``config_entries/get_entries``
    (bulk) across versions. Newer HA (≥ 2024.x) exposes ``config_entries/subscribe``
    for pub/sub; we stick to the bulk-get shape. Fall back to the singular
    ``config_entries/list`` for very old deployments.
    """
    try:
        rows = await client.send_command("config_entries/get") or []
    except Exception:
        try:
            rows = await client.send_command("config_entries/list") or []
        except Exception:
            rows = []

    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        entry_id = row.get("entry_id") or row.get("id")
        if entry_id:
            out[entry_id] = row
    return out


def _join(
    device_rows: list[dict[str, Any]],
    entity_rows: list[dict[str, Any]],
    area_rows: list[dict[str, Any]],
    entries: dict[str, dict[str, Any]],
) -> list[HADevice]:
    areas_by_id = {a.get("area_id"): a.get("name") for a in area_rows}
    entities_by_device: dict[str, list[HAEntity]] = {}
    for ent in entity_rows:
        dev_id = ent.get("device_id")
        if not dev_id:
            continue
        entities_by_device.setdefault(dev_id, []).append(HAEntity(
            entity_id=ent.get("entity_id") or "",
            unique_id=ent.get("unique_id"),
            platform=ent.get("platform") or "",
            device_class=ent.get("device_class"),
            disabled_by=ent.get("disabled_by"),
        ))

    out: list[HADevice] = []
    for row in device_rows:
        dev_id = row.get("id") or row.get("device_id")
        if not dev_id:
            continue
        # A device can live under multiple config entries (rare). We pick
        # the first one; extractors should still work because the entry
        # data shape is identical.
        entry_ids = row.get("config_entries") or []
        entry_id = entry_ids[0] if entry_ids else ""
        entry = entries.get(entry_id) or {}
        integration = (entry.get("domain") or "").strip()
        # Some devices are purely virtual (groups, helpers) — they have
        # no config entry. Skip silently.
        if not integration:
            continue

        out.append(HADevice(
            id=str(dev_id),
            name=str(row.get("name_by_user") or row.get("name") or ""),
            area=areas_by_id.get(row.get("area_id")),
            integration=integration,
            entry_id=str(entry_id),
            entry_data=dict(entry.get("data") or {}),
            entry_options=dict(entry.get("options") or {}),
            identifiers=[list(i) for i in (row.get("identifiers") or [])],
            manufacturer=row.get("manufacturer"),
            model=row.get("model"),
            sw_version=row.get("sw_version"),
            entities=entities_by_device.get(dev_id, []),
        ))
    return out


def _extract_mqtt_broker(entries: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    """Find the HA MQTT integration's broker config, if any.

    HA stores broker host/port/credentials on the MQTT config_entry's
    ``data`` blob under the keys below (stable across 2022+).
    """
    for entry in entries.values():
        if (entry.get("domain") or "") != "mqtt":
            continue
        data = entry.get("data") or {}
        broker = str(data.get("broker") or "").strip()
        if not broker:
            continue
        return {
            "broker_host": broker,
            "broker_port": int(data.get("port") or 1883),
            "username": str(data.get("username") or "") or None,
            "password": str(data.get("password") or "") or None,
            "client_id": str(data.get("client_id") or "") or None,
            "base_topic": str(data.get("discovery_prefix") or "homeassistant"),
        }
    return None


def integrations_summary(devices: list[HADevice]) -> dict[str, int]:
    """Count devices per integration — handy for logs / telemetry."""
    counts: dict[str, int] = {}
    for d in devices:
        counts[d.integration] = counts.get(d.integration, 0) + 1
    return counts


def supported_integrations() -> set[str]:
    return set(_SUPPORTED)
