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

from core.config_writer import get_value, read_config, update_config, update_many

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
#  Provision — background task state                                   #
# ================================================================== #

class _ProvisionState:
    """In-memory state for the provisioning pipeline."""

    def __init__(self) -> None:
        self.running = False
        self.done = False
        self.failed = False
        self.error: str | None = None
        self.current_task: str = ""
        self.tasks: list[dict[str, Any]] = []
        self.completed: int = 0
        self.total: int = 0

    def reset(self) -> None:
        self.running = False
        self.done = False
        self.failed = False
        self.error = None
        self.current_task = ""
        self.tasks = []
        self.completed = 0
        self.total = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "done": self.done,
            "failed": self.failed,
            "error": self.error,
            "current_task": self.current_task,
            "tasks": self.tasks,
            "completed": self.completed,
            "total": self.total,
        }


_provision = _ProvisionState()


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
        cfg = read_config()
        voice_cfg = cfg.get("voice", {}) or {}
        return {
            "inputs": [{"id": d.id, "name": d.name, "type": d.type} for d in devices.inputs],
            "outputs": [{"id": d.id, "name": d.name, "type": d.type} for d in devices.outputs],
            "selected_input": voice_cfg.get("audio_force_input"),
            "selected_output": voice_cfg.get("audio_force_output"),
        }
    except Exception as exc:
        logger.warning("Audio detection failed: %s", exc)
        return {"inputs": [], "outputs": []}


@router.post("/audio/select")
async def audio_select(body: dict[str, str]) -> dict[str, Any]:
    """Persist audio device selection to core.yaml."""
    input_device = body.get("input")
    output_device = body.get("output")
    updates = []
    if input_device:
        updates.append(("voice", "audio_force_input", input_device))
    if output_device:
        updates.append(("voice", "audio_force_output", output_device))
    if updates:
        update_many(updates)
    return {"status": "ok"}


@router.post("/audio/test/output")
async def audio_test_output(body: dict[str, str]) -> dict[str, Any]:
    """Play a short test tone on the selected output device."""
    device = body.get("device", "default")
    loop = asyncio.get_running_loop()

    def _play():
        is_pulse = device and not device.startswith("hw:") and device != "default"
        if is_pulse:
            cmd = ["paplay", "--device=" + device, "/usr/share/sounds/alsa/Front_Center.wav"]
        else:
            cmd = ["speaker-test", "-t", "wav", "-c", "2", "-l", "1"]
            if device and device != "default":
                cmd += ["-D", device]
        subprocess.run(cmd, timeout=6, capture_output=True)

    try:
        await asyncio.wait_for(loop.run_in_executor(None, _play), timeout=8)
        return {"status": "ok"}
    except Exception as exc:
        logger.warning("Output test failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/audio/test/input")
async def audio_test_input(body: dict[str, str]) -> dict[str, Any]:
    """Record 3s from input device, measure peak, then play back on output device."""
    input_device = body.get("device", "default")
    output_device = body.get("output_device", "default")
    loop = asyncio.get_running_loop()

    def _record_and_playback() -> dict:
        import struct
        import tempfile
        import wave

        # --- Record ---
        is_pulse_in = input_device and not input_device.startswith("hw:") and input_device != "default"
        if is_pulse_in:
            rec_cmd = ["timeout", "3",
                       "parecord", "--raw", "--format=s16le", "--rate=16000",
                       "--channels=1", "--device=" + input_device]
        else:
            rec_cmd = ["arecord", "-d", "3", "-f", "S16_LE", "-r", "16000", "-c", "1", "-t", "raw"]
            if input_device and input_device != "default":
                rec_cmd += ["-D", input_device]

        result = subprocess.run(rec_cmd, timeout=6, capture_output=True)
        if result.returncode != 0 and not result.stdout:
            raise RuntimeError(result.stderr.decode(errors="replace"))

        raw = result.stdout
        if len(raw) < 2:
            return {"peak_level": 0.0}

        samples = struct.unpack(f"<{len(raw) // 2}h", raw)
        peak = max(abs(s) for s in samples) if samples else 0

        # --- Write temp WAV ---
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        try:
            with wave.open(tmp.name, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(16000)
                wf.writeframes(raw)

            # --- Playback on output device ---
            is_pulse_out = output_device and not output_device.startswith("hw:") and output_device != "default"
            if is_pulse_out:
                play_cmd = ["paplay", "--device=" + output_device, tmp.name]
            else:
                play_cmd = ["aplay"]
                if output_device and output_device != "default":
                    play_cmd += ["-D", output_device]
                play_cmd.append(tmp.name)
            subprocess.run(play_cmd, timeout=6, capture_output=True)
        finally:
            os.unlink(tmp.name)

        return {"peak_level": round(peak / 32768.0, 4)}

    try:
        data = await asyncio.wait_for(loop.run_in_executor(None, _record_and_playback), timeout=15)
        return {"status": "ok", **data}
    except Exception as exc:
        logger.warning("Input test failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ================================================================== #
#  Audio Volume / Mic Gain                                             #
# ================================================================== #


@router.get("/audio/levels")
async def audio_levels() -> dict[str, Any]:
    """Get current output volume and input gain from config."""
    cfg = read_config().get("voice", {}) or {}
    return {
        "output_volume": cfg.get("output_volume", 100),
        "input_gain": cfg.get("input_gain", 100),
    }


@router.post("/audio/levels")
async def audio_set_levels(body: dict[str, Any]) -> dict[str, Any]:
    """Set output volume and/or input gain. Persists to config + applies via pactl."""
    loop = asyncio.get_running_loop()
    cfg = read_config().get("voice", {}) or {}

    out_vol = body.get("output_volume")
    in_gain = body.get("input_gain")

    if out_vol is not None:
        out_vol = max(0, min(150, int(out_vol)))
        update_config("voice", "output_volume", out_vol)
        # Apply via pactl to the configured output
        out_device = cfg.get("audio_force_output")
        if out_device:
            def _set_vol():
                subprocess.run(
                    ["pactl", "set-sink-volume", out_device, f"{out_vol}%"],
                    timeout=3, capture_output=True,
                    env=_pulse_env(),
                )
            await loop.run_in_executor(None, _set_vol)

    if in_gain is not None:
        in_gain = max(0, min(150, int(in_gain)))
        update_config("voice", "input_gain", in_gain)
        # Apply via pactl to the configured input
        in_device = cfg.get("audio_force_input")
        if in_device:
            def _set_gain():
                subprocess.run(
                    ["pactl", "set-source-volume", in_device, f"{in_gain}%"],
                    timeout=3, capture_output=True,
                    env=_pulse_env(),
                )
            await loop.run_in_executor(None, _set_gain)

    return {"status": "ok", "output_volume": out_vol, "input_gain": in_gain}


def _pulse_env() -> dict[str, str]:
    """Return environment dict for pactl to reach PulseAudio."""
    import glob as _glob
    env = os.environ.copy()
    if env.get("PULSE_SERVER") or env.get("PULSE_RUNTIME_PATH"):
        return env
    sockets = sorted(_glob.glob("/run/user/*/pulse/native"))
    if sockets:
        env["PULSE_SERVER"] = f"unix:{sockets[0]}"
    return env


@router.get("/audio/mic-level")
async def audio_mic_level() -> dict[str, Any]:
    """Read current mic level (peak) from a quick 200ms sample."""
    import struct
    cfg = read_config().get("voice", {}) or {}
    input_device = cfg.get("audio_force_input", "default")
    loop = asyncio.get_running_loop()

    def _sample() -> float:
        is_pulse = input_device and not input_device.startswith("hw:") and input_device != "default"
        if is_pulse:
            cmd = ["timeout", "0.3",
                   "parecord", "--raw", "--format=s16le", "--rate=16000",
                   "--channels=1", "--device=" + input_device]
        else:
            cmd = ["arecord", "-d", "1", "-f", "S16_LE", "-r", "16000", "-c", "1", "-t", "raw"]
            if input_device and input_device != "default":
                cmd += ["-D", input_device]
        result = subprocess.run(cmd, timeout=2, capture_output=True)
        raw = result.stdout
        if len(raw) < 2:
            return 0.0
        samples = struct.unpack(f"<{len(raw) // 2}h", raw)
        peak = max(abs(s) for s in samples) if samples else 0
        return round(peak / 32768.0, 4)

    try:
        level = await asyncio.wait_for(loop.run_in_executor(None, _sample), timeout=3)
        return {"level": level}
    except Exception:
        return {"level": 0.0}


# ================================================================== #
#  STT Models (Vosk)                                                   #
# ================================================================== #

@router.get("/stt/models")
async def stt_models() -> dict[str, Any]:
    """List installed Vosk STT models by scanning disk."""
    models_dir = Path(os.environ.get("VOSK_MODELS_DIR", "/var/lib/selena/models/vosk"))
    active_model = get_value("voice", "stt_model", os.environ.get("VOSK_MODEL", "vosk-model-small-uk-v3-small"))

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
    if models_dir.is_dir():
        for d in sorted(models_dir.iterdir()):
            if d.is_dir() and d.name.startswith("vosk-model"):
                size_mb = sum(f.stat().st_size for f in d.rglob("*") if f.is_file()) // (1024 * 1024)
                result.append({
                    "id": d.name,
                    "name": d.name,
                    "installed": True,
                    "active": d.name == active_model,
                    "size_mb": size_mb,
                    "fits_ram": True,
                })

    return {
        "models": result,
        "active": active_model,
        "ram_total_mb": ram_total_mb,
        "ram_available_mb": ram_available_mb,
    }


@router.post("/stt/select")
async def stt_select(req: SelectModelRequest) -> dict[str, Any]:
    """Select and persist Vosk STT model choice, reload engine."""
    update_config("voice", "stt_model", req.model)
    os.environ["VOSK_MODEL"] = req.model
    # Reload STT engine in voice-core module
    try:
        from system_modules.voice_core.stt import reload_stt
        reload_stt(req.model)
    except Exception as exc:
        logger.warning("STT reload failed: %s", exc)
    logger.info("STT model set to %s", req.model)
    return {"status": "ok", "model": req.model}


# ================================================================== #
#  TTS Voices (Piper)                                                  #
# ================================================================== #

@router.get("/tts/voices")
async def tts_voices() -> dict[str, Any]:
    """List installed Piper TTS voices by scanning disk."""
    models_dir = Path(os.environ.get("PIPER_MODELS_DIR", "/var/lib/selena/models/piper"))
    active_voice = get_value("voice", "tts_voice", os.environ.get("PIPER_VOICE", "ru_RU-irina-medium"))

    result = []
    if models_dir.is_dir():
        for f in sorted(models_dir.iterdir()):
            if f.is_file() and f.suffix == ".onnx":
                voice_id = f.stem  # e.g. "uk_UA-ukrainian_tts-medium"
                parts = voice_id.split("-", 1)
                lang = parts[0].split("_")[0] if parts else ""
                size_mb = f.stat().st_size // (1024 * 1024)
                result.append({
                    "id": voice_id,
                    "name": voice_id,
                    "language": lang,
                    "installed": True,
                    "active": voice_id == active_voice,
                    "size_mb": size_mb,
                })

    return {"voices": result, "active": active_voice}


@router.post("/tts/select")
async def tts_select(req: SelectVoiceRequest) -> dict[str, Any]:
    """Select and persist TTS voice."""
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
            # Check why synthesis failed
            if not shutil.which("piper"):
                raise HTTPException(status_code=503, detail="Piper TTS binary not found — install Piper first")
            voice_file = Path(os.environ.get("PIPER_MODELS_DIR", "/var/lib/selena/models/piper")) / f"{req.voice}.onnx"
            if not voice_file.exists():
                raise HTTPException(status_code=503, detail=f"Voice model not found: {req.voice}")
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
    """List installed LLM models from Ollama."""
    try:
        from system_modules.llm_engine.model_manager import get_model_manager
        manager = get_model_manager()
        models = await manager.list_models()
        active = manager.get_active()

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
            config = read_config()
            voice_cfg = config.setdefault("voice", {})
            voice_cfg["llm_model"] = req.model
            provider = voice_cfg.get("llm_provider", "ollama")
            providers = voice_cfg.setdefault("providers", {})
            providers.setdefault(provider, {})["model"] = req.model
            from core.config_writer import write_config
            write_config(config)
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
        asyncio.create_task(_download_model_bg(req.model))
        return {"status": "downloading", "model": req.model}
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



# Note: /network/status is defined earlier in this file (uses nmcli, returns wifi/ethernet/internet).


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


# ================================================================== #
#  Provision — download models & apply configuration                   #
# ================================================================== #

def _build_vosk_download_url(model_id: str) -> str:
    """Construct Vosk model download URL from catalog cache or default."""
    cache_file = Path(os.environ.get("SELENA_CACHE_DIR", "/var/lib/selena/cache")) / "vosk_catalog.json"
    if cache_file.exists():
        try:
            import json as _json
            catalog = _json.loads(cache_file.read_text())
            for m in catalog.get("models", []):
                if m["id"] == model_id:
                    return m.get("url", "")
        except Exception:
            pass
    return f"https://alphacephei.com/vosk/models/{model_id}.zip"


def _build_piper_download_urls(voice_id: str) -> list[str]:
    """Construct Piper voice download URLs from voice ID."""
    parts = voice_id.split("-", 1)
    if len(parts) < 2:
        return []
    locale = parts[0]
    lang = locale.split("_")[0]
    rest_parts = parts[1].rsplit("-", 1)
    name = rest_parts[0] if len(rest_parts) == 2 else parts[1]
    quality = rest_parts[1] if len(rest_parts) == 2 else "medium"
    base = "https://huggingface.co/rhasspy/piper-voices/resolve/main"
    return [
        f"{base}/{lang}/{locale}/{name}/{quality}/{voice_id}.onnx",
        f"{base}/{lang}/{locale}/{name}/{quality}/{voice_id}.onnx.json",
    ]


@router.post("/provision")
async def start_provision() -> dict[str, Any]:
    """Start the provisioning pipeline: download STT model, TTS voice, apply config."""
    if _provision.running:
        return {"status": "already_running", **_provision.to_dict()}

    config = read_config()
    stt_model = config.get("voice", {}).get("stt_model", "vosk-model-small-uk-v3-small")
    tts_voice = config.get("voice", {}).get("tts_voice", "uk_UA-ukrainian_tts-medium")
    llm_model = config.get("llm", {}).get("default_model")

    _provision.reset()
    _provision.running = True

    # Build task list
    tasks: list[dict[str, Any]] = []
    tasks.append({"id": "apply_config", "label": "apply_config", "status": "pending"})

    # Check if STT model needs downloading
    vosk_dir = Path(os.environ.get("VOSK_MODELS_DIR", "/var/lib/selena/models/vosk"))
    if not (vosk_dir / stt_model).is_dir():
        tasks.append({"id": "download_stt", "label": "download_stt", "status": "pending", "model": stt_model})

    # Check if TTS voice needs downloading
    piper_dir = Path(os.environ.get("PIPER_MODELS_DIR", "/var/lib/selena/models/piper"))
    if not (piper_dir / f"{tts_voice}.onnx").exists():
        tasks.append({"id": "download_tts", "label": "download_tts", "status": "pending", "voice": tts_voice})

    # Check if LLM model needs downloading
    if llm_model:
        tasks.append({"id": "download_llm", "label": "download_llm", "status": "pending", "model": llm_model})

    tasks.append({"id": "finalize", "label": "finalize", "status": "pending"})

    _provision.tasks = tasks
    _provision.total = len(tasks)
    _provision.completed = 0

    asyncio.create_task(_run_provision(stt_model, tts_voice, llm_model))
    return {"status": "started", **_provision.to_dict()}


@router.get("/provision/status")
async def provision_status() -> dict[str, Any]:
    """Poll current provisioning progress."""
    return _provision.to_dict()


async def _run_provision(stt_model: str, tts_voice: str, llm_model: str | None) -> None:
    """Execute provisioning tasks sequentially."""
    try:
        for task in _provision.tasks:
            task["status"] = "running"
            _provision.current_task = task["id"]
            logger.info("Provision: starting %s", task["id"])

            try:
                if task["id"] == "apply_config":
                    await _provision_apply_config()
                elif task["id"] == "download_stt":
                    await _provision_download_stt(stt_model)
                elif task["id"] == "download_tts":
                    await _provision_download_tts(tts_voice)
                elif task["id"] == "download_llm":
                    await _provision_download_llm(llm_model or "")
                elif task["id"] == "finalize":
                    await _provision_finalize()

                task["status"] = "done"
                _provision.completed += 1
                logger.info("Provision: completed %s", task["id"])
            except Exception as exc:
                logger.error("Provision task %s failed: %s", task["id"], exc)
                task["status"] = "error"
                task["error"] = str(exc)
                # Non-critical tasks: continue anyway (except finalize)
                _provision.completed += 1

        _provision.done = True
        _provision.running = False
        _provision.current_task = ""
        logger.info("Provision: all tasks completed")

    except Exception as exc:
        logger.error("Provision pipeline failed: %s", exc)
        _provision.failed = True
        _provision.error = str(exc)
        _provision.running = False


async def _provision_apply_config() -> None:
    """Apply saved configuration (timezone, etc.)."""
    config = read_config()
    tz = config.get("system", {}).get("timezone")
    if tz:
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, _apply_timezone_sync, tz)
        except Exception:
            pass
    # Small delay so the user sees the step
    await asyncio.sleep(1)


async def _provision_download_stt(model_id: str) -> None:
    """Download Vosk STT model."""
    import httpx
    import zipfile

    url = _build_vosk_download_url(model_id)
    if not url:
        logger.warning("No download URL for STT model %s, skipping", model_id)
        return

    vosk_dir = Path(os.environ.get("VOSK_MODELS_DIR", "/var/lib/selena/models/vosk"))
    vosk_dir.mkdir(parents=True, exist_ok=True)
    zip_path = vosk_dir / f"{model_id}.zip"

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=30.0)) as client:
            async with client.stream("GET", url, follow_redirects=True) as resp:
                resp.raise_for_status()
                with open(zip_path, "wb") as f:
                    async for chunk in resp.aiter_bytes(chunk_size=65536):
                        f.write(chunk)

        # Extract zip
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _extract_vosk_zip, zip_path, vosk_dir, model_id)
    finally:
        zip_path.unlink(missing_ok=True)


def _extract_vosk_zip(zip_path: Path, dest_dir: Path, model_id: str) -> None:
    """Extract Vosk model zip and rename to expected directory name."""
    import zipfile
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(dest_dir)

    # Vosk zips often have a root folder with a different name — rename to model_id
    extracted_dirs = [d for d in dest_dir.iterdir() if d.is_dir() and d.name != model_id]
    target = dest_dir / model_id
    if not target.exists() and extracted_dirs:
        # Find the most likely match
        for d in extracted_dirs:
            if model_id.replace("-", "") in d.name.replace("-", "") or d.name.startswith("vosk-model"):
                d.rename(target)
                break


async def _provision_download_tts(voice_id: str) -> None:
    """Download Piper TTS voice model (.onnx + .onnx.json)."""
    import httpx

    urls = _build_piper_download_urls(voice_id)
    if not urls:
        logger.warning("No download URL for TTS voice %s, skipping", voice_id)
        return

    piper_dir = Path(os.environ.get("PIPER_MODELS_DIR", "/var/lib/selena/models/piper"))
    piper_dir.mkdir(parents=True, exist_ok=True)

    async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=30.0)) as client:
        for url in urls:
            filename = url.rsplit("/", 1)[-1]
            dest = piper_dir / filename
            if dest.exists():
                continue
            async with client.stream("GET", url, follow_redirects=True) as resp:
                resp.raise_for_status()
                with open(dest, "wb") as f:
                    async for chunk in resp.aiter_bytes(chunk_size=65536):
                        f.write(chunk)
    logger.info("TTS voice %s downloaded to %s", voice_id, piper_dir)


async def _provision_download_llm(model_id: str) -> None:
    """Download LLM model via Ollama pull."""
    if not model_id:
        return
    try:
        from system_modules.llm_engine.model_manager import get_model_manager
        manager = get_model_manager()
        await manager.download(model_id)
    except Exception as exc:
        logger.warning("LLM model download failed (non-critical): %s", exc)


async def _provision_finalize() -> None:
    """Mark wizard as completed in config."""
    update_many([
        ("wizard", "completed", True),
        ("wizard", "provisioned", True),
    ])
    await asyncio.sleep(0.5)
