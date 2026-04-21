"""Philips Hue → selena philips_hue driver.

HA's Hue config_entry.data stores the bridge host, the API username
(token) and the bridge id. That's exactly what PhilipsHueDriver needs;
per-light id comes from the device's identifiers list
(``[["hue", "<bridge_id>/<light_id>"]]`` or similar).
"""
from __future__ import annotations

from typing import Any

from ..types import ExtractionResult, HADevice
from . import register


def _light_id_from_identifiers(device: HADevice) -> str | None:
    """Extract the per-light id from HA's device identifiers.

    Older Hue integrations used [["hue", "<light_id>"]]; the v2 API uses
    [["hue", "<bridge_id>/<resource_id>"]]. Pull the trailing component
    either way."""
    for tuple_ in device.identifiers:
        if len(tuple_) >= 2 and tuple_[0] == "hue":
            raw = tuple_[1]
            if "/" in raw:
                return raw.split("/")[-1] or None
            return raw or None
    return None


def extract(device: HADevice, context: dict[str, Any] | None = None) -> ExtractionResult:
    data = device.entry_data or {}
    host = (data.get("host") or "").strip()
    username = (data.get("api_key") or data.get("username") or "").strip()
    if not host or not username:
        return ExtractionResult(
            status="unsupported",
            reason="Hue config entry is missing host or API key — cannot reach the bridge without them.",
        )

    bridge_id = (data.get("bridge_id") or "").strip() or None
    light_id = _light_id_from_identifiers(device)
    if not light_id:
        return ExtractionResult(
            status="unsupported",
            reason="No Hue light id found in device identifiers — likely a bridge-only meta record.",
        )

    # Normalise host to the form PhilipsHueDriver expects (http://host).
    api_host = host if host.startswith(("http://", "https://")) else f"http://{host}"

    creds: dict[str, Any] = {
        "api_host": api_host,
        "token": username,
        "light_id": light_id,
    }
    if bridge_id:
        creds["bridge_id"] = bridge_id

    return ExtractionResult(
        status="ok",
        protocol="philips_hue",
        entity_type="light",
        credentials=creds,
        capabilities=["on", "off", "brightness"],
    )


register("hue", extract)
