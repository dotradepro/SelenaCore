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

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
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

# ── Sticky caches for slow native-service probes ─────────────────────────────
# Both probes can occasionally take longer than the wall budget (Ollama under
# load, Piper preloading a voice, …). Serving the previous good value avoids
# the LLM/Native cards from flickering on the system-info page.
_ollama_cache: dict[str, Any] | None = None
_ollama_cache_ts: float = 0.0
_OLLAMA_CACHE_TTL = 10.0
_OLLAMA_STALE_TTL = 120.0

_native_cache: list[dict[str, Any]] | None = None
_native_cache_ts: float = 0.0
_NATIVE_CACHE_TTL = 10.0
_NATIVE_STALE_TTL = 120.0


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


def _ollama_http_get(url: str, timeout: float = 4.0) -> dict[str, Any] | None:
    """Blocking HTTP GET helper — call only via asyncio.to_thread()."""
    import urllib.request
    req = urllib.request.Request(url, method="GET")
    req.add_header("Accept", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        if resp.status == 200:
            return json.loads(resp.read())
    return None


async def _read_ollama_status() -> dict[str, Any]:
    """Read Ollama/LLM status (best-effort, non-blocking, sticky-cached).

    Ollama runs natively on the host while Selena runs in a container, so the
    host binary is NOT on the container's PATH and ``shutil.which("ollama")``
    would return ``None``. The HTTP API is the authoritative source: if
    ``/api/tags`` answers, Ollama is reachable and we treat it as both
    installed and running.

    Behaviour:
    - Probes are run in a worker thread (non-blocking for the event loop).
    - ``/api/tags`` and ``/api/ps`` are issued in parallel.
    - Successful results are cached for ``_OLLAMA_CACHE_TTL`` seconds.
    - On a transient probe failure, the previous good result is reused for
      up to ``_OLLAMA_STALE_TTL`` seconds — this prevents the system-info
      card from flickering when Ollama is briefly busy.
    """
    global _ollama_cache, _ollama_cache_ts

    # Active model name is read fresh from config every call — it changes
    # the moment the user switches LLM provider and must NOT be cached.
    try:
        from core.config import get_yaml_config
        voice_cfg = get_yaml_config().get("voice", {})
        active_model = voice_cfg.get("llm_model") or os.environ.get("OLLAMA_MODEL", "phi3:mini")
    except Exception:
        active_model = os.environ.get("OLLAMA_MODEL", "phi3:mini")

    now = time.monotonic()
    if _ollama_cache is not None and (now - _ollama_cache_ts) < _OLLAMA_CACHE_TTL:
        cached = dict(_ollama_cache)
        cached["model"] = active_model
        return cached

    url = os.environ.get("OLLAMA_URL", "http://localhost:11434")
    result: dict[str, Any] = {
        "installed": False,
        "running": False,
        "model": None,
        "model_loaded": False,
        "url": url,
    }

    tags_data, ps_data = await asyncio.gather(
        asyncio.to_thread(_ollama_http_get, f"{url}/api/tags", 4.0),
        asyncio.to_thread(_ollama_http_get, f"{url}/api/ps", 4.0),
        return_exceptions=True,
    )

    probe_failed = True
    if isinstance(tags_data, dict):
        probe_failed = False
        result["running"] = True
        result["installed"] = True
        models = tags_data.get("models", [])
        result["models"] = [
            {"name": m.get("name", ""), "size_mb": round(m.get("size", 0) / 1e6)}
            for m in models
        ]

    if isinstance(ps_data, dict):
        probe_failed = False
        running_models = ps_data.get("models", [])
        result["model_loaded"] = len(running_models) > 0
        if running_models:
            result["loaded_model"] = running_models[0].get("name", "")

    # Binary-on-PATH fallback for the rare case where the API is down but
    # the local binary is present (e.g. service stopped on this same host).
    if not result["installed"]:
        try:
            result["installed"] = shutil.which("ollama") is not None
        except Exception:
            pass

    # Active model from config (independent of liveness probe).
    result["model"] = active_model

    # Sticky cache: serve last-good on transient probe failure.
    if probe_failed and _ollama_cache is not None and (now - _ollama_cache_ts) < _OLLAMA_STALE_TTL:
        cached = dict(_ollama_cache)
        cached["model"] = active_model
        return cached

    _ollama_cache = result
    _ollama_cache_ts = now
    return result


async def _read_llm_engine_status() -> dict[str, Any]:
    """Aggregate LLM engine state: active provider, configured cloud
    providers (without exposing API keys) and intent-cache stats.
    """
    from core.config_writer import read_config

    config = read_config()
    voice_cfg = config.get("voice", {}) if isinstance(config, dict) else {}
    provider_configs = voice_cfg.get("providers", {}) if isinstance(voice_cfg, dict) else {}

    active_provider = voice_cfg.get("llm_provider", "ollama") or "ollama"
    active_model = (
        voice_cfg.get("llm_model")
        or os.environ.get("OLLAMA_MODEL", "phi3:mini")
    )
    two_step = bool(voice_cfg.get("llm_two_step", False))

    cloud_providers: list[dict[str, Any]] = []
    try:
        from system_modules.llm_engine.cloud_providers import PROVIDERS
        for pid, meta in PROVIDERS.items():
            if not meta.get("needs_key", True):
                continue  # skip ollama — it has its own card
            p_cfg = provider_configs.get(pid, {}) if isinstance(provider_configs, dict) else {}
            cloud_providers.append({
                "id": pid,
                "name": meta.get("name", pid),
                "configured": bool(p_cfg.get("api_key")),
                "model": p_cfg.get("model", "") or "",
                "active": pid == active_provider,
            })
    except Exception as exc:
        logger.debug("LLM cloud providers enumeration failed: %s", exc)

    cache_size = 0
    cache_hot = 0
    try:
        from system_modules.llm_engine.intent_cache import get_intent_cache
        ic = get_intent_cache()
        cache_size = ic.count
        frequent = await ic.get_frequent(min_count=5)
        cache_hot = len(frequent)
    except Exception as exc:
        logger.debug("LLM intent cache stats failed: %s", exc)

    return {
        "provider": active_provider,
        "model": active_model,
        "two_step": two_step,
        "cloud_providers": cloud_providers,
        "intent_cache": {"size": cache_size, "hot": cache_hot},
    }


def _probe_piper_blocking(url: str, timeout: float = 3.0) -> dict[str, Any] | None:
    """Blocking Piper /health probe — call via asyncio.to_thread()."""
    try:
        return _ollama_http_get(f"{url}/health", timeout)
    except Exception:
        return None


def _probe_pulseaudio_blocking() -> bool:
    """Detect a running PulseAudio/PipeWire instance from inside the container."""
    import glob
    if glob.glob("/run/user/*/pulse/native"):
        return True
    if os.path.exists("/tmp/pulse-PKdhtXMmr18n/native"):
        return True
    try:
        proc = subprocess.run(
            ["pactl", "info"],
            capture_output=True, text=True, timeout=1,
        )
        if proc.returncode == 0:
            return True
    except Exception:
        pass
    return False


def _probe_alsa_blocking() -> dict[str, Any]:
    """Enumerate ALSA cards from /dev/snd."""
    import glob
    cards = sorted({
        os.path.basename(p).split("p")[0].replace("pcmC", "card")
        for p in glob.glob("/dev/snd/pcmC*")
    })
    return {"running": len(cards) > 0, "cards": len(cards)}


def _probe_vosk_blocking() -> dict[str, Any]:
    """Vosk is loaded in-process; we report whether models exist on disk."""
    models_dir = os.environ.get("VOSK_MODELS_DIR", "/var/lib/selena/models/vosk")
    try:
        if os.path.isdir(models_dir):
            entries = [
                e for e in os.listdir(models_dir)
                if os.path.isdir(os.path.join(models_dir, e))
            ]
            return {"running": len(entries) > 0, "models": len(entries), "path": models_dir}
    except Exception:
        pass
    return {"running": False, "models": 0, "path": models_dir}


def _probe_dbus_blocking() -> bool:
    return os.path.exists("/var/run/dbus/system_bus_socket")


async def _read_native_services() -> list[dict[str, Any]]:
    """Probe all native (host-side) services that Core depends on.

    Returns a list of dicts ``{name, running, url?, extra}``. Result is
    cached with the same sticky-cache strategy as ``_read_ollama_status``
    so transient hiccups don't blank out the system-info card.
    """
    global _native_cache, _native_cache_ts

    now = time.monotonic()
    if _native_cache is not None and (now - _native_cache_ts) < _NATIVE_CACHE_TTL:
        return _native_cache

    piper_url = os.environ.get("PIPER_GPU_URL", "http://localhost:5100")

    ollama_status, piper_health, pulse_ok, alsa_info, vosk_info, dbus_ok = await asyncio.gather(
        _read_ollama_status(),
        asyncio.to_thread(_probe_piper_blocking, piper_url, 3.0),
        asyncio.to_thread(_probe_pulseaudio_blocking),
        asyncio.to_thread(_probe_alsa_blocking),
        asyncio.to_thread(_probe_vosk_blocking),
        asyncio.to_thread(_probe_dbus_blocking),
        return_exceptions=True,
    )

    services: list[dict[str, Any]] = []

    # Ollama
    if isinstance(ollama_status, dict):
        services.append({
            "name": "ollama",
            "running": bool(ollama_status.get("running")),
            "url": ollama_status.get("url"),
            "extra": {
                "model": ollama_status.get("model"),
                "model_loaded": bool(ollama_status.get("model_loaded")),
                "models_count": len(ollama_status.get("models") or []),
            },
        })
    else:
        services.append({"name": "ollama", "running": False, "url": None, "extra": {}})

    # Piper
    if isinstance(piper_health, dict):
        loaded = piper_health.get("loaded_voices") or []
        services.append({
            "name": "piper",
            "running": True,
            "url": piper_url,
            "extra": {
                "device": piper_health.get("device", ""),
                "voices": len(loaded) if isinstance(loaded, list) else 0,
            },
        })
    else:
        services.append({"name": "piper", "running": False, "url": piper_url, "extra": {}})

    # PulseAudio
    services.append({
        "name": "pulseaudio",
        "running": bool(pulse_ok) if not isinstance(pulse_ok, Exception) else False,
        "url": None,
        "extra": {},
    })

    # ALSA
    if isinstance(alsa_info, dict):
        services.append({
            "name": "alsa",
            "running": bool(alsa_info.get("running")),
            "url": None,
            "extra": {"cards": alsa_info.get("cards", 0)},
        })
    else:
        services.append({"name": "alsa", "running": False, "url": None, "extra": {}})

    # Vosk
    if isinstance(vosk_info, dict):
        services.append({
            "name": "vosk",
            "running": bool(vosk_info.get("running")),
            "url": None,
            "extra": {
                "models": vosk_info.get("models", 0),
                "path": vosk_info.get("path", ""),
            },
        })
    else:
        services.append({"name": "vosk", "running": False, "url": None, "extra": {}})

    # D-Bus
    services.append({
        "name": "dbus",
        "running": bool(dbus_ok) if not isinstance(dbus_ok, Exception) else False,
        "url": None,
        "extra": {},
    })

    _native_cache = services
    _native_cache_ts = now
    return services


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
    """Combined system endpoint: core health + hardware metrics + LLM status
    + native services. Slow probes (Ollama, Piper) are non-blocking and
    sticky-cached so this stays snappy and the UI does not flicker.
    """
    from core.api.routes.system import _start_time as core_start_time
    from core.api.routes.system import get_system_mode
    mode = get_system_mode()

    hw = _read_hw_metrics()

    ollama, llm_engine, native_services = await asyncio.gather(
        _read_ollama_status(),
        _read_llm_engine_status(),
        _read_native_services(),
    )

    settings = get_settings()
    from core.version import VERSION

    return {
        "core": {
            "status": "ok",
            "version": VERSION,
            "mode": mode,
            "uptime": int(time.time() - core_start_time),
            "integrity": "ok",
            "core_port": settings.core_port,
        },
        "hardware": hw,
        "ollama": ollama,
        "llm_engine": llm_engine,
        "native_services": native_services,
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
    """Persist widget layout and broadcast change via SyncManager + SSE."""
    layout = await request.json()
    _save_layout(layout)
    from core.api.sync_manager import get_sync_manager
    await get_sync_manager().update_layout(layout)
    return {"ok": True}


# ---------- Settings sync (theme / language) across all browsers ----------

class SettingsBody(BaseModel):
    theme: str | None = None
    language: str | None = None


@router.get("/settings")
async def get_settings_sync() -> dict[str, Any]:
    """Return current authoritative UI settings (theme, language)."""
    from core.api.sync_manager import get_sync_manager
    return get_sync_manager().settings


@router.post("/settings")
async def save_settings(body: SettingsBody) -> dict[str, Any]:
    """Save settings and broadcast via SyncManager (WS + SSE)."""
    payload: dict[str, Any] = {}
    if body.theme is not None:
        payload["theme"] = body.theme
    if body.language is not None:
        payload["language"] = body.language
        update_config("system", "language", body.language)
    if payload:
        from core.api.sync_manager import get_sync_manager
        await get_sync_manager().update_settings(payload)
    return {"ok": True}


# ---------- WebSocket sync — versioned state + ping/pong ----------

@router.websocket("/sync")
async def ui_sync_websocket(websocket: WebSocket) -> None:
    """WebSocket endpoint for real-time UI sync with versioned state.

    Protocol:
      - On connect: server sends 'hello' (full snapshot) or 'replay' (missed events)
      - Server sends 'event' on state changes, 'ping' every 5s
      - Client must respond 'pong' within 10s or gets disconnected
      - On reconnect with ?v=<version>, server replays missed events
    """
    from core.api.sync_manager import get_sync_manager
    manager = get_sync_manager()

    await websocket.accept()

    # Parse last known version from query param
    last_version = 0
    v_param = websocket.query_params.get("v", "0")
    try:
        last_version = int(v_param)
    except (ValueError, TypeError):
        pass

    client_id = await manager.register(websocket)

    try:
        # Send initial state: full snapshot or replay of missed events
        if last_version > 0:
            events = manager.get_events_since(last_version)
            if events is not None and len(events) > 0:
                replay_data = [
                    {
                        "version": e.version,
                        "event_type": e.event_type,
                        "payload": e.payload,
                    }
                    for e in events
                ]
                await websocket.send_json({"type": "replay", "events": replay_data})
            else:
                # Too old or no events — send full snapshot
                await websocket.send_json(manager.get_snapshot())
        else:
            await websocket.send_json(manager.get_snapshot())

        # Main loop: ping/pong + receive client messages
        ping_task = asyncio.create_task(_ping_loop(manager, client_id))
        try:
            while True:
                raw = await websocket.receive_text()
                try:
                    msg = json.loads(raw)
                    if msg.get("type") == "pong":
                        manager.update_pong(client_id)
                except (json.JSONDecodeError, AttributeError):
                    pass
        except WebSocketDisconnect:
            pass
        finally:
            ping_task.cancel()
            try:
                await ping_task
            except asyncio.CancelledError:
                pass

    except Exception as exc:
        logger.debug("WebSocket sync error for %s: %s", client_id, exc)
    finally:
        manager.unregister(client_id)


async def _ping_loop(manager, client_id: str) -> None:
    """Send pings every 5s, close if client doesn't pong within 10s."""
    while True:
        await asyncio.sleep(5.0)
        if manager.is_client_stale(client_id, timeout_sec=15.0):
            logger.debug("WebSocket client %s stale, closing", client_id)
            break
        if not await manager.send_ping(client_id):
            break


# ---------- Module Content Proxy ----------

def _get_module_or_404(name: str):
    from core.module_loader.loader import get_plugin_manager
    manager = get_plugin_manager()
    module = manager.get_module(name)
    if module is None:
        raise HTTPException(status_code=404, detail="Module not found")
    return module


# Headers that force Chromium (especially the kiosk profile, which has a
# persistent disk cache at /tmp/chromium-kiosk) to revalidate widget HTML
# on every load instead of serving stale markup after a deploy.
_NO_CACHE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}


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
    return HTMLResponse(fpath.read_text(encoding="utf-8"), headers=_NO_CACHE_HEADERS)


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
    return HTMLResponse(fpath.read_text(encoding="utf-8"), headers=_NO_CACHE_HEADERS)


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

    # --- Validate + persist step data to core.yaml first; only mark the
    # step as done if _apply_wizard_step did not raise. This is critical
    # for admin_user — without a valid PIN we must NOT advance the wizard,
    # otherwise the user is locked out of settings forever.
    extra = await _apply_wizard_step(step_name, body.data)

    # Mark step as done only after successful application
    _wizard_state["steps"][step_name] = body.data
    logger.info("Wizard step completed: %s", step_name)

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
            from core.config_writer import update_nested
            update_nested("stt.vosk.active_model", data["model"])

        elif step == "tts_voice" and data.get("voice"):
            from core.config_writer import update_nested
            update_config("voice", "tts_voice", data["voice"])  # legacy key
            update_nested("voice.tts.primary.voice", data["voice"])  # canonical
            os.environ["PIPER_VOICE"] = data["voice"]

        elif step == "admin_user":
            # admin_user is REQUIRED — without a valid PIN the user cannot
            # later unlock the settings page (KioskElevationGate). This step
            # MUST NOT silently succeed without creating the owner account.
            username = (data.get("username") or "").strip()
            pin = (data.get("pin") or "").strip()
            if not username:
                raise HTTPException(status_code=422, detail="Username is required")
            if not pin or len(pin) < 4 or not pin.isdigit():
                raise HTTPException(
                    status_code=422,
                    detail="A numeric PIN of at least 4 digits is required — "
                           "you will need it to unlock settings later",
                )

            from core.module_loader.sandbox import get_sandbox
            um = get_sandbox().get_in_process_module("user-manager")
            if um is None:
                raise HTTPException(
                    status_code=503,
                    detail="user-manager module is not loaded; cannot create owner account",
                )

            try:
                existing = await um._users.get_by_username(username)
                if existing:
                    # Re-set the PIN so the user can recover if they re-ran the wizard
                    if hasattr(um._users, "update_pin"):
                        await um._users.update_pin(existing.user_id, pin)
                    logger.info("Wizard: owner '%s' already existed — PIN refreshed", username)
                else:
                    # UserManager.create() does NOT accept a `role` argument:
                    # the very first user is automatically assigned `admin`,
                    # subsequent users become `resident`. Since this runs as
                    # part of the first-run wizard the created account will
                    # be the admin owner.
                    await um._users.create(
                        username=username,
                        display_name=username,
                        pin=pin,
                    )
                    logger.info("Wizard: created admin account '%s' in DB", username)
            except HTTPException:
                raise
            except Exception as exc:
                logger.error("Wizard: failed to create owner in DB: %s", exc)
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to create owner account: {exc}",
                )

            update_section("admin", {
                "username": username,
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

        elif step == "home_devices":
            if data.get("device_name"):
                update_config("system", "kiosk_name", data["device_name"])
            # Issue a temporary session token for the wizard browser
            # (the browser is NOT registered as a persistent device)
            try:
                from core.module_loader.sandbox import get_sandbox
                um = get_sandbox().get_in_process_module("user-manager")
                if um:
                    # Find the admin user (UserManager calls the first user
                    # `admin`, not `owner` — see profiles.py:create()).
                    users = await um._users.list_all()
                    owner = next((u for u in users if u.role == "admin" and u.active), None)
                    if owner is None:
                        # Last-ditch: any active user (older databases)
                        owner = next((u for u in users if u.active), None)
                    if owner:
                        session_token = um._sessions.grant(
                            user_id=owner.user_id,
                            role=owner.role,
                            display_name=owner.display_name or owner.username,
                            device_name="Wizard Browser",
                        )
                        logger.info("Wizard: issued session token for browser (admin='%s')", owner.username)
                        return {"session_token": session_token}
            except Exception as exc:
                logger.warning("Wizard: failed to issue session token: %s", exc)

        elif step == "platform" and data.get("device_hash"):
            update_config("platform", "device_hash", data["device_hash"])

        elif step == "import":
            # Persist the user's selected import sources (Home Assistant, Tuya, Hue,
            # MQTT, etc.). Provisioning will create the corresponding bridge modules
            # in a follow-up task. Empty selection is allowed (skip step).
            sources = data.get("sources") or []
            if isinstance(sources, str):
                sources = [s.strip() for s in sources.split(",") if s.strip()]
            update_section("wizard_import", {"sources": sources})

    except HTTPException:
        # Validation failures (e.g. admin_user without PIN) MUST propagate
        # so the frontend re-prompts and the user cannot lock themselves out.
        raise
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
