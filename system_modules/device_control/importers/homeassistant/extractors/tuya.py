"""Tuya Cloud → selena tuya_local driver.

Tuya's local_key is the credential a ``tuya_local`` driver needs to talk
to a device directly over the LAN (no cloud round-trip). HA stores the
key in its own ``.storage/tuya`` blob, which we deliberately do NOT
reach into — that file is HA-internal and its format changes between
releases. Instead we ask the user for Smart Life cloud creds and
re-fetch local_keys through the exact same SDK our ``tuya_cloud``
provider uses. That path is already exercised in
``drivers/tuya_cloud.py::list_devices()`` — every call runs
``Manager.update_device_cache()`` which refreshes local_key from cloud.

Context expectations (populated by the import routes at preview time):
    context["tuya_devices_by_id"]: dict[str, dict]
        Map of Tuya device id → cloud device record, pre-fetched once
        with the user's Smart Life cloud creds. Per-device extraction
        then reduces to a dict lookup.

If the context is empty the extractor returns ``needs_user_input`` with
the specific need name the UI wizard resolves.
"""
from __future__ import annotations

from typing import Any

from ..types import ExtractionResult, HADevice
from . import register


def _tuya_device_id(device: HADevice) -> str | None:
    """HA's Tuya integration puts the cloud device id in identifiers."""
    for ident in device.identifiers:
        if len(ident) >= 2 and ident[0] in ("tuya", "tuyav2", "localtuya"):
            return (ident[1] or "").strip() or None
    return None


def _classify(cloud_device: dict[str, Any]) -> tuple[str, list[str]]:
    """Reuse the same category/keyword heuristics as the Tuya Cloud wizard.

    Deferred import to avoid a hard dependency on routes.py at module
    import time (tests exercise this without building the FastAPI app)."""
    try:
        from system_modules.device_control.routes import _classify_tuya_entity_type
        return _classify_tuya_entity_type(cloud_device)
    except Exception:  # pragma: no cover — defensive fallback
        return "switch", ["on", "off"]


def extract(device: HADevice, context: dict[str, Any] | None = None) -> ExtractionResult:
    tuya_id = _tuya_device_id(device)
    if not tuya_id:
        return ExtractionResult(
            status="unsupported",
            reason="HA device has no Tuya id in its identifiers — cannot map to a cloud record.",
        )

    ctx = context or {}
    devices_by_id = ctx.get("tuya_devices_by_id")
    if devices_by_id is None:
        return ExtractionResult(
            status="needs_user_input",
            needs=["tuya_cloud_creds"],
            reason=(
                "Tuya local_key must be fetched from Tuya cloud. "
                "Provide Smart Life authorization code to proceed."
            ),
        )

    cloud = devices_by_id.get(tuya_id)
    if not cloud:
        return ExtractionResult(
            status="unsupported",
            reason=(
                f"Tuya device {tuya_id} is not in the connected Smart Life "
                "account. It may belong to a different Tuya account."
            ),
        )

    local_key = str(cloud.get("local_key") or "").strip()
    if not local_key:
        return ExtractionResult(
            status="unsupported",
            reason=(
                f"Tuya device {tuya_id} has no local_key in the cloud "
                "response — it does not support LAN control."
            ),
        )

    entity_type, capabilities = _classify(cloud)
    creds: dict[str, Any] = {
        "device_id": tuya_id,
        "local_key": local_key,
        "version": str(cloud.get("version") or "3.3"),
    }
    # Cloud response sometimes carries a LAN ip — pass it through when
    # trusted; otherwise the tuya_local driver will broadcast-discover.
    ip = str(cloud.get("ip") or "").strip()
    if ip:
        creds["ip"] = ip

    return ExtractionResult(
        status="ok",
        protocol="tuya_local",
        entity_type=entity_type,
        credentials=creds,
        capabilities=capabilities,
    )


register("tuya", extract)
