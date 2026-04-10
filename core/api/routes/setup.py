"""
core/api/routes/setup.py — Real device setup & configuration API endpoints.

Endpoints for:
  - WiFi scanning & connection (nmcli)
  - Audio device detection
  - STT status (Vosk) — model management in core/api/routes/vosk.py
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

from core.config_writer import get_nested, get_value, read_config, update_config, update_many


def _piper_models_dir() -> Path:
    return Path(
        os.environ.get(
            "PIPER_MODELS_DIR",
            str(get_nested("voice.tts.models_dir", "/var/lib/selena/models/piper")),
        )
    )


def _piper_gpu_url() -> str:
    return os.environ.get(
        "PIPER_GPU_URL",
        str(get_nested("voice.tts.server_url", "http://localhost:5100")),
    )

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
    """Scan for available Wi-Fi networks via nmcli (or iw fallback)."""
    if not _nmcli_available():
        return {
            "networks": [],
            "available": False,
            "message": "NetworkManager (nmcli) is not available. Configure Wi-Fi externally.",
        }

    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(None, _scan_wifi_sync)
        if result:
            return {"networks": result, "available": True, "message": ""}
        # nmcli returned success but zero networks — typical for
        # unmanaged interfaces (DietPi / ifupdown).  Fall through.
        logger.info("nmcli scan returned empty — trying iw fallback")
    except Exception as exc:
        logger.warning("WiFi scan via nmcli failed: %s — trying iw fallback", exc)

    # Fallback: iw dev wlan0 scan in host namespace (works regardless
    # of whether NM manages the interface).
    try:
        result = await loop.run_in_executor(None, _scan_wifi_iw_fallback)
        return {"networks": result, "available": True, "message": ""}
    except Exception as exc2:
        logger.warning("WiFi iw fallback scan also failed: %s", exc2)
        return {"networks": [], "available": True, "message": str(exc2)}


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
                        val = line.split(":", 1)[-1].strip()
                        if val and val != "--":
                            ssid = val
                        break
        except Exception:
            pass
    # Fallback for unmanaged/ifupdown WiFi (DietPi): get SSID and IP
    # via host namespace tools when NM has no info.
    if not ssid:
        ssid = _get_wlan_ssid_fallback() or ""
    if ip == "unknown":
        # Inside Docker the socket trick connects via the bridge, not
        # wlan0.  Try reading wlan0 IP in the host network namespace.
        try:
            proc = _host_cmd(["ip", "-4", "-o", "addr", "show", "wlan0"])
            if proc.returncode == 0:
                match = re.search(r"inet\s+(\d+\.\d+\.\d+\.\d+)", proc.stdout)
                if match:
                    ip = match.group(1)
        except Exception:
            pass
    connected = ip != "unknown" or bool(ssid)
    return {"connected": connected, "ssid": ssid, "ip": ip}


@router.get("/wifi/enabled")
async def wifi_enabled() -> dict[str, Any]:
    """Check if the WiFi adapter is enabled."""
    if not _nmcli_available():
        return {"enabled": False, "adapter_found": False}
    try:
        proc = subprocess.run(
            ["nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device"],
            capture_output=True, text=True, timeout=5,
        )
        for line in proc.stdout.strip().splitlines():
            parts = line.split(":")
            if len(parts) >= 3 and parts[1] == "wifi":
                dev = parts[0]
                state = parts[2].strip()
                if state == "unmanaged":
                    # DietPi / ifupdown: NM does not manage the
                    # interface but wpa_supplicant may still have it
                    # connected.  Report enabled if it has an IP.
                    has_ip = _get_interface_ip(dev) is not None
                    return {
                        "enabled": has_ip,
                        "adapter_found": True,
                        "state": state,
                        "unmanaged": True,
                    }
                return {
                    "enabled": state != "unavailable",
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

    # Check internet connectivity (try locally first, then via host
    # namespace — inside Docker the bridge may not route to WAN).
    try:
        proc = subprocess.run(
            ["ping", "-c", "1", "-W", "2", "8.8.8.8"],
            capture_output=True, timeout=5,
        )
        result["internet"] = proc.returncode == 0
    except Exception:
        pass
    if not result["internet"]:
        try:
            proc = _host_cmd(["ping", "-c", "1", "-W", "2", "8.8.8.8"])
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
                if state == "connected":
                    result["wifi"]["enabled"] = True
                    result["wifi"]["connected"] = True
                    result["wifi"]["ssid"] = conn
                    result["wifi"]["ip"] = _get_interface_ip(dev)
                elif state == "unmanaged":
                    # DietPi / ifupdown-managed WiFi: NM doesn't control
                    # the interface but it may still be connected via
                    # wpa_supplicant.  Detect actual link state.
                    wifi_ip = _get_interface_ip(dev)
                    if wifi_ip:
                        result["wifi"]["enabled"] = True
                        result["wifi"]["connected"] = True
                        result["wifi"]["ip"] = wifi_ip
                        result["wifi"]["ssid"] = _get_wlan_ssid_fallback() or ""
                        result["wifi"]["unmanaged"] = True
                    else:
                        result["wifi"]["enabled"] = False
                else:
                    result["wifi"]["enabled"] = state != "unavailable"
    except Exception as exc:
        logger.warning("Network status check failed: %s", exc)

    return result


def _get_interface_ip(interface: str) -> str | None:
    """Get IP address of a specific network interface.

    Tries locally first, then falls back to the host network namespace
    via nsenter (needed when running inside Docker for host interfaces
    like wlan0 / eth0).
    """
    try:
        proc = subprocess.run(
            ["ip", "-4", "-o", "addr", "show", interface],
            capture_output=True, text=True, timeout=5,
        )
        match = re.search(r"inet\s+(\d+\.\d+\.\d+\.\d+)", proc.stdout)
        if match:
            return match.group(1)
    except Exception:
        pass
    # Fallback: host namespace (Docker container can't see host wlan0)
    try:
        proc = _host_cmd(["ip", "-4", "-o", "addr", "show", interface])
        match = re.search(r"inet\s+(\d+\.\d+\.\d+\.\d+)", proc.stdout)
        return match.group(1) if match else None
    except Exception:
        return None


def _host_cmd(cmd: list[str], timeout: int = 10) -> subprocess.CompletedProcess[str]:
    """Run a command in the host namespaces via nsenter.

    Inside a Docker container the wireless interface lives in the host
    network/mount namespace.  ``nsenter --target 1 --net --mount`` lets
    us reach ``iw`` / ``wpa_cli`` / ``iw scan`` that are installed on the
    host but invisible from the container's own rootfs.
    """
    full = ["nsenter", "--target", "1", "--net", "--mount", "--"] + cmd
    return subprocess.run(full, capture_output=True, text=True, timeout=timeout)


def _get_wlan_ssid_fallback() -> str | None:
    """Get current WiFi SSID via iw/wpa_cli when NM does not manage wlan0.

    Commands run in the host namespace (via nsenter) so they work both
    on bare metal and inside the Docker container.
    """
    # Try iw first
    try:
        proc = _host_cmd(["iw", "dev", "wlan0", "link"])
        if proc.returncode == 0:
            match = re.search(r"SSID:\s*(.+)", proc.stdout)
            if match:
                return match.group(1).strip()
    except Exception:
        pass
    # Fallback to wpa_cli
    try:
        proc = _host_cmd(["wpa_cli", "-i", "wlan0", "status"])
        if proc.returncode == 0:
            for line in proc.stdout.splitlines():
                if line.startswith("ssid="):
                    return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return None


def _scan_wifi_iw_fallback() -> list[dict[str, Any]]:
    """Scan WiFi networks via iw when nmcli cannot (unmanaged interface).

    Runs ``iw dev wlan0 scan`` in the host namespace via nsenter.
    """
    proc = _host_cmd(["iw", "dev", "wlan0", "scan"], timeout=20)
    if proc.returncode != 0:
        raise RuntimeError(f"iw scan failed: {proc.stderr.strip()}")

    current_ssid = _get_wlan_ssid_fallback()
    networks: dict[str, dict[str, Any]] = {}
    ssid = ""
    signal = 0
    security = ""

    for line in proc.stdout.splitlines():
        line = line.strip()
        if line.startswith("BSS ") and "(" in line:
            # Save previous entry
            if ssid:
                if ssid not in networks or signal > networks[ssid]["signal"]:
                    networks[ssid] = {
                        "ssid": ssid,
                        "signal": min(100, max(0, signal + 100)),  # dBm→%
                        "security": security,
                        "connected": ssid == current_ssid,
                    }
            ssid, signal, security = "", 0, ""
        elif line.startswith("SSID:"):
            ssid = line.split(":", 1)[1].strip()
        elif line.startswith("signal:"):
            try:
                signal = int(float(line.split(":")[1].strip().split()[0]))
            except (ValueError, IndexError):
                pass
        elif "WPA" in line or "RSN" in line:
            security = "WPA2" if "RSN" in line else "WPA"

    # Last entry
    if ssid:
        if ssid not in networks or signal > networks[ssid]["signal"]:
            networks[ssid] = {
                "ssid": ssid,
                "signal": min(100, max(0, signal + 100)),
                "security": security,
                "connected": ssid == current_ssid,
            }

    return sorted(networks.values(), key=lambda n: n["signal"], reverse=True)


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
    """Test speakers via Piper TTS with current software volume."""
    device = body.get("device", "default")

    test_text = "sound check. left channel. right channel. volume test complete."

    loop = asyncio.get_running_loop()

    try:
        # Synthesize via Piper HTTP server (GPU, on host)
        import httpx as _httpx
        gpu_url = _piper_gpu_url()

        voice = ""
        try:
            voice = read_config().get("voice", {}).get("tts", {}).get("fallback", {}).get("voice", "")
        except Exception:
            pass

        async with _httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(f"{gpu_url}/synthesize", json={
                "text": test_text, "voice": voice,
            })
            if resp.status_code != 200 or not resp.content:
                raise RuntimeError("Piper returned empty")
            wav_data = resp.content

        # Extract raw PCM and sample rate from WAV
        import io, wave, struct
        with wave.open(io.BytesIO(wav_data), "rb") as wf:
            sample_rate = wf.getframerate()
            pcm_data = wf.readframes(wf.getnframes())

        # Apply software volume
        vol_cfg = read_config().get("voice", {}).get("output_volume", 100)
        volume = max(0.0, min(1.5, int(vol_cfg) / 100.0))

        if abs(volume - 1.0) > 0.01 and len(pcm_data) > 2:
            n = len(pcm_data) // 2
            samples = struct.unpack(f"<{n}h", pcm_data[:n * 2])
            pcm_data = struct.pack(f"<{n}h", *(
                max(-32768, min(32767, int(s * volume))) for s in samples
            ))

        def _play():
            cmd = ["aplay", "-t", "raw", "-f", "S16_LE", "-r", str(sample_rate), "-c", "1"]
            if device and device != "default":
                cmd += ["-D", device]
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            proc.stdin.write(pcm_data)
            proc.stdin.close()
            proc.wait(timeout=15)

        await asyncio.wait_for(loop.run_in_executor(None, _play), timeout=18)
        return {"status": "ok"}

    except Exception as exc:
        logger.warning("Output test failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


def _pause_voice_loop() -> None:
    """Pause voice loop to release mic device for testing.

    Sets _mic_test_active flag and kills running arecord to free the device.
    """
    try:
        from core.module_loader.sandbox import get_sandbox
        vc = get_sandbox().get_in_process_module("voice-core")
        if vc and hasattr(vc, "_mic_test_active"):
            vc._mic_test_active = True
            # Kill the running arecord process to release ALSA device
            proc = getattr(vc, "_arecord_proc", None)
            if proc and proc.poll() is None:
                proc.kill()
                try:
                    proc.wait(timeout=2)
                except Exception:
                    pass
            import time
            time.sleep(0.3)
    except Exception:
        pass


def _resume_voice_loop() -> None:
    """Resume voice loop after mic test."""
    try:
        from core.module_loader.sandbox import get_sandbox
        vc = get_sandbox().get_in_process_module("voice-core")
        if vc and hasattr(vc, "_mic_test_active"):
            vc._mic_test_active = False
    except Exception:
        pass


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

        # Pause voice loop to release mic
        _pause_voice_loop()

        try:
            # --- Record via arecord (ALSA direct) ---
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

                # --- Playback on output device via aplay ---
                play_cmd = ["aplay"]
                if output_device and output_device != "default":
                    play_cmd += ["-D", output_device]
                play_cmd.append(tmp.name)
                subprocess.run(play_cmd, timeout=6, capture_output=True)
            finally:
                os.unlink(tmp.name)

            return {"peak_level": round(peak / 32768.0, 4)}
        finally:
            _resume_voice_loop()

    try:
        data = await asyncio.wait_for(loop.run_in_executor(None, _record_and_playback), timeout=20)
        return {"status": "ok", **data}
    except Exception as exc:
        _resume_voice_loop()
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


def _card_index_from_device(device_id: str) -> int | None:
    """Extract card index from ALSA device id like 'plughw:1,3' or 'hw:0,0'."""
    m = re.match(r"(?:plug)?hw:(\d+)", device_id or "")
    return int(m.group(1)) if m else None


def _find_volume_control(card: int, direction: str = "playback") -> str | None:
    """Find the first usable volume control on the given ALSA card.

    *direction* is ``"playback"`` or ``"capture"``.
    Returns the simple mixer control name or ``None``.
    """
    try:
        result = subprocess.run(
            ["amixer", "-c", str(card), "scontrols"],
            capture_output=True, text=True, timeout=3,
        )
    except Exception:
        return None

    for line in result.stdout.splitlines():
        # "Simple mixer control 'Mic',0"
        m = re.match(r"Simple mixer control '(.+?)',", line)
        if not m:
            continue
        name = m.group(1)
        # Check if this control has the right capability
        try:
            info = subprocess.run(
                ["amixer", "-c", str(card), "sget", name],
                capture_output=True, text=True, timeout=3,
            )
            caps = info.stdout.lower()
            if direction == "capture" and "cvolume" in caps:
                return name
            if direction == "playback" and "pvolume" in caps:
                return name
        except Exception:
            continue
    return None


def _apply_alsa_volume(card: int, control: str, pct: int) -> None:
    """Set an ALSA mixer control to *pct* percent."""
    subprocess.run(
        ["amixer", "-c", str(card), "sset", control, f"{pct}%"],
        timeout=3, capture_output=True,
    )


@router.post("/audio/levels")
async def audio_set_levels(body: dict[str, Any]) -> dict[str, Any]:
    """Set output volume and/or input gain. Persists to config + applies via amixer."""
    loop = asyncio.get_running_loop()

    out_vol = body.get("output_volume")
    in_gain = body.get("input_gain")

    if out_vol is not None:
        out_vol = max(0, min(150, int(out_vol)))
        update_config("voice", "output_volume", out_vol)
        # Re-read config after save to get current device selection
        out_device = read_config().get("voice", {}).get("audio_force_output")
        card = _card_index_from_device(out_device)
        if card is not None:
            ctrl = _find_volume_control(card, "playback")
            if ctrl:
                await loop.run_in_executor(None, _apply_alsa_volume, card, ctrl, out_vol)
            else:
                logger.info("No playback volume control on card %d (HDMI) — volume stored in config", card)

    if in_gain is not None:
        in_gain = max(0, min(150, int(in_gain)))
        update_config("voice", "input_gain", in_gain)
        in_device = read_config().get("voice", {}).get("audio_force_input")
        card = _card_index_from_device(in_device)
        if card is not None:
            ctrl = _find_volume_control(card, "capture")
            if ctrl:
                await loop.run_in_executor(None, _apply_alsa_volume, card, ctrl, in_gain)

    return {"status": "ok", "output_volume": out_vol, "input_gain": in_gain}


@router.get("/audio/mic-level")
async def audio_mic_level() -> dict[str, Any]:
    """Read current mic level from voice_core's live audio stream (no mic lock)."""
    try:
        from core.module_loader.sandbox import get_sandbox
        vc = get_sandbox().get_in_process_module("voice-core")
        if vc:
            energy = getattr(vc, "_last_energy", 0.0)
            return {"level": round(min(1.0, energy / 5000.0), 4)}
    except Exception:
        pass
    return {"level": 0.0}


# ================================================================== #
#  Audio Sources — per-module volume sliders                           #
# ================================================================== #


@router.get("/audio/sources")
async def audio_sources() -> dict[str, Any]:
    """Return audio source modules with their current volumes.

    Uses AudioMixerService if available, falls back to manual discovery.
    """
    try:
        from core.audio_mixer import get_mixer
        mixer = get_mixer()
        if mixer.is_initialized():
            return {"sources": mixer.get_sources()}
    except Exception:
        pass

    # Fallback: manual discovery
    sources: list[dict[str, Any]] = []
    try:
        from core.module_loader.sandbox import get_sandbox
        sandbox = get_sandbox()

        for mod_info in sandbox.list_modules():
            if mod_info.status != "RUNNING":
                continue
            manifest = mod_info.manifest or {}
            intents = manifest.get("intents", [])
            has_audio = any(
                i.startswith("media.") for i in intents
            ) if isinstance(intents, list) else False

            if not has_audio:
                continue

            instance = sandbox.get_in_process_module(mod_info.name)
            volume = read_config().get("voice", {}).get(f"media_volume_{mod_info.name}", 70)
            if instance:
                player = getattr(instance, "_player", None)
                if player:
                    runtime_vol = getattr(player, "_volume", None)
                    if runtime_vol is not None:
                        volume = runtime_vol

            display_name = mod_info.name.replace("-", " ").replace("_", " ").title()
            sources.append({
                "module": mod_info.name,
                "name": display_name,
                "volume": volume,
                "icon": manifest.get("ui", {}).get("icon", ""),
            })
    except Exception as exc:
        logger.warning("audio/sources error: %s", exc)

    tts_vol = read_config().get("voice", {}).get("output_volume", 100)
    sources.insert(0, {
        "module": "voice-core",
        "name": "Selena TTS",
        "volume": tts_vol,
        "icon": "",
    })

    return {"sources": sources}


@router.post("/audio/sources/volume")
async def audio_source_volume(body: dict[str, Any]) -> dict[str, Any]:
    """Set volume for a specific audio source module."""
    module = body.get("module", "")
    volume = max(0, min(150, int(body.get("volume", 70))))

    # Route through mixer if available
    try:
        from core.audio_mixer import get_mixer
        mixer = get_mixer()
        if mixer.is_initialized():
            source_name = "tts" if module == "voice-core" else module
            mixer.set_source_volume(source_name, volume)
            return {"status": "ok", "module": module, "volume": volume}
    except Exception:
        pass

    # Fallback: direct config + module control
    if module == "voice-core":
        update_config("voice", "output_volume", volume)
        return {"status": "ok", "module": module, "volume": volume}

    update_config("voice", f"media_volume_{module}", volume)

    try:
        from core.module_loader.sandbox import get_sandbox
        instance = get_sandbox().get_in_process_module(module)
        if instance:
            player = getattr(instance, "_player", None)
            if player and hasattr(player, "set_volume"):
                await player.set_volume(volume)
                return {"status": "ok", "module": module, "volume": volume}
    except Exception as exc:
        logger.warning("audio/sources/volume error: %s", exc)

    raise HTTPException(status_code=404, detail=f"Module '{module}' not found or has no volume control")


# ================================================================== #
#  STT (Vosk) — status and settings                                    #
#  Full model management API is in core/api/routes/vosk.py             #
# ================================================================== #

@router.get("/stt/status")
async def stt_status() -> dict[str, Any]:
    """Check STT provider status (Vosk)."""
    provider_name = "none"
    ready = False
    lang = "en"
    model = ""

    try:
        from core.module_loader.sandbox import get_sandbox
        vc = get_sandbox().get_in_process_module("voice-core")
        if vc and hasattr(vc, "_stt_provider"):
            p = vc._stt_provider
            if p and hasattr(p, "status"):
                st = p.status()
                provider_name = "vosk"
                ready = st.get("ready", False)
                lang = st.get("lang", "en")
                model = st.get("model_path", "")
            elif p:
                provider_name = type(p).__name__
    except Exception:
        pass

    return {
        "available": ready,
        "provider": provider_name,
        "lang": lang,
        "model": model,
    }


# ================================================================== #
#  TTS Voices (Piper)                                                  #
# ================================================================== #

@router.get("/tts/voices")
async def tts_voices() -> dict[str, Any]:
    """List installed Piper TTS voices by scanning disk."""
    models_dir = _piper_models_dir()
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


# ── Online Piper voice catalog (Hugging Face) ─────────────────────── #

_PIPER_CATALOG_URL = "https://huggingface.co/rhasspy/piper-voices/raw/main/voices.json"
_PIPER_CATALOG_CACHE = "/var/lib/selena/piper_catalog_cache.json"
_PIPER_CACHE_MAX_AGE_DAYS = 14


async def _load_piper_catalog() -> list[dict[str, Any]] | None:
    """Fetch + cache the Piper voices.json catalog from Hugging Face.

    Returns a normalized list of voice dicts:
        { id, name, lang, lang_label, country, quality, size_mb, num_speakers }
    """
    import json
    import time
    cache_path = Path(_PIPER_CATALOG_CACHE)

    cached: dict[str, Any] | None = None
    if cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text())
            age_days = (time.time() - cached.get("_ts", 0)) / 86400
            if age_days < _PIPER_CACHE_MAX_AGE_DAYS:
                return cached.get("voices") or []
        except Exception:
            cached = None

    try:
        import httpx
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(_PIPER_CATALOG_URL)
            resp.raise_for_status()
            raw = resp.json()
    except Exception as exc:
        logger.warning("Piper catalog fetch failed: %s", exc)
        # Fall back to stale cache rather than failing the wizard outright.
        return (cached or {}).get("voices") if cached else None

    voices: list[dict[str, Any]] = []
    for key, v in raw.items():
        if not isinstance(v, dict):
            continue
        lang_info = v.get("language") or {}
        files = v.get("files") or {}
        size_bytes = 0
        for fname, finfo in files.items():
            if fname.endswith(".onnx") and isinstance(finfo, dict):
                size_bytes = int(finfo.get("size_bytes") or 0)
                break
        voices.append({
            "id": key,
            "name": v.get("name") or key,
            "lang": (lang_info.get("family") or "").lower(),
            "locale": lang_info.get("code") or "",
            "lang_label": lang_info.get("name_native") or lang_info.get("name_english") or "",
            "country": lang_info.get("country_english") or "",
            "quality": v.get("quality") or "",
            "size_mb": round(size_bytes / (1024 * 1024)) if size_bytes else 0,
            "num_speakers": int(v.get("num_speakers") or 1),
        })

    voices.sort(key=lambda x: (x["lang"], x["name"], x["quality"]))
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps({"_ts": time.time(), "voices": voices}))
    except Exception as exc:
        logger.debug("Could not write Piper catalog cache: %s", exc)
    return voices


@router.get("/tts/catalog")
async def tts_catalog(
    lang: str = "",
    quality: str = "",
    q: str = "",
    page: int = 1,
    per_page: int = 20,
) -> dict[str, Any]:
    """Online Piper voice catalog with pagination + language/quality filters.

    Source: https://huggingface.co/rhasspy/piper-voices/raw/main/voices.json
    Cached at /var/lib/selena/piper_catalog_cache.json for 14 days.
    """
    if page < 1:
        page = 1
    per_page = max(1, min(per_page, 100))

    voices = await _load_piper_catalog()
    if voices is None:
        raise HTTPException(
            status_code=503,
            detail="Piper voice catalog unavailable (no cache, no internet)",
        )

    # Mark installed/active by scanning models_dir
    models_dir = _piper_models_dir()
    installed_ids: set[str] = set()
    if models_dir.is_dir():
        for f in models_dir.iterdir():
            if f.is_file() and f.suffix == ".onnx":
                installed_ids.add(f.stem)

    active_voice = (
        get_nested("voice.tts.primary.voice")
        or get_value("voice", "tts_voice", "")
        or os.environ.get("PIPER_VOICE", "")
    )

    for v in voices:
        v["installed"] = v["id"] in installed_ids
        v["active"] = v["id"] == active_voice

    # Language facets across the FULL catalog (before filtering)
    lang_counts: dict[str, dict[str, Any]] = {}
    for v in voices:
        code = v["lang"]
        if not code:
            continue
        node = lang_counts.setdefault(code, {"code": code, "label": v["lang_label"] or code.upper(), "count": 0})
        node["count"] += 1
    languages = sorted(lang_counts.values(), key=lambda x: (-x["count"], x["code"]))

    # Apply filters
    filtered = voices
    if lang:
        lang_l = lang.lower()
        filtered = [v for v in filtered if v["lang"] == lang_l]
    if quality:
        q_l = quality.lower()
        filtered = [v for v in filtered if v["quality"].lower() == q_l]
    if q:
        q_l = q.lower()
        filtered = [
            v for v in filtered
            if q_l in v["id"].lower()
            or q_l in v["name"].lower()
            or q_l in v["country"].lower()
        ]

    total = len(filtered)
    start = (page - 1) * per_page
    end = start + per_page
    return {
        "voices": filtered[start:end],
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": (total + per_page - 1) // per_page if per_page else 1,
        "languages": languages,
        "qualities": ["x_low", "low", "medium", "high"],
    }


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
            voice_file = _piper_models_dir() / f"{req.voice}.onnx"
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
#  Intent Patterns — entity regeneration                              #
# ================================================================== #

@router.post("/patterns/regenerate")
async def patterns_regenerate(entity_type: str | None = None) -> dict[str, Any]:
    """Regenerate auto_entity intent patterns for radios/devices/scenes.

    PatternGenerator wipes existing source='auto_entity' rows and rebuilds
    them via the hardcoded English LLM prompt. Hot-reloads IntentCompiler.

    Optional ?entity_type=radio_station|device|scene narrows the rebuild.
    """
    try:
        from system_modules.llm_engine.pattern_generator import get_pattern_generator
        gen = get_pattern_generator()
        count = await gen.regenerate_all(entity_type)
        return {"status": "ok", "count": count, "entity_type": entity_type or "all"}
    except Exception as exc:
        logger.error("Pattern regeneration failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ================================================================== #
#  LLM Models (Ollama)                                                 #
# ================================================================== #

@router.get("/llm/models")
async def llm_models() -> dict[str, Any]:
    """List installed LLM models for the active provider."""
    try:
        from system_modules.llm_engine.model_manager import get_model_manager
        manager = get_model_manager()
        provider = manager.get_provider()
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
            "ollama_available": provider == "ollama",
            "provider": provider,
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
    """Check LLM availability and current model."""
    try:
        from system_modules.llm_engine.model_manager import get_model_manager
        manager = get_model_manager()
        provider = manager.get_provider()

        if provider != "ollama":
            return {
                "available": True,
                "active_model": manager.get_active(),
                "installed_models": [],
                "provider": provider,
            }

        from system_modules.llm_engine.ollama_client import get_ollama_client
        client = get_ollama_client()
        is_available = await client.is_available()
        installed = await client.list_models() if is_available else []

        return {
            "available": is_available,
            "active_model": manager.get_active(),
            "installed_models": installed,
            "provider": "ollama",
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


# ================================================================== #
#  Translation (Argos Translate — offline language pairs)               #
# ================================================================== #


@router.get("/translate/status")
async def translate_status() -> dict[str, Any]:
    """Translation status: active language, settings."""
    from core.translation.local_translator import get_input_translator, get_output_translator
    return {
        "enabled": get_nested("translation.enabled", False),
        "fallback_to_llm": get_nested("translation.fallback_to_llm", True),
        "active_lang": get_nested("translation.active_lang", ""),
        "input_available": get_input_translator().is_available(),
        "output_available": get_output_translator().is_available(),
    }


@router.get("/translate/catalog")
async def translate_catalog() -> dict[str, Any]:
    """List available translation language pairs (XX↔EN)."""
    from core.translation.downloader import get_catalog
    loop = asyncio.get_event_loop()
    catalog = await loop.run_in_executor(None, get_catalog)
    return {"models": catalog}


@router.post("/translate/download")
async def translate_download(req: dict[str, Any]) -> dict[str, Any]:
    """Install both directions for a language. Body: {"lang": "uk"}"""
    from core.translation.downloader import install_pair, get_download_status
    lang = req.get("lang", "")
    if not lang:
        raise HTTPException(status_code=422, detail="lang is required")
    st = get_download_status()
    if st["active"]:
        return {"status": "already_downloading", **st}
    asyncio.create_task(install_pair(lang))
    return {"status": "started", "lang": lang}


@router.get("/translate/download/status")
async def translate_download_status() -> dict[str, Any]:
    """Poll download progress."""
    from core.translation.downloader import get_download_status
    return get_download_status()


@router.post("/translate/activate")
async def translate_activate(req: dict[str, Any]) -> dict[str, Any]:
    """Activate a language pair. Body: {"lang": "uk"}"""
    from core.translation.downloader import activate_lang
    lang = req.get("lang", "")
    if not lang:
        raise HTTPException(status_code=422, detail="lang is required")
    ok = activate_lang(lang)
    return {"status": "ok", "lang": lang}


@router.delete("/translate/lang/{lang_code}")
async def translate_delete(lang_code: str) -> dict[str, Any]:
    """Delete both directions of a language pair."""
    from core.translation.downloader import delete_pair
    ok = delete_pair(lang_code)
    if not ok:
        raise HTTPException(status_code=400, detail="Cannot delete active pair or not found")
    return {"status": "deleted", "lang": lang_code}


@router.post("/translate/settings")
async def translate_settings(req: dict[str, Any]) -> dict[str, Any]:
    """Update translation settings (enabled, fallback_to_llm)."""
    from core.translation.local_translator import reload_translators
    updates = []
    for k in ("enabled", "fallback_to_llm"):
        if k in req:
            updates.append(("translation", k, bool(req[k])))
    if updates:
        update_many(updates)
        reload_translators()
    return {"status": "ok"}


# ================================================================== #
#  Provisioning pipeline                                               #
# ================================================================== #


@router.post("/provision")
async def start_provision() -> dict[str, Any]:
    """Start the provisioning pipeline: download STT model, TTS voice, apply config."""
    if _provision.running:
        return {"status": "already_running", **_provision.to_dict()}

    config = read_config()
    # Wizard writes vosk active model into stt.vosk.active_model; legacy: voice.stt_model
    stt_model = (
        config.get("stt", {}).get("vosk", {}).get("active_model")
        or config.get("voice", {}).get("stt_model", "")
        or ""
    )
    tts_voice = (
        config.get("voice", {}).get("tts_voice")
        or config.get("voice", {}).get("tts", {}).get("primary", {}).get("voice")
        or "uk_UA-ukrainian_tts-medium"
    )
    llm_model = config.get("llm", {}).get("default_model")

    _provision.reset()
    _provision.running = True

    # Build task list
    tasks: list[dict[str, Any]] = []
    tasks.append({"id": "apply_config", "label": "apply_config", "status": "pending"})

    # Check if STT model needs downloading (new wizard sets stt.vosk.active_model)
    if stt_model and stt_model != "small":
        vosk_dir = Path(get_nested("stt.vosk.models_dir", "/var/lib/selena/models/vosk"))
        if not (vosk_dir / stt_model).is_dir():
            tasks.append({"id": "download_stt", "label": "download_stt", "status": "pending", "model": stt_model})

    # Check if TTS voice needs downloading
    piper_dir = _piper_models_dir()
    if not (piper_dir / f"{tts_voice}.onnx").exists():
        tasks.append({"id": "download_tts", "label": "download_tts", "status": "pending", "voice": tts_voice})

    # Check if LLM model needs downloading
    if llm_model:
        tasks.append({"id": "download_llm", "label": "download_llm", "status": "pending", "model": llm_model})

    # Install systemd units (host-side); skipped silently inside docker if no systemctl
    tasks.append({"id": "install_native_services", "label": "install_native_services", "status": "pending"})

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
                    await _provision_download_stt(stt_model, task)
                elif task["id"] == "download_tts":
                    await _provision_download_tts(tts_voice, task)
                elif task["id"] == "download_llm":
                    await _provision_download_llm(llm_model or "", task)
                elif task["id"] == "install_native_services":
                    await _provision_install_native_services()
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


async def _provision_download_tts(voice_id: str, task: dict[str, Any] | None = None) -> None:
    """Download Piper TTS voice model (.onnx + .onnx.json)."""
    import httpx

    urls = _build_piper_download_urls(voice_id)
    if not urls:
        logger.warning("No download URL for TTS voice %s, skipping", voice_id)
        return

    piper_dir = _piper_models_dir()
    piper_dir.mkdir(parents=True, exist_ok=True)

    # First try to copy from common user-local Piper cache
    if _copy_local_piper_voice(voice_id, piper_dir):
        logger.info("TTS voice %s copied from local cache to %s", voice_id, piper_dir)
        return

    # Pre-compute total size across all files for progress reporting
    total_bytes = 0
    downloaded_bytes = 0
    if task is not None:
        task["progress"] = {"downloaded_bytes": 0, "total_bytes": 0}

    async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=30.0)) as client:
        # Probe total size with HEAD requests
        if task is not None:
            for url in urls:
                filename = url.rsplit("/", 1)[-1]
                if (piper_dir / filename).exists():
                    continue
                try:
                    head = await client.head(url, follow_redirects=True)
                    cl = int(head.headers.get("content-length", 0))
                    total_bytes += cl
                except Exception:
                    pass
            task["progress"]["total_bytes"] = total_bytes

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
                        if task is not None:
                            downloaded_bytes += len(chunk)
                            task["progress"]["downloaded_bytes"] = downloaded_bytes
    logger.info("TTS voice %s downloaded to %s", voice_id, piper_dir)


async def _provision_download_llm(model_id: str, task: dict[str, Any] | None = None) -> None:
    """Download LLM model via Ollama pull.

    Raises on failure so the provisioning runner marks the task as
    ``error`` instead of silently marking it ``done``.
    """
    if not model_id:
        return
    from system_modules.llm_engine.ollama_client import get_ollama_client
    client = get_ollama_client()
    if not await client.is_available():
        raise RuntimeError(
            "Ollama server is not running — install it on the host "
            "(curl -fsSL https://ollama.com/install.sh | sh) and start "
            "with: sudo systemctl enable --now ollama"
        )

    if task is not None:
        task["progress"] = {"downloaded_bytes": 0, "total_bytes": 0}

    def _on_progress(downloaded: int, total: int) -> None:
        if task is not None:
            task["progress"]["downloaded_bytes"] = downloaded
            task["progress"]["total_bytes"] = total

    ok = await client.pull_model(model_id, progress_cb=_on_progress)
    if not ok:
        raise RuntimeError(f"Ollama pull failed for model '{model_id}'")


async def _provision_download_stt(model_id: str, task: dict[str, Any] | None = None) -> None:
    """Download Vosk STT model into configured models_dir AND activate it.

    `model_id` is the alphacephei.com model identifier (e.g. vosk-model-small-en-us-0.15).
    """
    if not model_id or model_id == "small":
        return
    models_dir = Path(
        get_nested("stt.vosk.models_dir", "/var/lib/selena/models/vosk")
    )
    models_dir.mkdir(parents=True, exist_ok=True)
    target_dir = models_dir / model_id
    if not target_dir.is_dir():
        url = f"https://alphacephei.com/vosk/models/{model_id}.zip"
        import httpx
        import zipfile
        import io

        downloaded_bytes = 0
        if task is not None:
            task["progress"] = {"downloaded_bytes": 0, "total_bytes": 0}

        async with httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=30.0)) as client:
            # Probe total size
            if task is not None:
                try:
                    head = await client.head(url, follow_redirects=True)
                    task["progress"]["total_bytes"] = int(
                        head.headers.get("content-length", 0)
                    )
                except Exception:
                    pass

            async with client.stream("GET", url, follow_redirects=True) as resp:
                resp.raise_for_status()
                # Use Content-Length from GET if HEAD didn't work
                if task is not None and task["progress"]["total_bytes"] == 0:
                    task["progress"]["total_bytes"] = int(
                        resp.headers.get("content-length", 0)
                    )
                buf = io.BytesIO()
                async for chunk in resp.aiter_bytes(chunk_size=131072):
                    buf.write(chunk)
                    if task is not None:
                        downloaded_bytes += len(chunk)
                        task["progress"]["downloaded_bytes"] = downloaded_bytes
        buf.seek(0)
        with zipfile.ZipFile(buf) as zf:
            zf.extractall(models_dir)
        logger.info("STT model %s installed to %s", model_id, models_dir)
    else:
        logger.info("STT model %s already on disk, skipping download", model_id)

    # Activate the model on the live voice-core STT provider so the wizard
    # finalize step doesn't leave the engine reporting "not installed".
    try:
        from core.module_loader.sandbox import get_sandbox
        from core.stt.vosk_provider import VoskProvider
        vc = get_sandbox().get_in_process_module("voice-core")
        if vc and hasattr(vc, "_stt_provider"):
            p = vc._stt_provider
            lang = "en"
            name_lower = model_id.lower()
            for code in ("uk", "ru", "en", "de", "fr", "es", "it", "pl"):
                if code in name_lower:
                    lang = code
                    break
            if isinstance(p, VoskProvider):
                await p.reload_model(str(target_dir), lang)
            else:
                from core.stt.factory import create_stt_provider
                vc._stt_provider = create_stt_provider()
            logger.info("STT provider activated with model %s (lang=%s)", model_id, lang)
    except Exception as exc:
        logger.warning("STT provider activation failed (non-fatal): %s", exc)


def _copy_local_piper_voice(voice_id: str, dest_dir: Path) -> bool:
    """Copy voice files from common local Piper caches if available.

    Searched paths (in order):
        ~/.local/share/piper/models/
        ~/.local/share/piper/
        /usr/local/share/piper/
    Returns True if both .onnx and .onnx.json were copied.
    """
    candidates = []
    home = Path(os.path.expanduser("~"))
    candidates.append(home / ".local/share/piper/models")
    candidates.append(home / ".local/share/piper")
    candidates.append(Path("/usr/local/share/piper"))
    # Also search SUDO_USER's home if running under sudo
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user:
        sudo_home = Path(f"/home/{sudo_user}")
        candidates.insert(0, sudo_home / ".local/share/piper/models")
        candidates.insert(1, sudo_home / ".local/share/piper")

    for src_dir in candidates:
        onnx = src_dir / f"{voice_id}.onnx"
        if onnx.exists():
            dest_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(onnx, dest_dir / onnx.name)
            json_file = src_dir / f"{voice_id}.onnx.json"
            if json_file.exists():
                shutil.copy2(json_file, dest_dir / json_file.name)
            return True
    return False


async def _provision_install_native_services() -> None:
    """Install systemd unit files from repo into /etc/systemd/system and enable them.

    Silently no-ops if `systemctl` is unavailable (e.g. running inside a container
    without privileged systemd access). The install.sh bootstrap will have already
    set up the user/group and directories.
    """
    if not shutil.which("systemctl"):
        logger.info("systemctl not available — skipping native service install")
        return
    repo_root = Path(__file__).resolve().parents[3]
    helper = repo_root / "scripts" / "install-systemd.sh"
    if not helper.exists():
        logger.info("scripts/install-systemd.sh not found — skipping")
        return
    try:
        proc = await asyncio.create_subprocess_exec(
            "bash", str(helper),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            logger.warning(
                "install-systemd.sh exited with %s: %s",
                proc.returncode, stderr.decode(errors="ignore"),
            )
        else:
            logger.info("Native systemd units installed")
    except Exception as exc:
        logger.warning("Native service install failed (non-critical): %s", exc)


async def _provision_finalize() -> None:
    """Mark wizard as completed in config."""
    update_many([
        ("wizard", "completed", True),
        ("wizard", "provisioned", True),
        ("system", "initialized", True),
    ])
    await asyncio.sleep(0.5)


# ================================================================== #
#  Provision SSE stream + model catalogs                              #
# ================================================================== #

@router.get("/provision/stream")
async def provision_stream():
    """Server-Sent Events stream of provisioning progress.

    Sends a JSON snapshot every 500ms while running, then closes once
    `done` or `failed` is true.
    """
    from fastapi.responses import StreamingResponse
    import json as _json

    async def _gen():
        last = None
        # If nothing started yet, emit the current snapshot once.
        snap = _provision.to_dict()
        yield f"data: {_json.dumps(snap)}\n\n"
        while True:
            await asyncio.sleep(0.5)
            snap = _provision.to_dict()
            payload = _json.dumps(snap)
            if payload != last:
                yield f"data: {payload}\n\n"
                last = payload
            if snap.get("done") or snap.get("failed"):
                break

    return StreamingResponse(_gen(), media_type="text/event-stream")


@router.get("/provision/models")
async def provision_models(type: str = "tts") -> dict[str, Any]:
    """Catalog of models available for the wizard to choose from.

    `type` is one of: tts, stt, llm.
    For tts: scans local Piper caches AND returns curated download list.
    For stt: returns curated Vosk model list.
    For llm: returns curated Ollama model list.
    """
    type = (type or "tts").lower()

    if type == "tts":
        # 1. local already-installed (highest priority)
        local: list[dict[str, Any]] = []
        seen: set[str] = set()
        models_dir = _piper_models_dir()
        if models_dir.is_dir():
            for f in sorted(models_dir.iterdir()):
                if f.suffix == ".onnx":
                    vid = f.stem
                    seen.add(vid)
                    local.append({
                        "id": vid, "name": vid, "installed": True,
                        "size_mb": f.stat().st_size // (1024 * 1024),
                        "language": vid.split("_")[0] if "_" in vid else "",
                        "source": "installed",
                    })
        # 2. local Piper cache (~/.local/share/piper/models/)
        for src in [Path.home() / ".local/share/piper/models",
                    Path("/usr/local/share/piper")]:
            if not src.is_dir():
                continue
            for f in sorted(src.iterdir()):
                if f.suffix == ".onnx" and f.stem not in seen:
                    vid = f.stem
                    seen.add(vid)
                    local.append({
                        "id": vid, "name": vid, "installed": False,
                        "size_mb": f.stat().st_size // (1024 * 1024),
                        "language": vid.split("_")[0] if "_" in vid else "",
                        "source": "local-cache",
                        "source_path": str(f),
                    })
        # 3. curated download list (small)
        curated = [
            {"id": "uk_UA-ukrainian_tts-medium", "language": "uk", "size_mb": 77},
            {"id": "uk_UA-lada-x_low", "language": "uk", "size_mb": 20},
            {"id": "en_US-amy-low", "language": "en", "size_mb": 63},
            {"id": "en_US-ryan-low", "language": "en", "size_mb": 63},
            {"id": "ru_RU-irina-medium", "language": "ru", "size_mb": 63},
        ]
        for c in curated:
            if c["id"] not in seen:
                c.update({"installed": False, "name": c["id"], "source": "remote"})
                local.append(c)
                seen.add(c["id"])
        return {"type": "tts", "models": local}

    if type == "stt":
        # Curated Vosk model list (alphacephei.com)
        models = [
            {"id": "vosk-model-small-en-us-0.15", "language": "en", "size_mb": 40},
            {"id": "vosk-model-en-us-0.22", "language": "en", "size_mb": 1800},
            {"id": "vosk-model-small-uk-v3-small", "language": "uk", "size_mb": 75},
            {"id": "vosk-model-uk-v3", "language": "uk", "size_mb": 350},
            {"id": "vosk-model-small-ru-0.22", "language": "ru", "size_mb": 45},
            {"id": "vosk-model-ru-0.42", "language": "ru", "size_mb": 1800},
        ]
        installed_dir = Path(
            get_nested("stt.vosk.models_dir", "/var/lib/selena/models/vosk")
        )
        installed_set = set()
        if installed_dir.is_dir():
            installed_set = {p.name for p in installed_dir.iterdir() if p.is_dir()}
        for m in models:
            m["installed"] = m["id"] in installed_set
            m["name"] = m["id"]
        return {"type": "stt", "models": models}

    if type == "llm":
        # Curated Ollama model list (small models suitable for edge devices)
        models = [
            {"id": "qwen2.5:0.5b", "size_mb": 400, "ram_gb": 1},
            {"id": "qwen2.5:1.5b", "size_mb": 1100, "ram_gb": 2},
            {"id": "qwen2.5:3b", "size_mb": 2000, "ram_gb": 4},
            {"id": "phi3:mini", "size_mb": 2300, "ram_gb": 4},
            {"id": "gemma2:2b", "size_mb": 1700, "ram_gb": 3},
            {"id": "llama3.2:1b", "size_mb": 1300, "ram_gb": 2},
            {"id": "llama3.2:3b", "size_mb": 2000, "ram_gb": 4},
        ]
        for m in models:
            m["name"] = m["id"]
            m["installed"] = False
        return {"type": "llm", "models": models}

    raise HTTPException(status_code=400, detail=f"Unknown type: {type}")
