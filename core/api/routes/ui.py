"""
core/api/routes/ui.py — UI-специфичные API эндпоинты для фронтенда.

Эти маршруты НЕ требуют module_token авторизации — UI обслуживается
на localhost и защищён iptables на уровне сети.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from core.config import get_settings, get_yaml_config
from core.config_writer import read_config, update_config, update_section, write_config
from core.registry.models import Device
from core.registry.service import DeviceNotFoundError, DeviceRegistry

logger = logging.getLogger(__name__)
router = APIRouter(tags=["ui"])


# ---------- Pydantic schemas ----------

class WizardStepRequest(BaseModel):
    step: str
    data: dict[str, Any]


class StateUpdateRequest(BaseModel):
    state: dict[str, Any]


# ---------- helpers ----------

def _get_db_session(request: Request):
    """Get async DB session factory from app state."""
    return request.app.state.db_session_factory


def _device_to_dict(device: Device) -> dict[str, Any]:
    return {
        "device_id": device.device_id,
        "name": device.name,
        "type": device.type,
        "protocol": device.protocol,
        "state": device.get_state(),
        "capabilities": device.get_capabilities(),
        "last_seen": device.last_seen.timestamp() if device.last_seen else None,
        "module_id": device.module_id,
        "meta": device.get_meta(),
    }


def _read_hw_metrics() -> dict[str, Any]:
    """Read hardware metrics using hw_monitor (best-effort)."""
    try:
        from system_modules.hw_monitor.monitor import collect_metrics
        m = collect_metrics()
        disk_total_gb = 0.0
        disk_used_gb = 0.0
        try:
            usage = shutil.disk_usage("/")
            disk_total_gb = usage.total / 1e9
            disk_used_gb = usage.used / 1e9
        except Exception:
            pass
        return {
            "cpu_temp": m.cpu_temp_c or 0,
            "ram_used_mb": round(m.ram_used_mb),
            "ram_total_mb": round(m.ram_total_mb),
            "disk_used_gb": round(disk_used_gb, 1),
            "disk_total_gb": round(disk_total_gb, 1),
        }
    except Exception as exc:
        logger.debug("hw_monitor unavailable: %s", exc)
        # Fallback: basic info via shutil
        disk_total_gb = 0.0
        disk_used_gb = 0.0
        ram_total_mb = 0
        ram_used_mb = 0
        try:
            usage = shutil.disk_usage("/")
            disk_total_gb = usage.total / 1e9
            disk_used_gb = usage.used / 1e9
        except Exception:
            pass
        try:
            import psutil
            vm = psutil.virtual_memory()
            ram_total_mb = vm.total // (1024 * 1024)
            ram_used_mb = vm.used // (1024 * 1024)
        except Exception:
            pass
        return {
            "cpu_temp": 0,
            "ram_used_mb": ram_used_mb,
            "ram_total_mb": ram_total_mb,
            "disk_used_gb": round(disk_used_gb, 1),
            "disk_total_gb": round(disk_total_gb, 1),
        }


# ---------- System ----------

@router.get("/system")
async def ui_system() -> dict[str, Any]:
    """Combined system endpoint: core health + hardware metrics."""
    from core.api.routes.system import _start_time as core_start_time
    from core.api.routes.system import get_system_mode
    mode = get_system_mode()

    hw = _read_hw_metrics()

    return {
        "core": {
            "status": "ok",
            "version": "0.3.0-beta",
            "mode": mode,
            "uptime": int(time.time() - core_start_time),
            "integrity": "ok",
        },
        "hardware": hw,
    }


# ---------- Devices ----------

@router.get("/devices")
async def ui_list_devices(request: Request) -> dict[str, Any]:
    session_factory = _get_db_session(request)
    async with session_factory() as session:
        registry = DeviceRegistry(session)
        devices = await registry.get_all()
        return {"devices": [_device_to_dict(d) for d in devices]}


@router.patch("/devices/{device_id}/state")
async def ui_update_device_state(
    device_id: str,
    body: StateUpdateRequest,
    request: Request,
) -> dict[str, Any]:
    session_factory = _get_db_session(request)
    async with session_factory() as session:
        registry = DeviceRegistry(session)
        try:
            device = await registry.update_state(device_id, body.state)
            await session.commit()

            # Publish event to bus
            try:
                from core.eventbus.bus import get_event_bus
                bus = get_event_bus()
                await bus.publish(
                    type="device.state_changed",
                    source="ui",
                    payload={
                        "device_id": device_id,
                        "new_state": body.state,
                    },
                )
            except Exception as exc:
                logger.warning("Event publish failed: %s", exc)

            return _device_to_dict(device)
        except DeviceNotFoundError:
            raise HTTPException(status_code=404, detail="Device not found")


# ---------- Modules ----------

@router.get("/modules")
async def ui_list_modules() -> dict[str, Any]:
    try:
        from core.module_loader.loader import get_plugin_manager
        manager = get_plugin_manager()
        modules_list = manager.list_modules()
        return {
            "modules": [
                {
                    "name": m.name,
                    "version": m.version,
                    "type": m.type,
                    "status": m.status.value,
                    "runtime_mode": m.runtime_mode,
                    "port": m.port,
                    "installed_at": m.installed_at,
                }
                for m in modules_list
            ]
        }
    except Exception as exc:
        logger.warning("Module loader unavailable: %s", exc)
        return {"modules": []}


@router.post("/modules/{name}/stop")
async def ui_stop_module(name: str) -> dict[str, Any]:
    from core.module_loader.loader import get_plugin_manager
    manager = get_plugin_manager()
    module = manager.get_module(name)
    if module is None:
        raise HTTPException(status_code=404, detail="Module not found")
    if module.type == "SYSTEM":
        raise HTTPException(status_code=403, detail="Cannot stop SYSTEM modules")
    info = await manager.stop(name)
    return {"name": info.name, "status": info.status.value}


@router.post("/modules/{name}/start")
async def ui_start_module(name: str) -> dict[str, Any]:
    from core.module_loader.loader import get_plugin_manager
    manager = get_plugin_manager()
    module = manager.get_module(name)
    if module is None:
        raise HTTPException(status_code=404, detail="Module not found")
    info = await manager.start(name)
    return {"name": info.name, "status": info.status.value}


@router.delete("/modules/{name}")
async def ui_remove_module(name: str) -> None:
    from core.module_loader.loader import get_plugin_manager
    manager = get_plugin_manager()
    module = manager.get_module(name)
    if module is None:
        raise HTTPException(status_code=404, detail="Module not found")
    if module.type == "SYSTEM":
        raise HTTPException(status_code=403, detail="Cannot remove SYSTEM modules")
    await manager.remove(name)


# ---------- Wizard ----------

# In-memory wizard state (persisted to core.yaml on completion)
_wizard_state: dict[str, Any] = {
    "completed": False,
    "steps": {},
}


def _init_wizard_state() -> None:
    """Load wizard state from core.yaml if available."""
    yaml_cfg = get_yaml_config()
    wizard_cfg = yaml_cfg.get("wizard", {})
    _wizard_state["completed"] = wizard_cfg.get("completed", False)
    _wizard_state["steps"] = wizard_cfg.get("steps", {})


# Define wizard steps with their required/optional status
WIZARD_STEPS = {
    "language": {"required": True, "label": "Language"},
    "wifi": {"required": False, "label": "Wi-Fi"},
    "device_name": {"required": True, "label": "Device Name"},
    "timezone": {"required": True, "label": "Timezone"},
    "stt_model": {"required": False, "label": "STT Model"},
    "tts_voice": {"required": False, "label": "TTS Voice"},
    "admin_user": {"required": True, "label": "Admin User"},
    "platform": {"required": False, "label": "Platform"},
    "import": {"required": False, "label": "Import"},
}


@router.get("/wizard/status")
async def ui_wizard_status() -> dict[str, Any]:
    yaml_cfg = get_yaml_config()
    wizard_cfg = yaml_cfg.get("wizard", {})
    completed = wizard_cfg.get("completed", False) or _wizard_state["completed"]
    return {"completed": completed}


@router.get("/wizard/requirements")
async def ui_wizard_requirements() -> dict[str, Any]:
    yaml_cfg = get_yaml_config()
    wizard_cfg = yaml_cfg.get("wizard", {})
    completed = wizard_cfg.get("completed", False) or _wizard_state["completed"]
    done_steps = _wizard_state.get("steps", {})

    steps: dict[str, Any] = {}
    for step_name, step_def in WIZARD_STEPS.items():
        steps[step_name] = {
            "required": step_def["required"],
            "done": step_name in done_steps,
            "label": step_def["label"],
        }

    # can_proceed if all required steps are done
    all_required_done = all(
        step_name in done_steps
        for step_name, step_def in WIZARD_STEPS.items()
        if step_def["required"]
    )
    can_proceed = completed or all_required_done

    return {
        "can_proceed": can_proceed,
        "wizard_completed": completed,
        "steps": steps,
    }


@router.post("/wizard/step")
async def ui_wizard_step(body: WizardStepRequest) -> dict[str, Any]:
    step_name = body.step
    if step_name not in WIZARD_STEPS:
        raise HTTPException(status_code=400, detail=f"Unknown wizard step: {step_name}")

    # Mark step as done
    _wizard_state["steps"][step_name] = body.data
    logger.info("Wizard step completed: %s", step_name)

    # --- Persist step data to core.yaml ---
    await _apply_wizard_step(step_name, body.data)

    # Determine next step
    step_names = list(WIZARD_STEPS.keys())
    current_idx = step_names.index(step_name)
    next_step = step_names[current_idx + 1] if current_idx + 1 < len(step_names) else None

    # If all steps done, mark wizard as completed
    all_required_done = all(
        sn in _wizard_state["steps"]
        for sn, sd in WIZARD_STEPS.items()
        if sd["required"]
    )

    if next_step is None or all_required_done:
        _wizard_state["completed"] = True
        # Persist to core.yaml
        _persist_wizard_completed()

    return {
        "step": step_name,
        "status": "ok",
        "next_step": next_step,
    }


def _persist_wizard_completed() -> None:
    """Write wizard_completed=true to core.yaml."""
    try:
        config = read_config()
        config.setdefault("wizard", {})["completed"] = True
        write_config(config)
        logger.info("Wizard completed, persisted to config")
    except Exception as exc:
        logger.error("Failed to persist wizard state: %s", exc)


async def _apply_wizard_step(step: str, data: dict[str, Any]) -> None:
    """Apply wizard step data to real system config."""
    try:
        if step == "language" and data.get("language"):
            update_config("system", "language", data["language"])

        elif step == "device_name" and data.get("name"):
            update_config("system", "device_name", data["name"])

        elif step == "timezone" and data.get("timezone"):
            update_config("system", "timezone", data["timezone"])
            # Try to apply system timezone
            try:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(
                    None,
                    lambda: subprocess.run(
                        ["timedatectl", "set-timezone", data["timezone"]],
                        capture_output=True, timeout=10,
                    )
                )
            except Exception:
                pass

        elif step == "stt_model" and data.get("model"):
            update_config("voice", "stt_model", data["model"])
            os.environ["WHISPER_MODEL"] = data["model"]

        elif step == "tts_voice" and data.get("voice"):
            update_config("voice", "tts_voice", data["voice"])
            os.environ["PIPER_VOICE"] = data["voice"]

        elif step == "admin_user" and data.get("username"):
            update_section("admin", {
                "username": data["username"],
                # PIN is not stored in plaintext in yaml; just mark admin created
                "created": True,
            })

        elif step == "wifi" and data.get("ssid"):
            update_config("system", "wifi_ssid", data["ssid"])
            # Attempt real WiFi connection via nmcli
            try:
                ssid = data["ssid"]
                password = data.get("password", "")
                cmd = ["nmcli", "dev", "wifi", "connect", ssid]
                if password:
                    cmd += ["password", password]
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(
                    None,
                    lambda: subprocess.run(cmd, capture_output=True, timeout=30)
                )
            except Exception as exc:
                logger.debug("WiFi connect via nmcli skipped: %s", exc)

        elif step == "platform" and data.get("device_hash"):
            update_config("platform", "device_hash", data["device_hash"])

    except Exception as exc:
        logger.warning("Failed to apply wizard step '%s': %s", step, exc)


# ---------- Setup QR ----------

@router.get("/setup/qr")
async def ui_setup_qr() -> dict[str, Any]:
    """Generate QR data for initial device setup (AP mode)."""
    settings = get_settings()
    return {
        "qr_data": f"http://192.168.4.1:{settings.ui_port}",
        "ssid": "Selena-Setup",
    }
