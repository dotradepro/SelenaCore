"""Z-Wave JS → always unsupported.

A Z-Wave controller is physically bonded to the HA host (USB stick or
network-attached dongle) and its secure network keys live in that
hardware's memory. Migrating those devices requires moving the controller
itself — SelenaCore does not run a Z-Wave controller and never will (see
plan: "USB-стіки для Zigbee, Z-Wave — поза межами цього плану
назавжди"). Report clearly instead of pretending we can.
"""
from __future__ import annotations

from typing import Any

from ..types import ExtractionResult, HADevice
from . import register


def extract(device: HADevice, context: dict[str, Any] | None = None) -> ExtractionResult:
    return ExtractionResult(
        status="unsupported",
        reason=(
            "Z-Wave devices are bonded to the controller that joined them. "
            "SelenaCore does not host a Z-Wave controller; migrating "
            "requires moving the physical stick and re-commissioning each "
            "device — out of scope for this importer."
        ),
    )


register("zwave_js", extract)
