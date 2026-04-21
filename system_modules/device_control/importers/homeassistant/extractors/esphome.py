"""ESPHome → selena esphome driver.

HA's ESPHome config_entry.data stores everything the native-API driver
needs (host, port, password, optional noise encryption key). No extra
user input required — extraction is status=ok whenever those fields are
present.
"""
from __future__ import annotations

from typing import Any

from ..types import ExtractionResult, HADevice
from . import register

_DEFAULT_PORT = 6053


def _guess_entity_type(device: HADevice) -> str:
    """ESPHome devices can advertise multiple entities; pick the dominant
    entity_type for the Selena Device row. Fall back to "switch" because
    that is the most common ESPHome role (relays, outlets)."""
    counts: dict[str, int] = {}
    for ent in device.entities:
        et = (ent.entity_id.split(".", 1)[0] or "").lower()
        if not et:
            continue
        counts[et] = counts.get(et, 0) + 1
    if not counts:
        return "switch"
    # Prefer "light" → "switch" → "outlet" → anything else.
    for preferred in ("light", "switch", "outlet", "sensor", "fan", "climate"):
        if preferred in counts:
            return preferred
    return next(iter(counts))


def extract(device: HADevice, context: dict[str, Any] | None = None) -> ExtractionResult:
    data = device.entry_data or {}
    host = (data.get("host") or "").strip()
    if not host:
        return ExtractionResult(
            status="unsupported",
            reason="ESPHome config entry has no host — nothing to connect to.",
        )
    port = int(data.get("port") or _DEFAULT_PORT)
    creds: dict[str, Any] = {"host": host, "port": port}
    if data.get("password"):
        creds["password"] = str(data["password"])
    # HA calls it "noise_psk" (aioesphomeapi's encryption_key).
    if data.get("noise_psk"):
        creds["encryption_key"] = str(data["noise_psk"])

    entity_type = _guess_entity_type(device)
    capabilities = ["on", "off"] if entity_type in ("light", "switch", "outlet", "fan") else []

    return ExtractionResult(
        status="ok",
        protocol="esphome",
        entity_type=entity_type,
        credentials=creds,
        capabilities=capabilities,
    )


register("esphome", extract)
