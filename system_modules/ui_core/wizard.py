"""
system_modules/ui_core/wizard.py — Onboarding wizard endpoints (9 steps)

Steps: wifi → language → device_name → timezone → stt_model →
       tts_voice → admin_user → platform → import
"""
from __future__ import annotations

import json
import logging
import socket
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core.i18n import t

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/ui/wizard", tags=["wizard"])

WIZARD_STATE_FILE = Path("/var/lib/selena/wizard_state.json")

STEPS = [
    "wifi",
    "language",
    "device_name",
    "timezone",
    "stt_model",
    "tts_voice",
    "admin_user",
    "home_devices",
    "platform",
    "import",
]

NEXT_STEP: dict[str, str | None] = {
    step: STEPS[i + 1] if i + 1 < len(STEPS) else None
    for i, step in enumerate(STEPS)
}


def _load_state() -> dict[str, Any]:
    if WIZARD_STATE_FILE.exists():
        try:
            return json.loads(WIZARD_STATE_FILE.read_text())
        except Exception:
            pass
    return {"completed": False, "current_step": "wifi", "data": {}}


def _save_state(state: dict) -> None:
    WIZARD_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    WIZARD_STATE_FILE.write_text(json.dumps(state, indent=2))


class WizardStepRequest(BaseModel):
    step: str
    data: dict[str, Any]


@router.get("/status")
async def wizard_status() -> dict:
    state = _load_state()
    return {
        "completed": state.get("completed", False),
        "current_step": state.get("current_step", "wifi"),
        "steps": STEPS,
        "progress": {
            "total": len(STEPS),
            "done": STEPS.index(state.get("current_step", "wifi")),
        },
    }


@router.post("/step")
async def wizard_step(req: WizardStepRequest) -> dict:
    """Process a wizard step and advance to the next."""
    if req.step not in STEPS:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown step '{req.step}'. Valid steps: {STEPS}",
        )

    state = _load_state()
    if state.get("completed"):
        raise HTTPException(status_code=409, detail="Wizard already completed")

    result = await _process_step(req.step, req.data, state)

    # Persist step data
    state.setdefault("data", {})[req.step] = req.data

    # Allow step handlers to write to root-level state
    if "_state_updates" in result:
        state.update(result.pop("_state_updates"))
    next_step = NEXT_STEP[req.step]
    if next_step is None:
        state["completed"] = True
        state["current_step"] = req.step
        _save_state(state)
        return {
            "step": req.step,
            "status": "ok",
            "next_step": None,
            "message": t("wizard.onboarding_complete"),
            **result,
        }

    state["current_step"] = next_step
    _save_state(state)
    return {
        "step": req.step,
        "status": "ok",
        "next_step": next_step,
        **result,
    }


@router.post("/reset")
async def wizard_reset() -> dict:
    """Reset wizard state (admin use only)."""
    if WIZARD_STATE_FILE.exists():
        WIZARD_STATE_FILE.unlink()
    return {"status": "reset", "message": "Wizard state cleared"}


@router.get("/requirements")
async def wizard_requirements() -> dict:
    """Return per-step completion status and whether setup can be skipped."""
    state = _load_state()
    step_data = state.get("data", {})

    def _internet_ok() -> bool:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(2)
            s.connect(("8.8.8.8", 53))
            s.close()
            return True
        except Exception:
            return False

    admin_d = step_data.get("admin_user", {})
    admin_done = bool(admin_d.get("username") and admin_d.get("pin"))

    device_d = step_data.get("device_name", {})
    device_done = bool(device_d.get("name"))

    platform_d = step_data.get("platform", {})
    platform_done = bool(platform_d.get("device_hash"))

    steps = {
        "internet": {
            "required": False,
            "done": _internet_ok(),
            "label": t("wizard.req_internet"),
        },
        "admin_user": {
            "required": True,
            "done": admin_done,
            "label": t("wizard.req_admin"),
        },
        "device_name": {
            "required": False,
            "done": device_done,
            "label": t("wizard.req_device_name"),
        },
        "platform": {
            "required": False,
            "done": platform_done,
            "label": t("wizard.req_platform"),
        },
    }

    can_proceed = all(v["done"] for v in steps.values() if v["required"])

    return {
        "can_proceed": can_proceed,
        "wizard_completed": state.get("completed", False),
        "steps": steps,
    }


# ------------------------------------------------------------------ #
# Step processors                                                       #
# ------------------------------------------------------------------ #

async def _process_step(step: str, data: dict, state: dict) -> dict:
    handlers = {
        "wifi": _step_wifi,
        "language": _step_language,
        "device_name": _step_device_name,
        "timezone": _step_timezone,
        "stt_model": _step_stt_model,
        "tts_voice": _step_tts_voice,
        "admin_user": _step_admin_user,
        "home_devices": _step_home_devices,
        "platform": _step_platform,
        "import": _step_import,
    }
    handler = handlers.get(step)
    if handler:
        return await handler(data, state)
    return {}


async def _step_wifi(data: dict, state: dict) -> dict:
    ssid = data.get("ssid", "")
    if not ssid:
        raise HTTPException(status_code=422, detail="'ssid' is required")
    # In production this calls nmcli to connect
    logger.info("Wizard wifi: ssid=%s", ssid)
    import socket
    try:
        local_ip = socket.gethostbyname(socket.gethostname())
    except Exception:
        local_ip = "192.168.1.x"
    return {"message": t("wizard.connected", ssid=ssid, ip=local_ip)}


async def _step_language(data: dict, state: dict) -> dict:
    lang = data.get("language", "en")
    logger.info("Wizard language: %s", lang)
    return {"message": t("wizard.language_set", lang=lang)}


async def _step_device_name(data: dict, state: dict) -> dict:
    name = data.get("name", "")
    if not name:
        raise HTTPException(status_code=422, detail="'name' is required")
    logger.info("Wizard device_name: %s", name)
    return {"message": t("wizard.device_name_set", name=name)}


async def _step_timezone(data: dict, state: dict) -> dict:
    tz = data.get("timezone", "Europe/Moscow")
    logger.info("Wizard timezone: %s", tz)
    return {"message": t("wizard.timezone_set", tz=tz)}


async def _step_stt_model(data: dict, state: dict) -> dict:
    model = data.get("model", "base")
    logger.info("Wizard stt_model: %s", model)
    return {"message": t("wizard.stt_model_set", model=model)}


async def _step_tts_voice(data: dict, state: dict) -> dict:
    voice = data.get("voice", "ru_RU-irina-medium")
    logger.info("Wizard tts_voice: %s", voice)
    return {"message": t("wizard.tts_voice_set", voice=voice)}


async def _step_admin_user(data: dict, state: dict) -> dict:
    username = data.get("username", "")
    if not username:
        raise HTTPException(status_code=422, detail="'username' is required")
    pin = data.get("pin", "")
    if not pin:
        raise HTTPException(status_code=422, detail="'pin' is required")

    # Create (or look up) the first user — automatically becomes owner
    try:
        from system_modules.user_manager.profiles import UserManager, UserAlreadyExistsError
        um = UserManager()
        existing = await um.get_by_username(username)
        if existing:
            user_id = existing.user_id
            logger.info("Wizard admin_user: user already exists, using existing id=%s", user_id)
        else:
            display_name = data.get("display_name") or username.capitalize()
            profile = await um.create(
                username=username,
                display_name=display_name,
                pin=pin,
                role="owner",  # first user is always owner
            )
            user_id = profile.user_id
            logger.info("Wizard admin_user: created owner user=%s id=%s", username, user_id)
    except Exception as exc:
        logger.exception("Wizard admin_user: failed to create user: %s", exc)
        raise HTTPException(status_code=500, detail=f"Failed to create user: {exc}") from exc

    return {
        "message": t("wizard.owner_created", username=username),
        "_state_updates": {"owner_user_id": user_id},
    }


async def _step_home_devices(data: dict, state: dict) -> dict:
    """Register the kiosk screen as the owner's trusted device.

    Returns a ``device_token`` the frontend stores in localStorage so the
    physical screen is automatically recognised on every subsequent load.
    Also returns QR registration info for mobile devices.
    """
    owner_user_id = state.get("owner_user_id")
    if not owner_user_id:
        # Wizard skipped admin_user — skip silently
        logger.warning("Wizard home_devices: owner_user_id not set — skipping auto-registration")
        return {"message": t("wizard.skip_registration")}

    device_name = data.get("device_name", "Kiosk Screen")

    try:
        import os as _os
        from sqlalchemy.ext.asyncio import create_async_engine as _cae
        from system_modules.user_manager.devices import DeviceManager

        _db_url = _os.environ.get("SELENA_DB_URL", "sqlite+aiosqlite:///var/lib/selena/selena.db")
        _engine = _cae(_db_url, echo=False)
        dm = DeviceManager(_engine)
        await dm.ensure_tables()
        plain_token = await dm.register(
            user_id=owner_user_id,
            device_name=device_name,
            user_agent="SelenaCore Kiosk",
        )
        logger.info("Wizard home_devices: kiosk registered for owner id=%s", owner_user_id)
    except Exception as exc:
        logger.exception("Wizard home_devices: device registration failed: %s", exc)
        # Non-fatal — wizard can continue
        return {"message": "Device registration skipped due to error"}

    return {
        "message": t("wizard.device_registered", name=device_name),
        "device_token": plain_token,
        "owner_user_id": owner_user_id,
    }


async def _step_platform(data: dict, state: dict) -> dict:
    device_hash = data.get("device_hash", "")
    platform_url = data.get("platform_url", "")
    if device_hash:
        # Save to .env
        env_path = Path("/opt/selena-core/.env")
        try:
            existing = env_path.read_text() if env_path.exists() else ""
            lines = [l for l in existing.splitlines() if not l.startswith("PLATFORM_DEVICE_HASH=")]
            lines.append(f"PLATFORM_DEVICE_HASH={device_hash}")
            if platform_url:
                lines = [l for l in lines if not l.startswith("PLATFORM_API_URL=")]
                lines.append(f"PLATFORM_API_URL={platform_url}")
            env_path.write_text("\n".join(lines) + "\n")
        except Exception as e:
            logger.warning("Could not save platform config: %s", e)
    return {"message": t("wizard.platform_saved")}


async def _step_import(data: dict, state: dict) -> dict:
    source = data.get("source", "manual")
    logger.info("Wizard import: source=%s", source)
    return {"message": t("wizard.import_queued", source=source)}
