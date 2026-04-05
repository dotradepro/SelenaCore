"""
core/api/routes/ui.py — UI-specific API endpoints for the frontend.

These routes do NOT require module_token authorization — the UI is served
on localhost and protected by iptables at the network level.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import time
import weakref
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from pydantic import BaseModel

from core.config import get_settings, get_yaml_config
from core.config_writer import read_config, update_config, update_section, write_config
from core.registry.models import Device
from core.registry.service import DeviceNotFoundError, DeviceRegistry

logger = logging.getLogger(__name__)
router = APIRouter(tags=["ui"])

# ── SSE broadcast: set of per-client asyncio.Queue ──────────────────────────
_sse_clients: weakref.WeakSet[asyncio.Queue] = weakref.WeakSet()

def _broadcast(event: dict[str, Any]) -> None:
    """Push event to all connected SSE clients (fire-and-forget)."""
    data = json.dumps(event)
    for q in list(_sse_clients):
        try:
            q.put_nowait(data)
        except asyncio.QueueFull:
            pass

# Public helper so other routes can broadcast (e.g. after module start/stop)
def broadcast_event(event_type: str, payload: dict[str, Any] | None = None) -> None:
    _broadcast({"type": event_type, "payload": payload or {}})

# ── Layout persistence ───────────────────────────────────────────────────────
_LAYOUT_PATH = Path(os.environ.get("CORE_DATA_DIR", "/var/lib/selena")) / "widget_layout.json"

def _load_layout() -> dict[str, Any]:
    try:
        if _LAYOUT_PATH.exists():
            return json.loads(_LAYOUT_PATH.read_text())
    except Exception:
        pass
    return {"pinned": [], "sizes": {}}

def _save_layout(layout: dict[str, Any]) -> None:
    try:
        _LAYOUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        _LAYOUT_PATH.write_text(json.dumps(layout))
    except Exception as e:
        logger.warning("Failed to save widget layout: %s", e)


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
    cpu_load: list[float] = [0.0, 0.0, 0.0]
    swap_used_mb = 0
    swap_total_mb = 0
    try:
        load1, load5, load15 = os.getloadavg()
        cpu_load = [round(load1, 2), round(load5, 2), round(load15, 2)]
    except OSError:
        pass
    try:
        for line in open("/proc/meminfo"):
            parts = line.split()
            if parts[0] == "SwapTotal:":
                swap_total_mb = int(parts[1]) // 1024
            elif parts[0] == "SwapFree:":
                swap_used_mb = swap_total_mb - int(parts[1]) // 1024
    except Exception:
        pass

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
        cpu_count = os.cpu_count() or 1
        return {
            "cpu_temp": m.cpu_temp_c or 0,
            "cpu_load": cpu_load,
            "cpu_count": cpu_count,
            "ram_used_mb": round(m.ram_used_mb),
            "ram_total_mb": round(m.ram_total_mb),
            "swap_used_mb": swap_used_mb,
            "swap_total_mb": swap_total_mb,
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
        cpu_count = os.cpu_count() or 1
        return {
            "cpu_temp": 0,
            "cpu_load": cpu_load,
            "cpu_count": cpu_count,
            "ram_used_mb": ram_used_mb,
            "ram_total_mb": ram_total_mb,
            "swap_used_mb": swap_used_mb,
            "swap_total_mb": swap_total_mb,
            "disk_used_gb": round(disk_used_gb, 1),
            "disk_total_gb": round(disk_total_gb, 1),
        }


def _read_ollama_status() -> dict[str, Any]:
    """Read Ollama/LLM status (best-effort)."""
    result: dict[str, Any] = {
        "installed": False,
        "running": False,
        "model": None,
        "model_loaded": False,
        "url": os.environ.get("OLLAMA_URL", "http://localhost:11434"),
    }
    try:
        result["installed"] = shutil.which("ollama") is not None
    except Exception:
        pass
    if not result["installed"]:
        return result
    try:
        import urllib.request
        req = urllib.request.Request(
            f"{result['url']}/api/tags",
            method="GET",
        )
        req.add_header("Accept", "application/json")
        with urllib.request.urlopen(req, timeout=2) as resp:
            if resp.status == 200:
                result["running"] = True
                data = json.loads(resp.read())
                models = data.get("models", [])
                result["models"] = [
                    {"name": m.get("name", ""), "size_mb": round(m.get("size", 0) / 1e6)}
                    for m in models
                ]
    except Exception:
        pass
    # Check active model from config
    try:
        from core.config import get_yaml_config
        cfg = get_yaml_config()
        voice_cfg = cfg.get("voice", {})
        result["model"] = voice_cfg.get("llm_model") or os.environ.get("OLLAMA_MODEL", "phi3:mini")
    except Exception:
        result["model"] = os.environ.get("OLLAMA_MODEL", "phi3:mini")
    # Check if model is actually loaded (ps endpoint)
    if result["running"]:
        try:
            import urllib.request
            req = urllib.request.Request(
                f"{result['url']}/api/ps",
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=2) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read())
                    running_models = data.get("models", [])
                    result["model_loaded"] = len(running_models) > 0
                    if running_models:
                        result["loaded_model"] = running_models[0].get("name", "")
        except Exception:
            pass
    return result


def _read_processes(sort_by: str = "cpu", limit: int = 30) -> list[dict[str, Any]]:
    """Read top processes sorted by cpu or memory."""
    procs: list[dict[str, Any]] = []
    # Try psutil first (works in Docker containers without procps)
    try:
        import psutil
        total_ram = psutil.virtual_memory().total
        for p in psutil.process_iter(["pid", "name", "memory_info", "username", "status"]):
            try:
                info = p.info
                mem = info.get("memory_info")
                rss = mem.rss if mem else 0
                mem_pct = round(rss / total_ram * 100, 1) if total_ram else 0
                # Use cpu_percent with interval=None for cached value
                cpu_pct = p.cpu_percent(interval=None)
                procs.append({
                    "pid": info["pid"],
                    "name": info.get("name", ""),
                    "user": info.get("username", "") or "",
                    "cpu": round(cpu_pct or 0, 1),
                    "mem_pct": mem_pct,
                    "ram_mb": round(rss / 1048576, 1),
                    "status": info.get("status", ""),
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except ImportError:
        # Fallback to ps command
        sort_flag = "-%mem" if sort_by == "ram" else "-%cpu"
        try:
            result = subprocess.run(
                ["ps", "-eo", "pid,user,%cpu,%mem,rss,stat,comm", f"--sort={sort_flag}"],
                capture_output=True, text=True, timeout=5,
            )
            for line in result.stdout.strip().split("\n")[1:limit + 1]:
                parts = line.split(None, 6)
                if len(parts) >= 7:
                    procs.append({
                        "pid": int(parts[0]),
                        "name": parts[6][:60],
                        "user": parts[1],
                        "cpu": float(parts[2]),
                        "mem_pct": float(parts[3]),
                        "ram_mb": round(int(parts[4]) / 1024, 1),
                        "status": parts[5],
                    })
        except Exception as exc:
            logger.debug("ps command failed: %s", exc)
    if sort_by == "ram":
        procs.sort(key=lambda p: p["ram_mb"], reverse=True)
    else:
        procs.sort(key=lambda p: p["cpu"], reverse=True)
    return procs[:limit]


# ---------- System ----------

@router.get("/system")
async def ui_system() -> dict[str, Any]:
    """Combined system endpoint: core health + hardware metrics + LLM status."""
    from core.api.routes.system import _start_time as core_start_time
    from core.api.routes.system import get_system_mode
    mode = get_system_mode()

    hw = _read_hw_metrics()
    ollama = _read_ollama_status()

    return {
        "core": {
            "status": "ok",
            "version": "0.3.0-beta",
            "mode": mode,
            "uptime": int(time.time() - core_start_time),
            "integrity": "ok",
        },
        "hardware": hw,
        "ollama": ollama,
    }


@router.get("/system/processes")
async def ui_system_processes(sort: str = "cpu", limit: int = 30) -> dict[str, Any]:
    """Return top processes sorted by cpu or ram."""
    loop = asyncio.get_event_loop()
    procs = await loop.run_in_executor(None, lambda: _read_processes(sort, limit))
    return {"processes": procs}


@router.post("/system/processes/{pid}/kill")
async def ui_kill_process(pid: int) -> dict[str, Any]:
    """Kill a process by PID. Refuses to kill PID 1 and core process."""
    if pid <= 1:
        raise HTTPException(status_code=403, detail="Cannot kill PID 0 or 1")
    if pid == os.getpid():
        raise HTTPException(status_code=403, detail="Cannot kill the core process")
    try:
        os.kill(pid, 15)  # SIGTERM
        return {"ok": True, "pid": pid, "signal": "SIGTERM"}
    except ProcessLookupError:
        raise HTTPException(status_code=404, detail=f"Process {pid} not found")
    except PermissionError:
        raise HTTPException(status_code=403, detail=f"Permission denied for PID {pid}")


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
                    "ui": m.manifest.get("ui"),
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
    broadcast_event("module.stopped", {"name": info.name})
    return {"name": info.name, "status": info.status.value}


@router.post("/modules/{name}/start")
async def ui_start_module(name: str) -> dict[str, Any]:
    from core.module_loader.loader import get_plugin_manager
    manager = get_plugin_manager()
    module = manager.get_module(name)
    if module is None:
        raise HTTPException(status_code=404, detail="Module not found")
    info = await manager.start(name)
    broadcast_event("module.started", {"name": info.name})
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
    broadcast_event("module.removed", {"name": name})


# ---------- SSE — real-time sync stream ----------

@router.get("/stream")
async def ui_sse_stream(request: Request) -> StreamingResponse:
    """Server-Sent Events stream for real-time sync between browser tabs/devices."""
    queue: asyncio.Queue[str] = asyncio.Queue(maxsize=64)
    _sse_clients.add(queue)

    async def generator():
        # Send initial handshake so the client knows it's connected
        yield "data: {\"type\":\"connected\"}\n\n"
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=20.0)
                    yield f"data: {data}\n\n"
                except asyncio.TimeoutError:
                    # keepalive ping
                    yield "data: {\"type\":\"ping\"}\n\n"
        finally:
            _sse_clients.discard(queue)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ---------- Widget layout persistence (cross-device sync) ----------

@router.get("/layout")
async def get_layout() -> dict[str, Any]:
    """Return persisted widget layout (shared across all browsers)."""
    return _load_layout()


@router.post("/layout")
async def save_layout(request: Request) -> dict[str, Any]:
    """Persist widget layout and broadcast change to all connected browsers."""
    layout = await request.json()
    _save_layout(layout)
    broadcast_event("layout_changed", layout)
    return {"ok": True}


# ---------- Settings sync (theme / language) across all browsers ----------

class SettingsBody(BaseModel):
    theme: str | None = None
    language: str | None = None

@router.post("/settings")
async def save_settings(body: SettingsBody) -> dict[str, Any]:
    """Save settings and broadcast change to all connected browsers via SSE."""
    payload: dict[str, Any] = {}
    if body.theme is not None:
        payload["theme"] = body.theme
    if body.language is not None:
        payload["language"] = body.language
        update_config("system", "language", body.language)
    if payload:
        broadcast_event("settings_changed", payload)
    return {"ok": True}


# ---------- Module Content Proxy ----------

def _get_module_or_404(name: str):
    from core.module_loader.loader import get_plugin_manager
    manager = get_plugin_manager()
    module = manager.get_module(name)
    if module is None:
        raise HTTPException(status_code=404, detail="Module not found")
    return module


@router.get("/modules/{name}/widget")
async def ui_module_widget(name: str) -> HTMLResponse:
    """Serve module widget HTML from module_dir."""
    module = _get_module_or_404(name)
    if not module.module_dir:
        raise HTTPException(status_code=404, detail="Module directory not available")
    widget_file = module.manifest.get("ui", {}).get("widget", {}).get("file", "widget.html")
    fpath = Path(module.module_dir) / widget_file
    if not fpath.is_file():
        raise HTTPException(status_code=404, detail="Widget file not found")
    return HTMLResponse(fpath.read_text(encoding="utf-8"))


@router.get("/modules/{name}/settings")
async def ui_module_settings(name: str) -> HTMLResponse:
    """Serve module settings HTML from module_dir."""
    module = _get_module_or_404(name)
    if not module.module_dir:
        raise HTTPException(status_code=404, detail="Module directory not available")
    settings_file = module.manifest.get("ui", {}).get("settings", "settings.html")
    fpath = Path(module.module_dir) / settings_file
    if not fpath.is_file():
        raise HTTPException(status_code=404, detail="Settings file not found")
    return HTMLResponse(fpath.read_text(encoding="utf-8"))


@router.get("/modules/{name}/icon")
async def ui_module_icon(name: str) -> Response:
    """Serve module icon file."""
    module = _get_module_or_404(name)
    if not module.module_dir:
        raise HTTPException(status_code=404, detail="Module directory not available")
    icon_file = module.manifest.get("ui", {}).get("icon", "icon.svg")
    fpath = Path(module.module_dir) / icon_file
    if not fpath.is_file():
        raise HTTPException(status_code=404, detail="Icon file not found")
    return Response(content=fpath.read_bytes(), media_type="image/svg+xml")


@router.api_route(
    "/modules/{name}/proxy/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
)
async def ui_module_proxy(name: str, path: str, request: Request) -> Response:
    """Proxy API requests to a bus-connected module via Module Bus."""
    module = _get_module_or_404(name)
    if module.status.value != "RUNNING":
        raise HTTPException(status_code=503, detail="Module is not running")

    from core.module_bus import get_module_bus
    bus = get_module_bus()
    if not bus.is_connected(name):
        raise HTTPException(status_code=502, detail="Module unreachable")

    body_bytes = await request.body()
    body = None
    if body_bytes:
        try:
            body = json.loads(body_bytes)
        except (json.JSONDecodeError, ValueError):
            body = body_bytes.decode(errors="replace")

    try:
        result = await bus.send_api_request(
            module=name,
            method=request.method,
            path=f"/{path}",
            body=body,
            timeout=30.0,
        )
        return Response(
            content=json.dumps(result).encode(),
            status_code=200,
            media_type="application/json",
        )
    except TimeoutError:
        raise HTTPException(status_code=504, detail="Module unreachable")
    except Exception as exc:
        logger.warning("Module proxy error for %s: %s", name, exc)
        raise HTTPException(status_code=502, detail="Proxy request failed")


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
    "home_devices": {"required": False, "label": "Home Devices"},
    "platform": {"required": False, "label": "Platform"},
    "import": {"required": False, "label": "Import"},
}


@router.get("/wizard/status")
async def ui_wizard_status() -> dict[str, Any]:
    yaml_cfg = read_config()
    wizard_cfg = yaml_cfg.get("wizard", {})
    completed = wizard_cfg.get("completed", False) or _wizard_state["completed"]
    return {"completed": completed}


@router.get("/wizard/requirements")
async def ui_wizard_requirements() -> dict[str, Any]:
    yaml_cfg = read_config()
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
    extra = await _apply_wizard_step(step_name, body.data)

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

    result = {
        "step": step_name,
        "status": "ok",
        "next_step": next_step,
    }
    if extra:
        result.update(extra)
    return result


def _persist_wizard_completed() -> None:
    """Write wizard_completed=true to core.yaml."""
    try:
        config = read_config()
        config.setdefault("wizard", {})["completed"] = True
        write_config(config)
        logger.info("Wizard completed, persisted to config")
    except Exception as exc:
        logger.error("Failed to persist wizard state: %s", exc)


@router.post("/wizard/reset")
async def ui_wizard_reset() -> dict[str, Any]:
    """Reset wizard state — allows re-running the initial setup."""
    _wizard_state["completed"] = False
    _wizard_state["steps"] = {}
    try:
        config = read_config()
        config["wizard"] = {"completed": False}
        write_config(config)
        # Invalidate the cached yaml config so subsequent reads pick up the reset
        import core.config as _cfg
        _cfg._yaml_config = None
        logger.info("Wizard state reset")
    except Exception as exc:
        logger.error("Failed to reset wizard state: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))
    return {"status": "ok", "message": "Wizard reset. Reload the page to start setup."}


async def _apply_wizard_step(step: str, data: dict[str, Any]) -> dict[str, Any] | None:
    """Apply wizard step data to real system config. Returns extra response fields."""
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
            update_config("stt", "vosk", {"active_model": data["model"]})

        elif step == "tts_voice" and data.get("voice"):
            update_config("voice", "tts_voice", data["voice"])
            os.environ["PIPER_VOICE"] = data["voice"]

        elif step == "admin_user" and data.get("username"):
            update_section("admin", {
                "username": data["username"],
                # PIN is not stored in plaintext in yaml; just mark admin created
                "created": True,
            })
            # Create admin/owner account in the DB
            if data.get("pin"):
                try:
                    from core.module_loader.sandbox import get_sandbox
                    um = get_sandbox().get_in_process_module("user-manager")
                    if um:
                        existing = await um._users.get_by_username(data["username"])
                        if not existing:
                            await um._users.create(
                                username=data["username"],
                                display_name=data["username"],
                                pin=data["pin"],
                                role="owner",
                            )
                            logger.info("Wizard: created owner account '%s' in DB", data["username"])
                        else:
                            logger.info("Wizard: owner '%s' already exists in DB", data["username"])
                except Exception as exc:
                    logger.warning("Wizard: failed to create owner in DB: %s", exc)

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

        elif step == "home_devices":
            if data.get("device_name"):
                update_config("system", "kiosk_name", data["device_name"])
            # Issue a temporary session token for the wizard browser
            # (the browser is NOT registered as a persistent device)
            try:
                from core.module_loader.sandbox import get_sandbox
                um = get_sandbox().get_in_process_module("user-manager")
                if um:
                    # Find admin/owner user
                    users = await um._users.list_all()
                    owner = next((u for u in users if u.role == "owner" and u.active), None)
                    if owner:
                        session_token = um._sessions.grant(
                            user_id=owner.user_id,
                            role=owner.role,
                            display_name=owner.display_name or owner.username,
                            device_name="Wizard Browser",
                        )
                        logger.info("Wizard: issued session token for browser (owner='%s')", owner.username)
                        return {"session_token": session_token}
            except Exception as exc:
                logger.warning("Wizard: failed to issue session token: %s", exc)

        elif step == "platform" and data.get("device_hash"):
            update_config("platform", "device_hash", data["device_hash"])

    except Exception as exc:
        logger.warning("Failed to apply wizard step '%s': %s", step, exc)
    return None


# ---------- Setup QR ----------

@router.get("/setup/qr")
async def ui_setup_qr() -> dict[str, Any]:
    """Generate QR data for initial device setup (AP mode)."""
    settings = get_settings()
    url = f"http://192.168.4.1:{settings.ui_port}"
    try:
        import qrcode
        qr = qrcode.QRCode(border=1)
        qr.add_data(url)
        qr.make(fit=True)
        matrix = qr.get_matrix()
        return {
            "url": url,
            "matrix": matrix,
            "size": len(matrix),
            "ssid": "Selena-Setup",
        }
    except ImportError:
        return {
            "url": url,
            "matrix": None,
            "size": 0,
            "ssid": "Selena-Setup",
        }
