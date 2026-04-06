"""
system_modules/device_control/routes.py — REST router.

Mounted by core at ``/api/ui/modules/device-control/``.

Endpoints:
    GET    /devices                       — list (filtered to module_id="device-control")
    POST   /devices                       — manual add
    PATCH  /devices/{device_id}           — edit name/location/meta (auto-translates name_en)
    DELETE /devices/{device_id}           — delete + cleanup auto_entity patterns
    POST   /devices/{device_id}/test      — toggle on→off→on
    POST   /devices/{device_id}/command   — arbitrary state update
    GET    /drivers                       — list of supported driver types
    GET    /tuya/wizard/status            — return whether cloud creds saved
    POST   /tuya/wizard/start              — step 1: user_code → qr_code payload
    POST   /tuya/wizard/poll              — step 2: block until user scans QR
    POST   /tuya/wizard/refresh           — re-query Smart Life devices (no new wizard)
    POST   /tuya/wizard/import            — bulk-import selected cloud devices
    POST   /tuya/wizard/disconnect        — wipe stored cloud creds
    GET    /tuya/wizard/qr.png            — render the QR payload as a PNG image
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from core.api.helpers import on_entity_changed, translate_to_en

from .drivers import DriverError, list_driver_types
from .drivers.tuya_cloud import TuyaCloudClient

if TYPE_CHECKING:
    from .module import DeviceControlModule

logger = logging.getLogger(__name__)


# ── Pydantic models ──────────────────────────────────────────────────────


class AddDeviceBody(BaseModel):
    name: str
    entity_type: str           # "light" | "switch" | "outlet" | ...
    location: str = ""
    protocol: str              # "tuya_local" | "tuya_cloud" | "mqtt"
    type: str = "actuator"     # sensor | actuator | controller | virtual
    capabilities: list[str] = Field(default_factory=lambda: ["on", "off"])
    meta: dict[str, Any] = Field(default_factory=dict)


class PatchDeviceBody(BaseModel):
    name: str | None = None
    entity_type: str | None = None
    location: str | None = None
    capabilities: list[str] | None = None
    meta: dict[str, Any] | None = None


class CommandBody(BaseModel):
    state: dict[str, Any]


class WizardStartBody(BaseModel):
    """Step 1 of the new user-code wizard.

    ``user_code`` is the 6–8 character code the user gets from Smart Life:
    Me → ⚙️ icon → "Authorization code" (or "Third-party integration").
    The code is single-use and expires in ~10 minutes.
    """
    user_code: str


class WizardPollBody(BaseModel):
    user_code: str


class WizardImportBody(BaseModel):
    selected_ids: list[str]


# ── Helpers ──────────────────────────────────────────────────────────────


async def _device_to_dict(d: Any) -> dict[str, Any]:
    return {
        "device_id": d.device_id,
        "name": d.name,
        "type": d.type,
        "protocol": d.protocol,
        "entity_type": d.entity_type,
        "location": d.location,
        "capabilities": json.loads(d.capabilities) if d.capabilities else [],
        "meta": json.loads(d.meta) if d.meta else {},
        "state": json.loads(d.state) if d.state else {},
        "last_seen": d.last_seen.timestamp() if d.last_seen else None,
        "module_id": d.module_id,
    }


# ── Router builder ───────────────────────────────────────────────────────


def build_router(svc: "DeviceControlModule") -> APIRouter:
    router = APIRouter()

    @router.get("/health")
    async def health():
        return {"status": "ok", "module": svc.name, "watchers": len(svc._watch_tasks)}

    # ── Devices CRUD ────────────────────────────────────────────────────

    @router.get("/devices")
    async def list_devices() -> dict[str, Any]:
        from core.registry.models import Device
        async with svc._db_session() as session:
            res = await session.execute(
                select(Device).where(Device.module_id == svc.name)
            )
            rows = list(res.scalars())
        return {"devices": [await _device_to_dict(d) for d in rows]}

    @router.post("/devices", status_code=201)
    async def add_device(body: AddDeviceBody) -> dict[str, Any]:
        from core.registry.models import Device
        if body.protocol not in ("tuya_local", "tuya_cloud", "mqtt"):
            raise HTTPException(422, f"Unsupported protocol: {body.protocol}")
        async with svc._db_session() as session:
            async with session.begin():
                device = Device(
                    name=body.name.strip(),
                    type=body.type,
                    protocol=body.protocol,
                    entity_type=body.entity_type or None,
                    location=(body.location or "").strip() or None,
                    module_id=svc.name,
                )
                device.set_capabilities(body.capabilities)
                device.set_meta(body.meta)
                session.add(device)
                await session.flush()
                device_id = device.device_id
                payload = await _device_to_dict(device)
        # Trigger pattern regeneration outside the session.
        try:
            await on_entity_changed("device", device_id, "created")
        except Exception as exc:
            logger.warning("device-control: pattern regen failed: %s", exc)
        await svc.add_device_watcher(device_id)
        await svc.publish("device.registered", {"device_id": device_id, "name": body.name})
        return payload

    @router.patch("/devices/{device_id}")
    async def patch_device(device_id: str, body: PatchDeviceBody) -> dict[str, Any]:
        from core.registry.models import Device

        # Read current row to compute post-patch "effective" values.
        async with svc._db_session() as session:
            d = await session.get(Device, device_id)
            if d is None or d.module_id != svc.name:
                raise HTTPException(404, "Device not found")
            existing_meta: dict = json.loads(d.meta) if d.meta else {}
            current_name = d.name or ""
            current_location = d.location or ""
            current_name_en = (existing_meta.get("name_en") or "").strip()

        # Effective display name after the patch.
        effective_name = (
            body.name.strip() if body.name is not None else current_name
        ) or ""

        # Effective location after the patch. A patch with ``location=""``
        # (empty string) means *clear the location*; ``None`` (field not in
        # the JSON body) means *keep it as is*. This matters because the
        # UI's Edit dialog always sends ``location`` and we must not wipe
        # it on every save.
        location_changed = body.location is not None
        if location_changed:
            effective_location = body.location.strip()
        else:
            effective_location = current_location

        # Meta merge: caller either sent a full meta (overwrite) or None
        # (keep existing).
        new_meta: dict = body.meta if body.meta is not None else dict(existing_meta)

        # ── Auto-translate name_en ──────────────────────────────────────
        # Only regenerate if:
        #   (a) caller didn't provide one AND
        #   (b) the display name actually changed since last time.
        desired_name_en = (new_meta.get("name_en") or "").strip()
        if not desired_name_en and effective_name:
            # Don't re-translate on every save — only when name changed or
            # no English form exists yet.
            if not current_name_en or effective_name != current_name:
                try:
                    translated = await translate_to_en(effective_name)
                except Exception:
                    translated = effective_name
                desired_name_en = (translated or "").strip().lower()
            else:
                desired_name_en = current_name_en
        if desired_name_en:
            new_meta["name_en"] = desired_name_en
        else:
            new_meta.pop("name_en", None)

        # ── Auto-translate location ─────────────────────────────────────
        # Same rules: only if location actually came in the patch and is
        # non-ASCII.
        final_location: str | None = effective_location or None
        if location_changed and final_location and not final_location.isascii():
            try:
                loc_en = await translate_to_en(final_location)
                final_location = (loc_en or final_location).strip().lower()
            except Exception:
                pass

        async with svc._db_session() as session:
            async with session.begin():
                d = await session.get(Device, device_id)
                if d is None or d.module_id != svc.name:
                    raise HTTPException(404, "Device not found")
                if body.name is not None:
                    d.name = body.name.strip()
                if body.entity_type is not None:
                    d.entity_type = body.entity_type or None
                if location_changed:
                    d.location = final_location
                if body.capabilities is not None:
                    d.set_capabilities(body.capabilities)
                d.set_meta(new_meta)
                payload = await _device_to_dict(d)
        try:
            await on_entity_changed("device", device_id, "updated")
        except Exception as exc:
            logger.warning("device-control: pattern regen failed: %s", exc)
        # Restart watcher to pick up new meta (e.g. new IP / DPS map).
        await svc.remove_device_watcher(device_id)
        await svc.add_device_watcher(device_id)
        return payload

    @router.delete("/devices/{device_id}")
    async def delete_device(device_id: str) -> dict[str, Any]:
        from core.registry.models import Device
        async with svc._db_session() as session:
            async with session.begin():
                d = await session.get(Device, device_id)
                if d is None or d.module_id != svc.name:
                    raise HTTPException(404, "Device not found")
                await session.execute(delete(Device).where(Device.device_id == device_id))
        await svc.remove_device_watcher(device_id)
        try:
            await on_entity_changed("device", device_id, "deleted")
        except Exception as exc:
            logger.warning("device-control: pattern delete failed: %s", exc)
        await svc.publish("device.removed", {"device_id": device_id})
        return {"status": "ok", "device_id": device_id}

    @router.post("/devices/{device_id}/test")
    async def test_device(device_id: str) -> dict[str, Any]:
        try:
            await svc.execute_command(device_id, {"on": True})
            await asyncio.sleep(1.0)
            await svc.execute_command(device_id, {"on": False})
            await asyncio.sleep(1.0)
            await svc.execute_command(device_id, {"on": True})
            return {"status": "ok"}
        except DriverError as exc:
            raise HTTPException(502, f"Driver error: {exc}")

    @router.post("/devices/{device_id}/command")
    async def send_command(device_id: str, body: CommandBody) -> dict[str, Any]:
        try:
            await svc.execute_command(device_id, body.state)
            return {"status": "ok", "state": body.state}
        except DriverError as exc:
            raise HTTPException(502, f"Driver error: {exc}")

    # ── Drivers metadata ────────────────────────────────────────────────

    @router.get("/drivers")
    async def list_drivers() -> dict[str, Any]:
        return {"drivers": list_driver_types()}

    # ── Tuya cloud wizard (user-code flow) ──────────────────────────────

    @router.get("/tuya/wizard/status")
    async def wizard_status() -> dict[str, Any]:
        return TuyaCloudClient.get().status_summary()

    @router.post("/tuya/wizard/start")
    async def wizard_start(body: WizardStartBody) -> dict[str, Any]:
        """Step 1: user enters their Smart Life user_code → we fetch a QR.

        Returns ``{qr_url, qr_token}``. Frontend renders qr_url as a QR image,
        user scans it with Smart Life, then frontend polls ``/tuya/wizard/poll``.
        """
        code = (body.user_code or "").strip()
        if not code:
            raise HTTPException(422, "user_code is required")
        # Reset any stale session so we start clean.
        TuyaCloudClient.reset()
        client = TuyaCloudClient.get()
        try:
            result = await asyncio.to_thread(client.start_qr_login, code)
        except DriverError as exc:
            logger.warning("Tuya wizard start failed: %s", exc)
            raise HTTPException(502, str(exc))
        except Exception as exc:
            logger.exception("Tuya wizard start crashed")
            raise HTTPException(500, f"Unexpected error: {exc}")
        return {"status": "pending", **result}

    @router.post("/tuya/wizard/poll")
    async def wizard_poll(body: WizardPollBody) -> dict[str, Any]:
        """Step 2: block until the user scans the QR in Smart Life, or timeout.

        On success returns ``{status: "ok", devices: [...]}``. The response
        time is bounded by the Tuya timeout (~3 minutes).
        """
        code = (body.user_code or "").strip()
        if not code:
            raise HTTPException(422, "user_code is required")
        client = TuyaCloudClient.get()
        try:
            result = await client.poll_login(code)
        except DriverError as exc:
            logger.warning("Tuya wizard poll failed: %s", exc)
            raise HTTPException(504, str(exc))
        except Exception as exc:
            logger.exception("Tuya wizard poll crashed")
            raise HTTPException(500, f"Unexpected error: {exc}")
        return result

    @router.post("/tuya/wizard/disconnect")
    async def wizard_disconnect() -> dict[str, Any]:
        TuyaCloudClient.wipe_creds()
        TuyaCloudClient.reset()
        return {"status": "ok"}

    @router.post("/tuya/wizard/refresh")
    async def wizard_refresh() -> dict[str, Any]:
        """Re-query the Smart Life account for its current device list.

        Use this after adding a new device in the Smart Life app — the
        stored credentials are reused, no new user_code / QR scan needed.
        Returns the full list, same shape as the wizard's poll result.
        """
        client = TuyaCloudClient.get()
        try:
            devices = await client.list_devices()
        except DriverError as exc:
            logger.warning("Tuya wizard refresh failed: %s", exc)
            raise HTTPException(502, str(exc))
        return {"status": "ok", "devices": devices}

    @router.get("/tuya/wizard/qr.png")
    async def wizard_qr_png(url: str) -> Response:
        """Render a Tuya ``tuyaSmart--qrLogin?token=...`` URL as a PNG QR.

        The frontend passes the exact URL returned by ``/tuya/wizard/start``
        as the ``?url=`` query param.
        """
        import io
        import qrcode  # type: ignore
        img = qrcode.make(url)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return Response(content=buf.getvalue(), media_type="image/png")

    @router.post("/tuya/wizard/import")
    async def wizard_import(body: WizardImportBody) -> dict[str, Any]:
        from core.registry.models import Device

        client = TuyaCloudClient.get()
        try:
            cloud_devices = await client.list_devices()
        except DriverError as exc:
            raise HTTPException(502, str(exc))

        wanted = {d["id"] for d in cloud_devices if d["id"] in set(body.selected_ids)}
        cloud_by_id = {d["id"]: d for d in cloud_devices}

        created: list[dict[str, Any]] = []
        skipped: list[str] = []

        for cid in body.selected_ids:
            cd = cloud_by_id.get(cid)
            if cd is None:
                skipped.append(cid)
                continue
            # Pick local LAN if we have ip + local_key, else fall back to cloud.
            has_local = bool(cd.get("ip")) and bool(cd.get("local_key"))
            protocol = "tuya_local" if has_local else "tuya_cloud"
            # Auto-detect the "switch" code: Tuya devices expose their on/off
            # status under one of several codes ("switch", "switch_1",
            # "switch_led", etc.). Pick the first one we recognise; fall back
            # to switch_1 which covers 90% of devices.
            status_dict = cd.get("status") or {}
            on_code = "switch_1"
            for candidate in ("switch", "switch_1", "switch_led", "switch_led_1"):
                if candidate in status_dict:
                    on_code = candidate
                    break
            # Auto-translate the device display name to English for voice
            # patterns — Tuya often returns names in the user's native
            # language (Chinese, Ukrainian, …). ``translate_to_en`` short-
            # circuits to the original if it's already ASCII.
            raw_name = cd.get("name") or cd["id"]
            try:
                name_en = await translate_to_en(raw_name)
            except Exception:
                name_en = raw_name
            meta: dict[str, Any] = {
                "tuya": {
                    "device_id": cd["id"],
                    "cloud_device_id": cd["id"],
                    "local_key": cd.get("local_key", ""),
                    "ip": cd.get("ip", ""),
                    "version": str(cd.get("version", "3.3")),
                    "dps_map": {"on": "1"},        # local LAN DPS index (default)
                    "code_map": {"on": on_code},   # cloud status code (auto-detected)
                    "category": cd.get("category", ""),
                    "product_name": cd.get("product_name", ""),
                },
                "name_en": (name_en or "").strip().lower() or None,
            }
            # Drop None so we don't store 'null' in JSON.
            if meta["name_en"] is None:
                meta.pop("name_en", None)
            async with svc._db_session() as session:
                async with session.begin():
                    device = Device(
                        name=cd.get("name") or cd["id"],
                        type="actuator",
                        protocol=protocol,
                        entity_type="switch",
                        module_id=svc.name,
                    )
                    device.set_capabilities(["on", "off"])
                    device.set_meta(meta)
                    session.add(device)
                    await session.flush()
                    device_id = device.device_id
            try:
                await on_entity_changed("device", device_id, "created")
            except Exception:
                pass
            await svc.add_device_watcher(device_id)
            created.append({"device_id": device_id, "name": cd.get("name", cid), "protocol": protocol})

        return {"status": "ok", "created": created, "skipped": skipped}

    return router
