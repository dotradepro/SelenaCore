"""
core/api/routes/setup.py — Real device setup & configuration API endpoints.

Endpoints for:
  - WiFi scanning & connection (nmcli)
  - Audio device detection
  - STT model list & selection (Whisper)
  - TTS voice list, selection & preview (Piper)
  - LLM model list, download & selection (Ollama)
  - Timezone list & application
  - Network status

No module_token auth — localhost only, protected by iptables.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any
from zoneinfo import available_timezones

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core.config_writer import get_value, read_config, update_config

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/setup", tags=["setup"])


# ================================================================== #
#  Pydantic schemas                                                    #
# ================================================================== #

class WifiConnectRequest(BaseModel):
    ssid: str
    password: str = ""


class SelectModelRequest(BaseModel):
    model: str


class SelectVoiceRequest(BaseModel):
    voice: str


class PreviewVoiceRequest(BaseModel):
    text: str
    voice: str | None = None


class SetTimezoneRequest(BaseModel):
    timezone: str


class ConfigUpdateRequest(BaseModel):
    section: str
    key: str
    value: Any


# ================================================================== #
#  Wi-Fi                                                               #
# ================================================================== #

def _nmcli_available() -> bool:
    try:
        subprocess.run(
            ["nmcli", "--version"],
            capture_output=True, timeout=5,
        )
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


@router.get("/wifi/scan")
async def wifi_scan() -> dict[str, Any]:
    """Scan for available Wi-Fi networks via nmcli."""
    if not _nmcli_available():
        return {
            "networks": [],
            "available": False,
            "message": "NetworkManager (nmcli) is not available. Configure Wi-Fi externally.",
        }

    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(None, _scan_wifi_sync)
        return {"networks": result, "available": True, "message": ""}
    except Exception as exc:
        logger.warning("WiFi scan failed: %s", exc)
        return {"networks": [], "available": True, "message": str(exc)}


def _scan_wifi_sync() -> list[dict[str, Any]]:
    """Run nmcli to list available networks."""
    proc = subprocess.run(
        ["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY,IN-USE", "dev", "wifi", "list", "--rescan", "yes"],
        capture_output=True, text=True, timeout=15,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"nmcli failed: {proc.stderr.strip()}")

    networks: dict[str, dict[str, Any]] = {}
    for line in proc.stdout.strip().splitlines():
        parts = line.split(":")
        if len(parts) < 3:
            continue
        ssid = parts[0].strip()
        if not ssid:
            continue
        signal = int(parts[1]) if parts[1].isdigit() else 0
        security = parts[2].strip() if len(parts) > 2 else ""
        in_use = parts[3].strip() == "*" if len(parts) > 3 else False
        # Deduplicate by SSID, keep strongest
        if ssid not in networks or signal > networks[ssid]["signal"]:
            networks[ssid] = {
                "ssid": ssid,
                "signal": signal,
                "security": security,
                "connected": in_use,
            }

    return sorted(networks.values(), key=lambda n: n["signal"], reverse=True)


@router.post("/wifi/connect")
async def wifi_connect(req: WifiConnectRequest) -> dict[str, Any]:
    """Connect to a WiFi network via nmcli."""
    if not _nmcli_available():
        raise HTTPException(status_code=503, detail="NetworkManager not available")

    if not req.ssid:
        raise HTTPException(status_code=422, detail="SSID is required")

    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(None, _connect_wifi_sync, req.ssid, req.password)
        return result
    except Exception as exc:
        logger.error("WiFi connect failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


def _connect_wifi_sync(ssid: str, password: str) -> dict[str, Any]:
    """Connect to WiFi via nmcli."""
    # Stop hotspot if active (AP and client mode are mutually exclusive on wlan0)
    if _is_ap_active():
        _stop_ap_sync()

    cmd = ["nmcli", "dev", "wifi", "connect", ssid]
    if password:
        cmd += ["password", password]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        error_msg = proc.stderr.strip() or proc.stdout.strip()
        raise RuntimeError(f"Connection failed: {error_msg}")

    # Get IP address
    ip = _get_current_ip()
    return {"status": "connected", "ssid": ssid, "ip": ip}


def _get_current_ip() -> str:
    """Get current LAN IP address."""
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(2)
        s.connect(("8.8.8.8", 53))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "unknown"


@router.get("/wifi/status")
async def wifi_status() -> dict[str, Any]:
    """Get current WiFi connection status."""
    ip = _get_current_ip()
    ssid = ""
    if _nmcli_available():
        try:
            proc = subprocess.run(
                ["nmcli", "-t", "-f", "GENERAL.CONNECTION", "dev", "show", "wlan0"],
                capture_output=True, text=True, timeout=5,
            )
            if proc.returncode == 0:
                for line in proc.stdout.splitlines():
                    if "CONNECTION" in line:
                        ssid = line.split(":", 1)[-1].strip()
                        break
        except Exception:
            pass
    return {"connected": ip != "unknown", "ssid": ssid, "ip": ip}


@router.get("/wifi/enabled")
async def wifi_enabled() -> dict[str, Any]:
    """Check if the WiFi adapter is enabled."""
    if not _nmcli_available():
        return {"enabled": False, "adapter_found": False}
    try:
        proc = subprocess.run(
            ["nmcli", "-t", "-f", "TYPE,STATE", "device"],
            capture_output=True, text=True, timeout=5,
        )
        for line in proc.stdout.strip().splitlines():
            parts = line.split(":")
            if len(parts) >= 2 and parts[0] == "wifi":
                state = parts[1].strip()
                return {
                    "enabled": state not in ("unavailable", "unmanaged"),
                    "adapter_found": True,
                    "state": state,
                }
        return {"enabled": False, "adapter_found": False}
    except Exception as exc:
        logger.warning("WiFi enabled check failed: %s", exc)
        return {"enabled": False, "adapter_found": False}


@router.post("/wifi/toggle")
async def wifi_toggle(data: dict[str, Any]) -> dict[str, Any]:
    """Enable or disable the WiFi adapter via nmcli."""
    if not _nmcli_available():
        raise HTTPException(status_code=503, detail="NetworkManager not available")

    enable = bool(data.get("enable", True))
    action = "on" if enable else "off"

    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, _toggle_wifi_sync, action)
        return {"enabled": enable, "message": f"WiFi turned {action}"}
    except Exception as exc:
        logger.error("WiFi toggle failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


def _toggle_wifi_sync(action: str) -> None:
    """Enable/disable WiFi via nmcli radio."""
    proc = subprocess.run(
        ["nmcli", "radio", "wifi", action],
        capture_output=True, text=True, timeout=10,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"nmcli radio wifi {action} failed: {proc.stderr.strip()}")


@router.get("/network/status")
async def network_status() -> dict[str, Any]:
    """Full network status: ethernet, WiFi, internet connectivity."""
    result: dict[str, Any] = {
        "internet": False,
        "ethernet": {"connected": False, "ip": None, "interface": None},
        "wifi": {"connected": False, "ssid": None, "ip": None, "enabled": False, "adapter_found": False},
    }

    # Check internet connectivity
    try:
        proc = subprocess.run(
            ["ping", "-c", "1", "-W", "2", "8.8.8.8"],
            capture_output=True, timeout=5,
        )
        result["internet"] = proc.returncode == 0
    except Exception:
        pass

    if not _nmcli_available():
        # Fallback: still try to detect IP
        ip = _get_current_ip()
        if ip != "unknown":
            result["ethernet"]["connected"] = True
            result["ethernet"]["ip"] = ip
        return result

    # Parse nmcli device status
    try:
        proc = subprocess.run(
            ["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device"],
            capture_output=True, text=True, timeout=5,
        )
        for line in proc.stdout.strip().splitlines():
            parts = line.split(":")
            if len(parts) < 3:
                continue
            dev, dtype, state = parts[0], parts[1], parts[2]
            conn = parts[3] if len(parts) > 3 else ""

            if dtype == "ethernet" and state == "connected":
                eth_ip = _get_interface_ip(dev)
                result["ethernet"] = {
                    "connected": True,
                    "ip": eth_ip,
                    "interface": dev,
                }
            elif dtype == "wifi":
                result["wifi"]["adapter_found"] = True
                result["wifi"]["enabled"] = state not in ("unavailable", "unmanaged")
                if state == "connected":
                    result["wifi"]["connected"] = True
                    result["wifi"]["ssid"] = conn
                    result["wifi"]["ip"] = _get_interface_ip(dev)
    except Exception as exc:
        logger.warning("Network status check failed: %s", exc)

    return result


def _get_interface_ip(interface: str) -> str | None:
    """Get IP address of a specific network interface."""
    try:
        proc = subprocess.run(
            ["ip", "-4", "-o", "addr", "show", interface],
            capture_output=True, text=True, timeout=5,
        )
        match = re.search(r"inet\s+(\d+\.\d+\.\d+\.\d+)", proc.stdout)
        return match.group(1) if match else None
    except Exception:
        return None


# ================================================================== #
#  Access Point (Hotspot) for initial setup                            #
# ================================================================== #

AP_SSID = "Selena-Setup"
AP_PASSWORD = "selena1234"
AP_INTERFACE = "wlan0"


def _is_ap_active() -> bool:
    """Check if the hotspot connection is currently active."""
    try:
        proc = subprocess.run(
            ["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show", "--active"],
            capture_output=True, text=True, timeout=5,
        )
        for line in proc.stdout.strip().splitlines():
            if "Hotspot" in line:
                return True
        return False
    except Exception:
        return False


def _start_ap_sync() -> dict[str, Any]:
    """Start WiFi hotspot via nmcli."""
    # Stop any existing hotspot first
    subprocess.run(
        ["nmcli", "connection", "down", "Hotspot"],
        capture_output=True, text=True, timeout=10,
    )
    proc = subprocess.run(
        ["nmcli", "device", "wifi", "hotspot", "ifname", AP_INTERFACE,
         "ssid", AP_SSID, "password", AP_PASSWORD, "band", "bg"],
        capture_output=True, text=True, timeout=15,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"Failed to start AP: {proc.stderr.strip()}")

    ip = _get_interface_ip(AP_INTERFACE) or "10.42.0.1"
    return {"status": "active", "ssid": AP_SSID, "password": AP_PASSWORD, "ip": ip}


def _stop_ap_sync() -> None:
    """Stop WiFi hotspot."""
    subprocess.run(
        ["nmcli", "connection", "down", "Hotspot"],
        capture_output=True, text=True, timeout=10,
    )


@router.get("/ap/status")
async def ap_status() -> dict[str, Any]:
    """Check if the WiFi hotspot is currently active."""
    if not _nmcli_available():
        return {"active": False}
    loop = asyncio.get_event_loop()
    active = await loop.run_in_executor(None, _is_ap_active)
    if active:
        ip = await loop.run_in_executor(None, _get_interface_ip, AP_INTERFACE)
        return {"active": True, "ssid": AP_SSID, "password": AP_PASSWORD, "ip": ip or "10.42.0.1"}
    return {"active": False}


@router.post("/ap/start")
async def ap_start() -> dict[str, Any]:
    """Start WiFi access point for phone-based setup."""
    if not _nmcli_available():
        raise HTTPException(status_code=503, detail="NetworkManager not available")
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(None, _start_ap_sync)
        return result
    except Exception as exc:
        logger.error("AP start failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/ap/stop")
async def ap_stop() -> dict[str, Any]:
    """Stop WiFi access point."""
    if not _nmcli_available():
        raise HTTPException(status_code=503, detail="NetworkManager not available")
    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, _stop_ap_sync)
        return {"status": "stopped"}
    except Exception as exc:
        logger.error("AP stop failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ================================================================== #
#  Audio Devices                                                       #
# ================================================================== #

@router.get("/audio/devices")
async def audio_devices() -> dict[str, Any]:
    """Detect available audio input/output devices."""
    try:
        from system_modules.voice_core.audio_manager import detect_audio_devices
        devices = detect_audio_devices()
        return {
            "inputs": [{"id": d.id, "name": d.name, "type": d.type} for d in devices.inputs],
            "outputs": [{"id": d.id, "name": d.name, "type": d.type} for d in devices.outputs],
        }
    except Exception as exc:
        logger.warning("Audio detection failed: %s", exc)
        return {"inputs": [], "outputs": []}


@router.post("/audio/select")
async def audio_select(body: dict[str, str]) -> dict[str, Any]:
    """Persist audio device selection to core.yaml."""
    input_device = body.get("input")
    output_device = body.get("output")
    if input_device:
        update_config("voice", "audio_force_input", input_device)
    if output_device:
        update_config("voice", "audio_force_output", output_device)
    return {"status": "ok"}


# ================================================================== #
#  STT Models (Vosk)                                                   #
# ================================================================== #

STT_MODELS = [
    {"id": "vosk-model-small-uk", "name": "Ukrainian (small)", "lang": "uk", "ram_mb": 150, "size_mb": 133, "quality": "good"},
    {"id": "vosk-model-small-ru", "name": "Russian (small)", "lang": "ru", "ram_mb": 150, "size_mb": 45, "quality": "good"},
    {"id": "vosk-model-small-en-us", "name": "English (small)", "lang": "en", "ram_mb": 150, "size_mb": 40, "quality": "good"},
    {"id": "vosk-model-uk", "name": "Ukrainian (large)", "lang": "uk", "ram_mb": 500, "size_mb": 1600, "quality": "high"},
    {"id": "vosk-model-ru", "name": "Russian (large)", "lang": "ru", "ram_mb": 500, "size_mb": 1600, "quality": "high"},
    {"id": "vosk-model-en-us", "name": "English (large)", "lang": "en", "ram_mb": 500, "size_mb": 1600, "quality": "high"},
]


@router.get("/stt/models")
async def stt_models() -> dict[str, Any]:
    """List available Vosk STT models with installed status."""
    models_dir = Path(os.environ.get("VOSK_MODELS_DIR", "/var/lib/selena/models/vosk"))
    active_model = get_value("voice", "stt_model", os.environ.get("VOSK_MODEL", "vosk-model-small-uk"))

    # Check system RAM
    ram_total_mb = 0
    ram_available_mb = 0
    try:
        import psutil
        vm = psutil.virtual_memory()
        ram_total_mb = vm.total // (1024 * 1024)
        ram_available_mb = vm.available // (1024 * 1024)
    except ImportError:
        pass

    result = []
    for m in STT_MODELS:
        model_path = models_dir / m["id"]
        result.append({
            **m,
            "installed": model_path.is_dir(),
            "active": m["id"] == active_model,
            "fits_ram": ram_available_mb >= m["ram_mb"] if ram_available_mb else True,
        })

    return {
        "models": result,
        "active": active_model,
        "ram_total_mb": ram_total_mb,
        "ram_available_mb": ram_available_mb,
    }


@router.post("/stt/select")
async def stt_select(req: SelectModelRequest) -> dict[str, Any]:
    """Select and persist Vosk STT model choice."""
    valid_ids = {m["id"] for m in STT_MODELS}
    if req.model not in valid_ids:
        raise HTTPException(status_code=422, detail=f"Invalid model. Valid: {valid_ids}")

    update_config("voice", "stt_model", req.model)
    os.environ["VOSK_MODEL"] = req.model
    logger.info("STT model set to %s", req.model)
    return {"status": "ok", "model": req.model}


# ================================================================== #
#  TTS Voices (Piper)                                                  #
# ================================================================== #

TTS_VOICES = [
    {"id": "uk_UA-ukrainian_tts-medium", "name": "Tetiana", "language": "uk", "gender": "female", "size_mb": 55},
    {"id": "uk_UA-lada-medium", "name": "Lada", "language": "uk", "gender": "female", "size_mb": 50},
    {"id": "ru_RU-irina-medium", "name": "Irina", "language": "ru", "gender": "female", "size_mb": 50},
    {"id": "ru_RU-ruslan-medium", "name": "Ruslan", "language": "ru", "gender": "male", "size_mb": 50},
    {"id": "en_US-amy-medium", "name": "Amy", "language": "en", "gender": "female", "size_mb": 50},
    {"id": "en_US-ryan-high", "name": "Ryan", "language": "en", "gender": "male", "size_mb": 60},
]


@router.get("/tts/voices")
async def tts_voices() -> dict[str, Any]:
    """List available Piper TTS voices with installed status."""
    models_dir = Path(os.environ.get("PIPER_MODELS_DIR", "/var/lib/selena/models/piper"))
    active_voice = get_value("voice", "tts_voice", os.environ.get("PIPER_VOICE", "ru_RU-irina-medium"))

    result = []
    for v in TTS_VOICES:
        model_file = models_dir / f"{v['id']}.onnx"
        result.append({
            **v,
            "installed": model_file.exists(),
            "active": v["id"] == active_voice,
        })

    return {"voices": result, "active": active_voice}


@router.post("/tts/select")
async def tts_select(req: SelectVoiceRequest) -> dict[str, Any]:
    """Select and persist TTS voice."""
    valid_ids = {v["id"] for v in TTS_VOICES}
    if req.voice not in valid_ids:
        raise HTTPException(status_code=422, detail=f"Invalid voice. Valid: {valid_ids}")

    update_config("voice", "tts_voice", req.voice)
    os.environ["PIPER_VOICE"] = req.voice
    logger.info("TTS voice set to %s", req.voice)
    return {"status": "ok", "voice": req.voice}


@router.post("/tts/preview")
async def tts_preview(req: PreviewVoiceRequest) -> Any:
    """Synthesize sample text and return WAV audio."""
    from fastapi.responses import Response

    try:
        from system_modules.voice_core.tts import TTSEngine
        engine = TTSEngine(voice=req.voice or "ru_RU-irina-medium")
        text = req.text[:200]  # limit preview length
        wav_bytes = await engine.synthesize(text)
        if not wav_bytes:
            raise HTTPException(status_code=500, detail="TTS synthesis failed")
        return Response(content=wav_bytes, media_type="audio/wav")
    except ImportError:
        raise HTTPException(status_code=503, detail="Piper TTS not available")
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("TTS preview failed: %s", exc)
        raise HTTPException(status_code=500, detail="TTS synthesis error")


# ================================================================== #
#  LLM Models (Ollama)                                                 #
# ================================================================== #

@router.get("/llm/models")
async def llm_models() -> dict[str, Any]:
    """List recommended LLM models with download & active status."""
    try:
        from system_modules.llm_engine.model_manager import get_model_manager
        manager = get_model_manager()
        models = await manager.list_recommended()
        active = manager.get_active()

        # RAM info
        ram_available_gb = 0.0
        try:
            import psutil
            ram_available_gb = psutil.virtual_memory().available / (1024 ** 3)
        except ImportError:
            pass

        return {
            "models": models,
            "active": active,
            "ram_available_gb": round(ram_available_gb, 1),
            "ollama_available": True,
        }
    except Exception as exc:
        logger.warning("LLM model listing failed: %s", exc)
        return {
            "models": [],
            "active": None,
            "ram_available_gb": 0,
            "ollama_available": False,
            "error": str(exc),
        }


@router.post("/llm/select")
async def llm_select(req: SelectModelRequest) -> dict[str, Any]:
    """Switch active LLM model."""
    try:
        from system_modules.llm_engine.model_manager import get_model_manager
        manager = get_model_manager()
        ok = await manager.switch_model(req.model)
        if ok:
            update_config("llm", "default_model", req.model)
            return {"status": "ok", "model": req.model}
        raise HTTPException(status_code=400, detail="Model not installed or switch failed")
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("LLM select failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/llm/download")
async def llm_download(req: SelectModelRequest) -> dict[str, Any]:
    """Trigger model download via Ollama pull. Returns immediately (async)."""
    try:
        from system_modules.llm_engine.model_manager import get_model_manager
        manager = get_model_manager()

        # Check RAM
        if not manager.check_ram_sufficient(req.model):
            raise HTTPException(
                status_code=400,
                detail="Insufficient RAM for this model",
            )

        # Start download in background
        asyncio.create_task(_download_model_bg(req.model))
        return {"status": "downloading", "model": req.model}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("LLM download start failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


async def _download_model_bg(model_id: str) -> None:
    """Background task for model download."""
    try:
        from system_modules.llm_engine.model_manager import get_model_manager
        manager = get_model_manager()
        ok = await manager.download(model_id)
        if ok:
            logger.info("Model %s downloaded successfully", model_id)
        else:
            logger.error("Model %s download failed", model_id)
    except Exception as exc:
        logger.error("Model download error: %s", exc)


@router.get("/llm/status")
async def llm_status() -> dict[str, Any]:
    """Check Ollama availability and current model."""
    try:
        from system_modules.llm_engine.ollama_client import get_ollama_client
        from system_modules.llm_engine.model_manager import get_model_manager
        client = get_ollama_client()
        manager = get_model_manager()

        is_available = await client.is_available()
        installed = await client.list_models() if is_available else []

        return {
            "available": is_available,
            "active_model": manager.get_active(),
            "installed_models": installed,
        }
    except Exception as exc:
        logger.warning("Ollama status check failed: %s", exc)
        return {"available": False, "active_model": None, "installed_models": [], "error": str(exc)}


# ================================================================== #
#  Timezones                                                           #
# ================================================================== #

# Common timezones shown first
_COMMON_TZ = [
    "Europe/Kyiv", "Europe/Moscow", "Europe/London", "Europe/Berlin",
    "Europe/Paris", "Europe/Warsaw", "Europe/Istanbul",
    "America/New_York", "America/Chicago", "America/Denver",
    "America/Los_Angeles", "Asia/Tokyo", "Asia/Shanghai",
    "Asia/Dubai", "Asia/Kolkata", "Australia/Sydney",
    "Pacific/Auckland", "UTC",
]


@router.get("/timezones")
async def list_timezones() -> dict[str, Any]:
    """Return available timezones with common ones first."""
    all_tz = sorted(available_timezones())
    current = get_value("system", "timezone", "UTC")

    common = [tz for tz in _COMMON_TZ if tz in all_tz]
    rest = [tz for tz in all_tz if tz not in _COMMON_TZ]

    return {"timezones": common + rest, "common": common, "current": current}


@router.post("/timezone/set")
async def set_timezone(req: SetTimezoneRequest) -> dict[str, Any]:
    """Apply timezone and persist to core.yaml."""
    if req.timezone not in available_timezones() and req.timezone != "UTC":
        raise HTTPException(status_code=422, detail=f"Invalid timezone: {req.timezone}")

    update_config("system", "timezone", req.timezone)

    # Try to apply system timezone
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _apply_timezone_sync, req.timezone)
    except Exception as exc:
        logger.warning("Could not apply system timezone: %s", exc)

    return {"status": "ok", "timezone": req.timezone}


def _apply_timezone_sync(tz: str) -> None:
    """Apply timezone via timedatectl (best-effort)."""
    try:
        subprocess.run(
            ["timedatectl", "set-timezone", tz],
            capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        logger.debug("timedatectl not available, timezone set in config only")


# ================================================================== #
#  Network Status (combined)                                           #
# ================================================================== #

@router.get("/network/status")
async def network_status() -> dict[str, Any]:
    """Get overall network status: WiFi, Ethernet, internet."""
    ip = _get_current_ip()

    # Check internet
    internet = False
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(2)
        s.connect(("8.8.8.8", 53))
        s.close()
        internet = True
    except Exception:
        pass

    # Get interfaces
    interfaces: list[dict[str, str]] = []
    try:
        proc = subprocess.run(
            ["ip", "-j", "addr", "show"],
            capture_output=True, text=True, timeout=5,
        )
        if proc.returncode == 0:
            import json
            for iface in json.loads(proc.stdout):
                name = iface.get("ifname", "")
                if name in ("lo",):
                    continue
                addrs = []
                for addr_info in iface.get("addr_info", []):
                    if addr_info.get("family") == "inet":
                        addrs.append(addr_info.get("local", ""))
                if addrs:
                    interfaces.append({
                        "name": name,
                        "ip": addrs[0],
                        "type": "wifi" if name.startswith("wl") else "ethernet",
                    })
    except Exception:
        if ip != "unknown":
            interfaces.append({"name": "default", "ip": ip, "type": "unknown"})

    return {
        "internet": internet,
        "ip": ip,
        "interfaces": interfaces,
        "nmcli_available": _nmcli_available(),
    }


# ================================================================== #
#  Config Read/Write                                                   #
# ================================================================== #

@router.get("/config")
async def get_config() -> dict[str, Any]:
    """Read current configuration (filtered for frontend)."""
    config = read_config()
    # Return safe sections only
    return {
        "system": config.get("system", {}),
        "voice": config.get("voice", {}),
        "llm": config.get("llm", {}),
        "wizard": config.get("wizard", {}),
    }


@router.post("/config/update")
async def update_config_endpoint(req: ConfigUpdateRequest) -> dict[str, Any]:
    """Update a single config value."""
    allowed_sections = {"system", "voice", "llm", "ui"}
    if req.section not in allowed_sections:
        raise HTTPException(status_code=403, detail=f"Section '{req.section}' not modifiable")

    update_config(req.section, req.key, req.value)
    return {"status": "ok", "section": req.section, "key": req.key}
