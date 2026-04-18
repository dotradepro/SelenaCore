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

import httpx

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from core.api.helpers import on_entity_changed, translate_to_en

from .drivers import DriverError, list_driver_types
from .drivers.gree import AC_CAPABILITIES
from .drivers.tuya_cloud import TuyaCloudClient

if TYPE_CHECKING:
    from .module import DeviceControlModule


#: Strong references to fire-and-forget background tasks spawned by routes
#: (currently only Matter pattern regeneration). Without this set the GC
#: can collect a task whose only reference lives inside the event loop's
#: weak set, killing it before it finishes — see PEP 654 / asyncio docs:
#:     https://docs.python.org/3/library/asyncio-task.html#asyncio.create_task
#: The done-callback discards each task once it completes so the set never
#: grows unboundedly.
_BG_TASKS: set[asyncio.Task[Any]] = set()


def _spawn_bg(coro: Any, *, name: str | None = None) -> asyncio.Task[Any]:
    """Schedule ``coro`` and keep a strong reference until it finishes."""
    task = asyncio.create_task(coro, name=name)
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)
    return task

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
    enabled: bool | None = None


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


class GreeDiscoverBody(BaseModel):
    timeout: int = 10


class GreeImportEntry(BaseModel):
    ip: str
    mac: str
    name: str = ""
    location: str = ""


class GreeImportBody(BaseModel):
    devices: list[GreeImportEntry]


class HueDiscoverBody(BaseModel):
    api_host: str          # e.g. "http://192.168.1.254:7000"
    token: str             # Hue API username / token


class HueImportEntry(BaseModel):
    light_id: int | str
    name: str = ""
    location: str = ""
    hue_type: str = ""   # raw Hue API "type" string, e.g. "Extended color light"


class HueImportBody(BaseModel):
    api_host: str
    token: str
    devices: list[HueImportEntry]


_HUE_TYPE_TO_ENTITY: tuple[tuple[tuple[str, ...], str], ...] = (
    # Order matters — first match wins; match is lowercase substring.
    (("plug", "outlet"),                       "outlet"),
    (("on/off plug", "on/off light"),          "outlet"),
    (("dimmer switch", "smart button",
      "tap dial", "wall switch"),              "switch"),
    (("motion sensor", "light sensor",
      "temperature sensor", "presence"),       "sensor"),
    (("contact sensor", "door"),               "door_lock"),
    # Anything that contains "light" falls through to "light" below.
)


def _hue_type_to_entity(hue_type: str | None) -> str:
    """Map a Hue API ``type`` string to our canonical entity_type.

    Hue's discovery endpoint reports types like "Extended color light",
    "Dimmable light", "On/Off plug-in unit", "ZLLSwitch". Before this
    mapping the import hardcoded ``entity_type="light"`` for every row,
    which broke type-based voice resolution for plugs, switches and
    sensors imported from a Hue bridge. Falls back to ``light`` because
    that is Hue's majority type.
    """
    s = (hue_type or "").strip().lower()
    if not s:
        return "light"
    for needles, et in _HUE_TYPE_TO_ENTITY:
        if any(n in s for n in needles):
            return et
    return "light"


class Z2mDiscoverBody(BaseModel):
    mqtt_host: str = "localhost"
    mqtt_port: int = 1883


class Z2mImportEntry(BaseModel):
    friendly_name: str
    ieee_address: str = ""
    name: str = ""
    entity_type: str = "sensor"
    location: str = ""


class Z2mImportBody(BaseModel):
    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    base_topic: str = "zigbee2mqtt"
    devices: list[Z2mImportEntry]


class ProviderUninstallBody(BaseModel):
    remove_package: bool = False


# ── Helpers ──────────────────────────────────────────────────────────────


def _is_private_ip(ip: str) -> bool:
    """Return True if ``ip`` looks like an RFC1918 LAN address.

    Tuya cloud often returns the public WAN IP of the user's router in the
    device's ``ip`` field — that address is useless for tinytuya which needs
    to TCP-connect on port 6668 inside the local network. We use this to
    decide whether the cloud-supplied IP is trustworthy.
    """
    if not ip:
        return False
    try:
        import ipaddress
        return ipaddress.ip_address(ip).is_private
    except (ValueError, TypeError):
        return False


async def _scan_tuya_lan() -> dict[str, dict[str, Any]]:
    """Run tinytuya LAN broadcast scan, return ``{gwId: {ip, version}}``.

    Blocks for ~15 seconds while listening for Tuya UDP broadcasts on
    ports 6666/6667/6668. Tuya devices broadcast every few seconds, so
    ``maxretry=15`` (matching tinytuya default) catches each device with
    high reliability. Runs in a thread to keep the event loop alive.
    Returns an empty dict on any failure (we treat scan as best-effort —
    devices not found just stay disabled).
    """
    def _do_scan() -> dict[str, dict[str, Any]]:
        try:
            import tinytuya  # type: ignore
        except ImportError:
            return {}
        try:
            raw = tinytuya.deviceScan(
                verbose=False, maxretry=15, color=False, poll=False, forcescan=False,
            )
        except Exception as exc:
            logger.warning("tinytuya LAN scan failed: %s", exc)
            return {}
        out: dict[str, dict[str, Any]] = {}
        for ip, info in (raw or {}).items():
            gw_id = info.get("gwId") or info.get("id")
            if not gw_id:
                continue
            out[str(gw_id)] = {
                "ip": str(ip),
                "version": str(info.get("version") or "3.3"),
            }
        return out

    return await asyncio.to_thread(_do_scan)


def _classify_tuya_entity_type(cloud_device: dict[str, Any]) -> tuple[str, list[str]]:
    """Best-effort entity_type + capabilities for a Tuya cloud device.

    Tuya's ``category`` and ``product_name`` fields are reasonably
    reliable hints. Falls back to ``switch`` for anything we can't
    classify, which the user can correct via PATCH /devices/{id}.
    """
    category = (cloud_device.get("category") or "").lower()
    product = (cloud_device.get("product_name") or "").lower()
    name = (cloud_device.get("name") or "").lower()
    text = f"{product} {name}"

    # Lighting (Tuya category "dj" = lighting devices)
    if category == "dj" or any(k in text for k in ("light", "lamp", "bulb", "led", "лампа", "світло", "освітлення")):
        caps = ["on", "off"]
        # If status payload exposes brightness/colour codes, advertise them
        status = cloud_device.get("status") or {}
        if any(k in status for k in ("bright_value", "bright_value_v2", "bright_value_1")):
            caps.append("brightness")
        if any(k in status for k in ("temp_value", "temp_value_v2", "colour_data")):
            caps.append("colour_temp")
        return "light", caps
    # Outlets / sockets (Tuya category "cz")
    if category == "cz" or any(k in text for k in ("socket", "outlet", "plug", "розетка")):
        return "outlet", ["on", "off"]
    # Fans
    if category == "fs" or "fan" in text or "вентилятор" in text:
        return "fan", ["on", "off"]
    # Default
    return "switch", ["on", "off"]


async def _scan_gree_lan(timeout: int = 10) -> list[dict[str, Any]]:
    """Run greeclimate LAN broadcast discovery, return a list of dicts.

    Each entry: ``{ip, mac, name, brand, model, version}``. Returns an empty
    list on any failure (best-effort scan).
    """
    try:
        from greeclimate.discovery import Discovery  # type: ignore
    except ImportError:
        logger.warning("greeclimate not installed — Gree discovery disabled")
        return []

    found: list[dict[str, Any]] = []
    try:
        discovery = Discovery(timeout=timeout)
        # greeclimate.Discovery.scan() yields/returns DeviceInfo objects.
        # API differs subtly across versions: 1.x exposes scan() as a coroutine
        # returning a list, 2.x supports both. Try the coroutine path first.
        try:
            results = await discovery.scan(wait_for=timeout)
        except TypeError:
            results = await discovery.scan()
        for di in results or []:
            try:
                found.append({
                    "ip": getattr(di, "ip", "") or "",
                    "mac": getattr(di, "mac", "") or "",
                    "name": getattr(di, "name", "") or "",
                    "brand": getattr(di, "brand", "") or "gree",
                    "model": getattr(di, "model", "") or "",
                    "version": getattr(di, "version", "") or "",
                })
            except Exception:  # pragma: no cover - defensive
                continue
    except Exception as exc:
        logger.warning("Gree discovery failed: %s", exc)
    return found


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
        "enabled": bool(d.enabled),
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
        from .providers.catalog import PROVIDERS
        if body.protocol not in PROVIDERS:
            raise HTTPException(422, f"Unsupported protocol: {body.protocol}")

        # Normalise + auto-translate native name/location so the intent
        # router's keyword filter has something to match against. The
        # user-supplied meta never carries ``name_en``/``location_en`` —
        # they are server-derived on every save (POST and PATCH alike).
        from system_modules.llm_engine.intent_router import _normalize_en

        native_name = (body.name or "").strip()
        native_location = (body.location or "").strip() or None

        meta: dict = dict(body.meta or {})
        meta.pop("name_en", None)
        meta.pop("location_en", None)

        if native_name:
            if native_name.isascii():
                meta["name_en"] = _normalize_en(native_name)
            else:
                # Lowercase the input: Argos returns wildly different
                # translations for "Кабінет" vs "кабінет" vs "КАБІНЕТ"
                # on single words. Force lowercase so the output is
                # deterministic and article-free.
                try:
                    translated = await translate_to_en(native_name.lower())
                except Exception:
                    translated = native_name
                meta["name_en"] = _normalize_en(translated or native_name)

        if native_location:
            meta["location"] = native_location  # mirror for consistency
            if native_location.isascii():
                meta["location_en"] = _normalize_en(native_location)
            else:
                try:
                    translated_loc = await translate_to_en(native_location.lower())
                except Exception:
                    translated_loc = ""
                meta["location_en"] = _normalize_en(translated_loc)

        async with svc._db_session() as session:
            async with session.begin():
                device = Device(
                    name=native_name,
                    type=body.type,
                    protocol=body.protocol,
                    entity_type=body.entity_type or None,
                    location=native_location,
                    module_id=svc.name,
                )
                device.set_capabilities(body.capabilities)
                device.set_meta(meta)
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
        # Enriched payload — subscribers (climate, lights-switches,
        # energy-monitor) use entity_type to decide if they own this device.
        await svc.publish("device.registered", {
            "device_id": device_id,
            "name": body.name,
            "entity_type": payload.get("entity_type"),
            "location": payload.get("location"),
            "protocol": payload.get("protocol"),
            "capabilities": payload.get("capabilities", []),
        })
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
        # (keep existing). Strip any caller-provided name_en/location_en —
        # those fields are SERVER-DERIVED ONLY (LLM auto-translation). The
        # editor UI must not let the user type them by hand: with many
        # devices in many rooms, manual EN entries lead to typos and
        # collisions that silently break voice patterns.
        new_meta: dict = body.meta if body.meta is not None else dict(existing_meta)
        new_meta.pop("name_en", None)
        new_meta.pop("location_en", None)

        from system_modules.llm_engine.intent_router import _normalize_en

        # ── Auto-translate name → meta.name_en (server-only) ────────────
        # Always re-translate on save. Previous implementation cached the
        # first translation forever — which means a stale Argos output
        # (e.g. "кабінет" → "cabinet" from an older model version) would
        # persist across every subsequent save. The user has no way to
        # refresh it without deleting the device. The ~50 ms Argos call
        # is not a concern on a save path.
        desired_name_en = ""
        if effective_name:
            if effective_name.isascii():
                desired_name_en = _normalize_en(effective_name)
            else:
                # Lowercase input: Argos is case-sensitive and returns
                # different words for "Кабінет" vs "кабінет". Force
                # lowercase to get stable, article-free output.
                try:
                    translated = await translate_to_en(effective_name.lower())
                except Exception:
                    translated = effective_name
                desired_name_en = _normalize_en(translated or effective_name)
        if desired_name_en:
            new_meta["name_en"] = desired_name_en

        # ── Auto-translate location → meta.location_en (server-only) ────
        # Keep the user-language string in ``device.location`` (so the UI
        # shows "Кабінет", not "office"). ``meta.location_en`` is the
        # English form the intent router's keyword filter matches against.
        # _normalize_en strips Argos artefacts like leading "the "/"a ".
        final_location: str | None = effective_location or None
        desired_location_en = ""
        if final_location:
            if final_location.isascii():
                desired_location_en = _normalize_en(final_location)
            else:
                try:
                    translated_loc = await translate_to_en(final_location.lower())
                except Exception:
                    translated_loc = ""
                desired_location_en = _normalize_en(translated_loc)
        if desired_location_en:
            new_meta["location_en"] = desired_location_en
        # Mirror the native-language location inside meta so the UI and
        # the filter both have a single source of truth even when legacy
        # ``d.location`` drifts (it defaulted to None on some earlier
        # CRUD paths).
        if final_location:
            new_meta["location"] = final_location

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
                if body.enabled is not None:
                    d.enabled = bool(body.enabled)
                d.set_meta(new_meta)
                payload = await _device_to_dict(d)
        try:
            await on_entity_changed("device", device_id, "updated")
        except Exception as exc:
            logger.warning("device-control: pattern regen failed: %s", exc)
        # Restart watcher to pick up new meta (e.g. new IP / DPS map).
        # add_device_watcher itself skips disabled devices.
        await svc.remove_device_watcher(device_id)
        await svc.add_device_watcher(device_id)
        return payload

    @router.delete("/devices/{device_id}")
    async def delete_device(device_id: str) -> dict[str, Any]:
        from core.registry.models import Device
        # Capture entity_type BEFORE the row vanishes so the bus event
        # can carry it to subscribers (energy-monitor needs it to drop
        # the matching source).
        captured_entity: str | None = None
        captured_name: str | None = None
        async with svc._db_session() as session:
            async with session.begin():
                d = await session.get(Device, device_id)
                if d is None or d.module_id != svc.name:
                    raise HTTPException(404, "Device not found")
                captured_entity = d.entity_type
                captured_name = d.name
                await session.execute(delete(Device).where(Device.device_id == device_id))
        await svc.remove_device_watcher(device_id)
        try:
            await on_entity_changed("device", device_id, "deleted")
        except Exception as exc:
            logger.warning("device-control: pattern delete failed: %s", exc)
        await svc.publish("device.removed", {
            "device_id": device_id,
            "name": captured_name,
            "entity_type": captured_entity,
        })
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

    # ── Provider lifecycle (install/uninstall driver libraries at runtime) ──

    @router.get("/providers")
    async def list_providers() -> dict[str, Any]:
        loader = svc.provider_loader
        if loader is None:
            return {"providers": []}
        return {"providers": await loader.list_state()}

    @router.post("/providers/{provider_id}/install")
    async def install_provider(provider_id: str) -> dict[str, Any]:
        loader = svc.provider_loader
        if loader is None:
            raise HTTPException(503, "Provider loader not initialised")
        ok, msg = await loader.install(provider_id)
        if not ok:
            raise HTTPException(502, msg)
        return {"status": "ok", "provider": provider_id, "message": msg, "restart_needed": False}

    @router.post("/providers/{provider_id}/uninstall")
    async def uninstall_provider(
        provider_id: str, body: ProviderUninstallBody | None = None,
    ) -> dict[str, Any]:
        loader = svc.provider_loader
        if loader is None:
            raise HTTPException(503, "Provider loader not initialised")
        remove_package = bool(body and body.remove_package)
        ok, msg = await loader.uninstall(provider_id, remove_package=remove_package)
        if not ok:
            raise HTTPException(502, msg)
        return {"status": "ok", "provider": provider_id, "message": msg}

    # ── Gree / Pular discovery + import ─────────────────────────────────

    @router.post("/gree/discover")
    async def gree_discover(body: GreeDiscoverBody | None = None) -> dict[str, Any]:
        """LAN-broadcast scan for Gree-protocol A/C units (incl. Pular)."""
        timeout = max(2, min(30, (body.timeout if body else 10)))
        found = await _scan_gree_lan(timeout=timeout)
        return {"status": "ok", "devices": found}

    @router.post("/gree/import")
    async def gree_import(body: GreeImportBody) -> dict[str, Any]:
        """Bulk-create Device rows from a Gree discovery result.

        Each entry must already have ip + mac. The Gree per-device key is
        not known until the driver's first ``connect()`` — that runs inside
        the watcher and is persisted on success.
        """
        from core.registry.models import Device

        created: list[dict[str, Any]] = []
        skipped: list[str] = []

        for entry in body.devices:
            ip = (entry.ip or "").strip()
            mac = (entry.mac or "").strip()
            if not ip or not mac:
                skipped.append(mac or ip or "<empty>")
                continue
            display = (entry.name or "").strip() or f"AC {mac[-5:]}"
            location = (entry.location or "").strip() or None
            try:
                name_en = await translate_to_en(display)
            except Exception:
                name_en = display
            # Translate location → meta.location_en for voice patterns. The
            # display field (device.location) keeps the user's original
            # language so the UI shows what they typed.
            from system_modules.llm_engine.intent_router import _normalize_en
            loc_en: str | None = None
            if location:
                if location.isascii():
                    loc_en = _normalize_en(location)
                else:
                    try:
                        translated_loc = await translate_to_en(location)
                    except Exception:
                        translated_loc = ""
                    loc_en = _normalize_en(translated_loc or "") or None
            meta: dict[str, Any] = {
                "gree": {
                    "ip": ip,
                    "mac": mac,
                    "name": display,
                    "port": 7000,
                    "key": None,
                    "brand": "gree",
                },
                "name_en": _normalize_en(name_en or "") or None,
                "location_en": loc_en,
            }
            for k in ("name_en", "location_en"):
                if meta.get(k) is None:
                    meta.pop(k, None)

            async with svc._db_session() as session:
                async with session.begin():
                    device = Device(
                        name=display,
                        type="actuator",
                        protocol="gree",
                        entity_type="air_conditioner",
                        location=location,
                        module_id=svc.name,
                        enabled=True,
                    )
                    device.set_capabilities(AC_CAPABILITIES)
                    device.set_meta(meta)
                    session.add(device)
                    await session.flush()
                    device_id = device.device_id
            try:
                await on_entity_changed("device", device_id, "created")
            except Exception as exc:
                logger.warning("device-control: gree import pattern regen failed: %s", exc)
            await svc.add_device_watcher(device_id)
            await svc.publish("device.registered", {
                "device_id": device_id,
                "name": display,
                "entity_type": "air_conditioner",
                "location": location,
                "protocol": "gree",
                "capabilities": list(AC_CAPABILITIES),
            })
            created.append({
                "device_id": device_id,
                "name": display,
                "ip": ip,
                "mac": mac,
            })

        return {"status": "ok", "created": created, "skipped": skipped}

    # ── Hue discovery + import ────────────────────────────────────────────

    @router.post("/hue/discover")
    async def hue_discover(body: HueDiscoverBody) -> dict[str, Any]:
        """Query a Hue Bridge (or compatible emulator) for all lights."""
        api_host = body.api_host.rstrip("/")
        url = f"{api_host}/api/{body.token}/lights"
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                resp = await client.get(url)
                resp.raise_for_status()
                raw = resp.json()
            except Exception as exc:
                raise HTTPException(502, f"Hue API error: {exc}")
        devices: list[dict[str, Any]] = []
        for lid, info in raw.items():
            state = info.get("state", {})
            devices.append({
                "light_id": lid,
                "name": info.get("name", f"Light {lid}"),
                "type": info.get("type", ""),
                "model": info.get("modelid", ""),
                "manufacturer": info.get("manufacturername", ""),
                "on": state.get("on", False),
                "reachable": state.get("reachable", True),
            })
        return {"status": "ok", "devices": devices}

    @router.post("/hue/import")
    async def hue_import(body: HueImportBody) -> dict[str, Any]:
        """Bulk-create Device rows from a Hue discovery result."""
        from core.registry.models import Device
        from system_modules.llm_engine.intent_router import _normalize_en

        created: list[dict[str, Any]] = []
        for entry in body.devices:
            lid = entry.light_id
            display = (entry.name or "").strip() or f"Hue light {lid}"
            location = (entry.location or "").strip() or None
            try:
                name_en = await translate_to_en(display)
            except Exception:
                name_en = display
            loc_en: str | None = None
            if location:
                if location.isascii():
                    loc_en = _normalize_en(location)
                else:
                    try:
                        loc_en = _normalize_en(await translate_to_en(location)) or None
                    except Exception:
                        loc_en = None
            meta: dict[str, Any] = {
                "philips_hue": {
                    "api_host": body.api_host.rstrip("/"),
                    "token": body.token,
                    "light_id": int(lid) if str(lid).isdigit() else lid,
                },
                "name_en": _normalize_en(name_en or "") or None,
                "location_en": loc_en,
            }
            for k in ("name_en", "location_en"):
                if meta.get(k) is None:
                    meta.pop(k, None)

            entity_type = _hue_type_to_entity(entry.hue_type)
            async with svc._db_session() as session:
                async with session.begin():
                    device = Device(
                        name=display,
                        type="actuator",
                        protocol="philips_hue",
                        entity_type=entity_type,
                        location=location,
                        module_id=svc.name,
                        enabled=True,
                    )
                    device.set_capabilities(["on", "off", "brightness"])
                    device.set_meta(meta)
                    session.add(device)
                    await session.flush()
                    device_id = device.device_id
            try:
                await on_entity_changed("device", device_id, "created")
            except Exception as exc:
                logger.warning("device-control: hue import pattern regen failed: %s", exc)
            await svc.add_device_watcher(device_id)
            await svc.publish("device.registered", {
                "device_id": device_id,
                "name": display,
                "entity_type": entity_type,
                "location": location,
                "protocol": "philips_hue",
                "capabilities": ["on", "off", "brightness"],
            })
            created.append({"device_id": device_id, "name": display, "light_id": lid})

        return {"status": "ok", "created": created}

    # ── Z2M discovery + import ─────────────────────────────────────────────

    @router.post("/z2m/discover")
    async def z2m_discover(body: Z2mDiscoverBody) -> dict[str, Any]:
        """Connect to an MQTT broker and read zigbee2mqtt/bridge/devices."""
        import socket
        import struct

        host = body.mqtt_host
        port = body.mqtt_port
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            sock.connect((host, port))
            # MQTT CONNECT
            client_id = b"selena-z2m-discover"
            connect = bytearray(b"\x00\x04MQTT\x04\x02")
            connect += struct.pack(">H", 60)
            connect += struct.pack(">H", len(client_id)) + client_id
            pkt = b"\x10" + bytes([len(connect)]) + bytes(connect)
            sock.send(pkt)
            ack = sock.recv(4)
            if len(ack) < 4 or ack[3] != 0:
                raise Exception("MQTT CONNACK failed")
            # SUBSCRIBE to zigbee2mqtt/bridge/devices
            topic = b"zigbee2mqtt/bridge/devices"
            sub = struct.pack(">H", 1) + struct.pack(">H", len(topic)) + topic + b"\x00"
            pkt = b"\x82" + bytes([len(sub)]) + bytes(sub)
            sock.send(pkt)
            sock.recv(5)  # SUBACK
            # Read retained message — payload may span multiple TCP frames
            sock.settimeout(3)
            data = b""
            devices_raw: list[dict[str, Any]] = []
            for _ in range(30):
                try:
                    chunk = sock.recv(16384)
                    if not chunk:
                        break
                    data += chunk
                    idx = data.find(b"[")
                    if idx >= 0:
                        try:
                            devices_raw = json.loads(data[idx:])
                            break
                        except json.JSONDecodeError:
                            continue  # incomplete, keep reading
                except socket.timeout:
                    # Last attempt: try to parse what we have
                    idx = data.find(b"[")
                    if idx >= 0:
                        try:
                            devices_raw = json.loads(data[idx:])
                        except json.JSONDecodeError:
                            pass
                    break
            sock.close()
        except Exception as exc:
            raise HTTPException(502, f"MQTT error ({host}:{port}): {exc}")

        devices: list[dict[str, Any]] = []
        for d in devices_raw:
            fn = d.get("friendly_name", "")
            if fn.startswith("Coordinator") or d.get("type") == "Coordinator":
                continue
            exposes = d.get("definition", {}).get("exposes", [])
            expose_names = [e.get("name", "") for e in exposes]
            # Guess entity_type from exposes
            if "contact" in expose_names:
                etype = "sensor"
            elif "occupancy" in expose_names:
                etype = "sensor"
            elif "state" in expose_names or "brightness" in expose_names:
                etype = "light"
            else:
                etype = "switch"
            devices.append({
                "friendly_name": fn,
                "ieee_address": d.get("ieee_address", ""),
                "model": d.get("model_id", ""),
                "manufacturer": d.get("manufacturer", ""),
                "entity_type": etype,
                "exposes": expose_names,
            })
        return {"status": "ok", "devices": devices}

    @router.post("/z2m/import")
    async def z2m_import(body: Z2mImportBody) -> dict[str, Any]:
        """Bulk-create Device rows from a Z2M discovery result."""
        from core.registry.models import Device

        created: list[dict[str, Any]] = []
        for entry in body.devices:
            fname = (entry.friendly_name or "").strip()
            if not fname:
                continue
            display = (entry.name or "").strip() or fname
            etype = entry.entity_type or "sensor"
            location = (entry.location or "").strip() or None
            try:
                name_en = await translate_to_en(display)
            except Exception:
                name_en = display
            from system_modules.llm_engine.intent_router import _normalize_en
            loc_en: str | None = None
            if location:
                if location.isascii():
                    loc_en = _normalize_en(location)
                else:
                    try:
                        loc_en = _normalize_en(await translate_to_en(location)) or None
                    except Exception:
                        loc_en = None
            caps = ["read"] if etype == "sensor" else ["on", "off"]
            if etype == "light":
                caps.append("brightness")
            meta: dict[str, Any] = {
                "zigbee2mqtt": {
                    "friendly_name": fname,
                    "ieee_address": entry.ieee_address or None,
                    "base_topic": body.base_topic,
                },
                "name_en": _normalize_en(name_en or "") or None,
                "location_en": loc_en,
            }
            for k in ("name_en", "location_en"):
                if meta.get(k) is None:
                    meta.pop(k, None)

            async with svc._db_session() as session:
                async with session.begin():
                    device = Device(
                        name=display,
                        type="sensor" if etype == "sensor" else "actuator",
                        protocol="zigbee2mqtt",
                        entity_type=etype,
                        location=location,
                        module_id=svc.name,
                        enabled=True,
                    )
                    device.set_capabilities(caps)
                    device.set_meta(meta)
                    session.add(device)
                    await session.flush()
                    device_id = device.device_id
            try:
                await on_entity_changed("device", device_id, "created")
            except Exception as exc:
                logger.warning("device-control: z2m import pattern regen failed: %s", exc)
            await svc.add_device_watcher(device_id)
            await svc.publish("device.registered", {
                "device_id": device_id,
                "name": display,
                "entity_type": etype,
                "location": location,
                "protocol": "zigbee2mqtt",
                "capabilities": caps,
            })
            created.append({"device_id": device_id, "name": display, "friendly_name": fname})

        return {"status": "ok", "created": created}

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

    @router.post("/tuya/wizard/lan-rescan")
    async def wizard_lan_rescan() -> dict[str, Any]:
        """Re-scan the LAN and update IP/version for existing Tuya devices.

        Use this when an imported device shows as offline because the cloud
        gave us a wrong IP. We discover the real LAN IP via tinytuya
        broadcast and update meta.tuya.ip / meta.tuya.version in place.
        Devices that are now reachable are switched to tuya_local + enabled.
        Devices not found on the LAN are left untouched.
        """
        from core.registry.models import Device

        lan_map = await _scan_tuya_lan()
        logger.info(
            "tuya lan-rescan: found %d device(s) on LAN: %s",
            len(lan_map), list(lan_map.keys()),
        )

        updated: list[dict[str, Any]] = []
        async with svc._db_session() as session:
            res = await session.execute(
                select(Device).where(Device.module_id == svc.name)
            )
            devices = list(res.scalars())

        for d in devices:
            meta = json.loads(d.meta) if d.meta else {}
            tuya_meta = meta.get("tuya") or {}
            cloud_id = tuya_meta.get("cloud_device_id") or tuya_meta.get("device_id")
            if not cloud_id:
                continue
            lan = lan_map.get(cloud_id)
            if not lan:
                continue
            old_ip = tuya_meta.get("ip", "")
            old_version = tuya_meta.get("version", "")
            if lan["ip"] == old_ip and lan["version"] == old_version:
                continue
            tuya_meta["ip"] = lan["ip"]
            tuya_meta["version"] = lan["version"]
            meta["tuya"] = tuya_meta

            async with svc._db_session() as session:
                async with session.begin():
                    fresh = await session.get(Device, d.device_id)
                    if fresh is None:
                        continue
                    fresh.set_meta(meta)
                    # If device has a local_key and we now have a real LAN
                    # IP, promote to tuya_local + enabled.
                    if tuya_meta.get("local_key"):
                        fresh.protocol = "tuya_local"
                        fresh.enabled = True

            # Restart watcher to pick up new IP / promoted protocol.
            await svc.remove_device_watcher(d.device_id)
            await svc.add_device_watcher(d.device_id)
            updated.append({
                "device_id": d.device_id,
                "name": d.name,
                "old_ip": old_ip,
                "new_ip": lan["ip"],
                "version": lan["version"],
            })

        return {"status": "ok", "updated": updated, "lan_devices_found": len(lan_map)}

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

        # Run a single LAN broadcast scan to discover real LAN IPs +
        # protocol versions. The cloud's ``ip`` field is unreliable — Tuya
        # often returns the router's WAN IP (e.g. 209.x.x.x) which tinytuya
        # cannot reach. We trust the LAN scan over the cloud whenever the
        # device responds to broadcasts.
        lan_map = await _scan_tuya_lan()
        logger.info(
            "tuya import: LAN scan discovered %d device(s): %s",
            len(lan_map), list(lan_map.keys()),
        )

        created: list[dict[str, Any]] = []
        skipped: list[str] = []

        for cid in body.selected_ids:
            cd = cloud_by_id.get(cid)
            if cd is None:
                skipped.append(cid)
                continue
            # Prefer LAN scan results over cloud-reported ip/version.
            lan = lan_map.get(cid)
            if lan:
                effective_ip = lan["ip"]
                effective_version = lan["version"]
            else:
                cloud_ip = cd.get("ip", "") or ""
                # Fall back to cloud-reported IP only if it's a private LAN
                # address. Public IPs are router WAN addresses — useless.
                effective_ip = cloud_ip if _is_private_ip(cloud_ip) else ""
                effective_version = str(cd.get("version") or "3.3")

            # tuya_local requires real LAN IP + local_key. Without either,
            # save as inactive cloud-only entry.
            has_local = bool(effective_ip) and bool(cd.get("local_key"))
            protocol = "tuya_local" if has_local else "tuya_cloud"
            enabled = has_local
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
            from system_modules.llm_engine.intent_router import _normalize_en
            meta: dict[str, Any] = {
                "tuya": {
                    "device_id": cd["id"],
                    "cloud_device_id": cd["id"],
                    "local_key": cd.get("local_key", ""),
                    "ip": effective_ip,
                    "version": effective_version,
                    "dps_map": {"on": "1"},        # local LAN DPS index (default)
                    "code_map": {"on": on_code},   # cloud status code (auto-detected)
                    "category": cd.get("category", ""),
                    "product_name": cd.get("product_name", ""),
                },
                "name_en": _normalize_en(name_en or "") or None,
            }
            # Drop None so we don't store 'null' in JSON.
            if meta["name_en"] is None:
                meta.pop("name_en", None)
            # Classify entity_type from Tuya category + product name so the
            # device automatically lands in the right consumer module
            # (lights-switches for light/switch/outlet, climate for AC).
            entity_type, capabilities = _classify_tuya_entity_type(cd)
            async with svc._db_session() as session:
                async with session.begin():
                    device = Device(
                        name=cd.get("name") or cd["id"],
                        type="actuator",
                        protocol=protocol,
                        entity_type=entity_type,
                        module_id=svc.name,
                        enabled=enabled,
                    )
                    device.set_capabilities(capabilities)
                    device.set_meta(meta)
                    session.add(device)
                    await session.flush()
                    device_id = device.device_id
            try:
                await on_entity_changed("device", device_id, "created")
            except Exception:
                pass
            # add_device_watcher itself skips disabled devices.
            await svc.add_device_watcher(device_id)
            await svc.publish("device.registered", {
                "device_id": device_id,
                "name": cd.get("name") or cd["id"],
                "entity_type": entity_type,
                "location": None,
                "protocol": protocol,
                "capabilities": capabilities,
            })
            created.append({
                "device_id": device_id,
                "name": cd.get("name", cid),
                "protocol": protocol,
                "entity_type": entity_type,
                "enabled": enabled,
            })

        return {"status": "ok", "created": created, "skipped": skipped}

    # ── Matter / Thread commissioning ───────────────────────────────────

    @router.post("/matter/commission")
    async def matter_commission(body: dict[str, Any]) -> dict[str, Any]:
        """Pair a Matter device via QR / manual setup code.

        Body: ``{"setup_code": "MT:...", "device_name": "Front Bulb",
                  "entity_type": "light"}``.
        Talks to the matter-server sidecar (``--profile matter``) over its
        WebSocket. On success registers a new Device row with
        ``protocol="matter"`` and triggers pattern regeneration so voice
        commands work immediately.
        """
        from core.registry.models import Device
        from system_modules.device_control.drivers.matter import _HOLDER

        setup_code = (body.get("setup_code") or "").strip()
        device_name = (body.get("device_name") or "").strip()
        entity_type = (body.get("entity_type") or "light").strip()
        if not setup_code or not device_name:
            raise HTTPException(422, "setup_code and device_name are required")

        try:
            node_id = await asyncio.wait_for(
                _HOLDER.commission_with_code(setup_code), timeout=60.0,
            )
        except DriverError as exc:
            raise HTTPException(400, str(exc))
        except asyncio.TimeoutError:
            raise HTTPException(504, "matter commission timed out after 60s")
        except Exception as exc:
            raise HTTPException(400, f"matter commission failed: {exc}")

        async with svc._db_session() as session:
            async with session.begin():
                device = Device(
                    name=device_name,
                    type="actuator",
                    protocol="matter",
                    entity_type=entity_type,
                    module_id=svc.name,
                    enabled=True,
                )
                device.set_capabilities([])
                device.set_meta({"matter": {"node_id": node_id, "endpoint": 1}})
                session.add(device)
                await session.flush()
                device_id = device.device_id

        # Pattern regeneration is fire-and-forget — it can take 10+ seconds
        # if it falls back to the LLM, and we don't want the user staring at
        # the "Pair device" spinner for the duration. Errors land in the log.
        # _spawn_bg keeps a strong ref so the GC can't reap the task early.
        async def _regen_in_background() -> None:
            try:
                await on_entity_changed("device", device_id, "created")
            except Exception as exc:
                logger.warning(
                    "device-control: pattern regen failed for %s: %s",
                    device_id, exc,
                )
        _spawn_bg(_regen_in_background(), name=f"matter_pattern_regen:{device_id}")

        await svc.add_device_watcher(device_id)
        await svc.publish("device.registered", {
            "device_id": device_id,
            "name": device_name,
            "entity_type": entity_type,
            "location": None,
            "protocol": "matter",
            "capabilities": [],
        })
        return {"device_id": device_id, "node_id": node_id}

    @router.post("/matter/remove/{node_id}")
    async def matter_remove(node_id: int) -> dict[str, Any]:
        """Decommission a Matter node and delete the matching Device row."""
        from core.registry.models import Device
        from system_modules.device_control.drivers.matter import _HOLDER

        try:
            await _HOLDER.remove_node(int(node_id))
        except DriverError as exc:
            raise HTTPException(400, str(exc))
        except Exception as exc:
            raise HTTPException(502, f"matter remove failed: {exc}")

        # Find any Device row bound to this node and remove it.
        deleted: list[str] = []
        async with svc._db_session() as session:
            async with session.begin():
                res = await session.execute(
                    select(Device).where(Device.protocol == "matter")
                )
                for d in res.scalars():
                    meta = json.loads(d.meta) if d.meta else {}
                    if int(meta.get("matter", {}).get("node_id", -1)) == int(node_id):
                        deleted.append(d.device_id)
                        await session.delete(d)
        for did in deleted:
            await svc.remove_device_watcher(did)
            await svc.publish("device.removed", {"device_id": did})
        return {"removed_node_id": int(node_id), "deleted_devices": deleted}

    return router
