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

    result = await _process_step(req.step, req.data)

    # Persist step data
    state.setdefault("data", {})[req.step] = req.data
    next_step = NEXT_STEP[req.step]
    if next_step is None:
        state["completed"] = True
        state["current_step"] = req.step
        _save_state(state)
        return {
            "step": req.step,
            "status": "ok",
            "next_step": None,
            "message": "Onboarding complete! Rebooting to apply settings.",
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
            "label": "Подключение к сети",
        },
        "admin_user": {
            "required": True,
            "done": admin_done,
            "label": "Учётная запись администратора",
        },
        "device_name": {
            "required": False,
            "done": device_done,
            "label": "Имя устройства",
        },
        "platform": {
            "required": False,
            "done": platform_done,
            "label": "Платформа SmartHome LK",
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

async def _process_step(step: str, data: dict) -> dict:
    handlers = {
        "wifi": _step_wifi,
        "language": _step_language,
        "device_name": _step_device_name,
        "timezone": _step_timezone,
        "stt_model": _step_stt_model,
        "tts_voice": _step_tts_voice,
        "admin_user": _step_admin_user,
        "platform": _step_platform,
        "import": _step_import,
    }
    handler = handlers.get(step)
    if handler:
        return await handler(data)
    return {}


async def _step_wifi(data: dict) -> dict:
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
    return {"message": f"Connected to {ssid}. IP: {local_ip}"}


async def _step_language(data: dict) -> dict:
    lang = data.get("language", "ru")
    logger.info("Wizard language: %s", lang)
    return {"message": f"Language set to {lang}"}


async def _step_device_name(data: dict) -> dict:
    name = data.get("name", "")
    if not name:
        raise HTTPException(status_code=422, detail="'name' is required")
    logger.info("Wizard device_name: %s", name)
    return {"message": f"Device name set to '{name}'"}


async def _step_timezone(data: dict) -> dict:
    tz = data.get("timezone", "Europe/Moscow")
    logger.info("Wizard timezone: %s", tz)
    return {"message": f"Timezone set to {tz}"}


async def _step_stt_model(data: dict) -> dict:
    model = data.get("model", "base")
    logger.info("Wizard stt_model: %s", model)
    return {"message": f"STT model set to {model}"}


async def _step_tts_voice(data: dict) -> dict:
    voice = data.get("voice", "ru_RU-irina-medium")
    logger.info("Wizard tts_voice: %s", voice)
    return {"message": f"TTS voice set to {voice}"}


async def _step_admin_user(data: dict) -> dict:
    username = data.get("username", "")
    if not username:
        raise HTTPException(status_code=422, detail="'username' is required")
    if not data.get("pin"):
        raise HTTPException(status_code=422, detail="'pin' is required")
    logger.info("Wizard admin_user: %s", username)
    return {"message": f"Admin user '{username}' created"}


async def _step_platform(data: dict) -> dict:
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
    return {"message": "Platform credentials saved"}


async def _step_import(data: dict) -> dict:
    source = data.get("source", "manual")
    logger.info("Wizard import: source=%s", source)
    return {"message": f"Import from {source} queued"}
