"""
system_modules/voice_core/audio_manager.py — audio device autodetection

Priority order:
  Input:  usb > i2s_gpio > bluetooth > hdmi > builtin
  Output: usb > i2s_gpio > bluetooth > hdmi > jack > builtin
"""
from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

PRIORITY_INPUT = ["usb", "i2s_gpio", "bluetooth", "hdmi", "builtin"]
PRIORITY_OUTPUT = ["usb", "i2s_gpio", "bluetooth", "hdmi", "jack", "builtin"]


@dataclass
class AudioDevice:
    id: str            # ALSA hw:X,Y or PulseAudio sink name
    name: str
    type: str          # usb | i2s_gpio | bluetooth | hdmi | jack | builtin


@dataclass
class AudioDevices:
    inputs: list[AudioDevice] = field(default_factory=list)
    outputs: list[AudioDevice] = field(default_factory=list)


def _priority_score(device_type: str, priority: list[str]) -> int:
    try:
        return priority.index(device_type)
    except ValueError:
        return len(priority)


def _classify_card(card_name: str, driver: str = "") -> str:
    name_lower = (card_name + " " + driver).lower()
    if "usb" in name_lower:
        return "usb"
    if "i2s" in name_lower or "rpi" in name_lower or "simple" in name_lower:
        return "i2s_gpio"
    if "hdmi" in name_lower:
        return "hdmi"
    if "jack" in name_lower or "headphone" in name_lower:
        return "jack"
    return "builtin"


def _parse_alsa_cards() -> list[dict]:
    """Parse /proc/asound/cards to get card list."""
    try:
        content = open("/proc/asound/cards").read()
    except OSError:
        return []

    cards = []
    for line in content.splitlines():
        m = re.match(r"\s*(\d+)\s+\[(\S+)\s*\]: (.+)", line)
        if m:
            cards.append({
                "index": int(m.group(1)),
                "id": m.group(2),
                "name": m.group(3).strip(),
            })
    return cards


def _card_has_capture(card_index: int) -> bool:
    try:
        result = subprocess.run(
            ["arecord", "-l"],
            capture_output=True, text=True, timeout=5
        )
        return f"card {card_index}" in result.stdout
    except Exception:
        return False


def _card_has_playback(card_index: int) -> bool:
    try:
        result = subprocess.run(
            ["aplay", "-l"],
            capture_output=True, text=True, timeout=5
        )
        return f"card {card_index}" in result.stdout
    except Exception:
        return False


def _pulse_env() -> dict[str, str]:
    """Return environment dict that lets ``pactl`` reach PulseAudio.

    When the server runs as root, PulseAudio belongs to a regular user.
    We look for the first available socket under ``/run/user/*/pulse/native``.
    """
    import glob as _glob
    import os

    env = os.environ.copy()
    # Already reachable?
    if env.get("PULSE_SERVER") or env.get("PULSE_RUNTIME_PATH"):
        return env
    sockets = sorted(_glob.glob("/run/user/*/pulse/native"))
    if sockets:
        env["PULSE_SERVER"] = f"unix:{sockets[0]}"
    return env


def _is_pulse_running() -> bool:
    try:
        result = subprocess.run(
            ["pactl", "info"],
            capture_output=True, timeout=3,
            env=_pulse_env(),
        )
        return result.returncode == 0
    except Exception:
        return False


def _pactl_list_detailed(kind: str) -> list[dict]:
    """List PulseAudio sinks or sources with descriptions.

    *kind* must be ``"sinks"`` or ``"sources"``.
    """
    try:
        result = subprocess.run(
            ["pactl", "list", kind],
            capture_output=True, text=True, timeout=5,
            env=_pulse_env(),
        )
        items: list[dict] = []
        current: dict = {}
        for line in result.stdout.splitlines():
            line_s = line.strip()
            if line_s.startswith(("Sink #", "Source #")):
                if current.get("name"):
                    items.append(current)
                current = {}
            elif line_s.startswith("Name:"):
                current["name"] = line_s.split(":", 1)[1].strip()
            elif line_s.startswith("Description:"):
                current["description"] = line_s.split(":", 1)[1].strip()
        if current.get("name"):
            items.append(current)
        return items
    except Exception:
        return []


def _classify_pulse_name(name: str) -> str:
    """Classify a PulseAudio sink/source name into a device type."""
    n = name.lower()
    if "bluez" in n:
        return "bluetooth"
    if "usb" in n:
        return "usb"
    if "hdmi" in n:
        return "hdmi"
    return "builtin"


def detect_audio_devices() -> AudioDevices:
    """Detect all available audio input and output devices, sorted by priority."""
    devices = AudioDevices()

    if _is_pulse_running():
        # PulseAudio / PipeWire is running — use it as the primary source.
        for sink in _pactl_list_detailed("sinks"):
            dtype = _classify_pulse_name(sink["name"])
            desc = sink.get("description", sink["name"])
            devices.outputs.append(AudioDevice(id=sink["name"], name=desc, type=dtype))

        for source in _pactl_list_detailed("sources"):
            # Skip monitor sources — they are not real inputs
            if ".monitor" in source["name"]:
                continue
            dtype = _classify_pulse_name(source["name"])
            desc = source.get("description", source["name"])
            devices.inputs.append(AudioDevice(id=source["name"], name=desc, type=dtype))
    else:
        # Fallback: raw ALSA cards
        for card in _parse_alsa_cards():
            dtype = _classify_card(card["name"])
            alsa_id = f"hw:{card['index']},0"
            if _card_has_capture(card["index"]):
                devices.inputs.append(AudioDevice(id=alsa_id, name=card["name"], type=dtype))
            if _card_has_playback(card["index"]):
                devices.outputs.append(AudioDevice(id=alsa_id, name=card["name"], type=dtype))

    # Sort by priority
    devices.inputs.sort(key=lambda d: _priority_score(d.type, PRIORITY_INPUT))
    devices.outputs.sort(key=lambda d: _priority_score(d.type, PRIORITY_OUTPUT))

    logger.info(
        "Audio devices: %d inputs, %d outputs",
        len(devices.inputs), len(devices.outputs)
    )
    return devices


def get_best_input() -> AudioDevice | None:
    return (detect_audio_devices().inputs or [None])[0]


def get_best_output() -> AudioDevice | None:
    return (detect_audio_devices().outputs or [None])[0]
