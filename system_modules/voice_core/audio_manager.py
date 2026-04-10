"""
system_modules/voice_core/audio_manager.py — audio device autodetection

Priority order:
  Input:  usb > i2s_gpio > bluetooth > hdmi > builtin
  Output: usb > i2s_gpio > bluetooth > hdmi > jack > builtin

ALSA fallback parses ``aplay -l`` / ``arecord -l`` to get real device numbers
(not just card-level ``hw:X,0``) and filters out internal buses (tegra APE/ADMAIF).
"""
from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

PRIORITY_INPUT = ["usb", "i2s_gpio", "bluetooth", "hdmi", "builtin"]
PRIORITY_OUTPUT = ["usb", "i2s_gpio", "bluetooth", "hdmi", "jack", "builtin"]

# Internal virtual buses that should never appear in the user-facing list.
_INTERNAL_DEVICE_RE = re.compile(r"admaif|xbar|tegra-dlink", re.IGNORECASE)


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


def _classify_device(card_name: str, device_name: str = "") -> str:
    """Classify an ALSA device into a type using card + device names."""
    combined = (card_name + " " + device_name).lower()
    if "usb" in combined:
        return "usb"
    if "i2s" in combined or "rpi" in combined or "simple" in combined:
        return "i2s_gpio"
    if "hdmi" in combined or "hda" in combined:
        return "hdmi"
    if "jack" in combined or "headphone" in combined:
        return "jack"
    return "builtin"


def _parse_aplay_arecord(cmd: str) -> list[dict]:
    """Parse output of ``aplay -l`` or ``arecord -l``.

    Returns a list of dicts with keys: card, device, card_name, device_name.
    Filters out internal virtual buses (tegra APE ADMAIF channels).
    """
    try:
        result = subprocess.run(
            [cmd, "-l"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return []
    except Exception:
        return []

    devices: list[dict] = []
    for line in result.stdout.splitlines():
        # "card 1: HDA [NVIDIA Jetson Orin Nano HDA], device 3: HDMI 0 [HDMI]"
        m = re.match(
            r"card\s+(\d+):\s+\S+\s+\[(.+?)\],\s+device\s+(\d+):\s+(.+)",
            line,
        )
        if not m:
            continue
        card_idx = int(m.group(1))
        card_name = m.group(2).strip()
        dev_idx = int(m.group(3))
        dev_rest = m.group(4).strip()

        # dev_rest is like "HDMI 0 [HDMI]" or "USB Audio [USB Audio]"
        # Extract the part before the bracket as display name
        bracket = dev_rest.find("[")
        if bracket > 0:
            device_name = dev_rest[:bracket].strip()
        else:
            device_name = dev_rest

        # Skip internal virtual buses (tegra APE ADMAIF)
        if _INTERNAL_DEVICE_RE.search(dev_rest):
            continue

        devices.append({
            "card": card_idx,
            "device": dev_idx,
            "card_name": card_name,
            "device_name": device_name,
        })
    return devices


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
    if "hdmi" in n or "hda" in n:
        return "hdmi"
    return "builtin"


def detect_audio_devices() -> AudioDevices:
    """Detect all available audio input and output devices, sorted by priority.

    Strategy: ALSA is the primary source (always sees all hardware cards,
    even those held by ``arecord`` or not loaded by PulseAudio).
    PulseAudio supplements with virtual/Bluetooth devices that ALSA
    doesn't know about.
    """
    devices = AudioDevices()
    alsa_cards: set[int] = set()

    # ── 1. ALSA hardware (always) ───────────────────────────────────
    # De-duplicate: keep only the first sub-device per (card, type) pair.
    # E.g. Jetson HDA exposes HDMI 0-3 on the same card — show one entry.
    seen_out: set[tuple[int, str]] = set()
    for dev in _parse_aplay_arecord("aplay"):
        alsa_id = f"plughw:{dev['card']},{dev['device']}"
        dtype = _classify_device(dev["card_name"], dev["device_name"])
        key = (dev["card"], dtype)
        if key in seen_out:
            continue
        seen_out.add(key)
        display = f"{dev['device_name']} ({dev['card_name']})"
        devices.outputs.append(AudioDevice(id=alsa_id, name=display, type=dtype))
        alsa_cards.add(dev["card"])

    seen_in: set[tuple[int, str]] = set()
    for dev in _parse_aplay_arecord("arecord"):
        alsa_id = f"plughw:{dev['card']},{dev['device']}"
        dtype = _classify_device(dev["card_name"], dev["device_name"])
        key = (dev["card"], dtype)
        if key in seen_in:
            continue
        seen_in.add(key)
        display = f"{dev['device_name']} ({dev['card_name']})"
        devices.inputs.append(AudioDevice(id=alsa_id, name=display, type=dtype))
        alsa_cards.add(dev["card"])

    # ── 2. PulseAudio extras (bluetooth, virtual — not visible to ALSA) ─
    if _is_pulse_running():
        for sink in _pactl_list_detailed("sinks"):
            dtype = _classify_pulse_name(sink["name"])
            if dtype == "bluetooth":
                desc = sink.get("description", sink["name"])
                devices.outputs.append(AudioDevice(id=sink["name"], name=desc, type=dtype))

        for source in _pactl_list_detailed("sources"):
            if ".monitor" in source["name"]:
                continue
            dtype = _classify_pulse_name(source["name"])
            if dtype == "bluetooth":
                desc = source.get("description", source["name"])
                devices.inputs.append(AudioDevice(id=source["name"], name=desc, type=dtype))

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
