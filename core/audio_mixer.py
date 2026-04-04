"""
core/audio_mixer.py — Central ALSA audio mixer service.

Generates an ALSA dmix configuration that allows multiple audio sources
(TTS, media player, future modules) to play simultaneously through
one hardware device.

Architecture:
    source_1 (aplay -D selena_tts)    ──┐
    source_2 (VLC --alsa selena_media) ──┼── plug → dmix → hw:X,Y → speaker
    source_N (aplay -D selena_out)     ──┘

Each source has independent volume control (software-level).
Master volume applied via ALSA amixer when HW control exists.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

ASOUNDRC_PATH = Path("/etc/asound.conf")
IPC_KEY = 5678  # dmix shared memory key


@dataclass
class AudioSource:
    name: str           # e.g. "tts", "media"
    display_name: str   # e.g. "Selena TTS", "Media Player"
    device: str         # ALSA device name: "selena_tts"
    volume: int = 70    # 0-100
    icon: str = ""


class AudioMixerService:
    """Singleton that manages ALSA dmix configuration and audio source routing."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sources: dict[str, AudioSource] = {}
        self._master_volume: int = 100  # 0-150
        self._hw_device: str = ""       # raw hw device e.g. "hw:1,3"
        self._hw_card: int = -1
        self._hw_dev: int = -1
        self._initialized = False

    def initialize(self) -> None:
        """Detect hardware device, generate asoundrc, register built-in sources."""
        with self._lock:
            self._detect_hw_device()
            if not self._hw_device:
                logger.warning("AudioMixer: no output device configured, skipping")
                return

            # Register built-in sources
            self._register_builtin_sources()

            # Load master volume from config
            try:
                from core.config_writer import read_config
                cfg = read_config().get("voice", {})
                self._master_volume = int(cfg.get("output_volume", 100))
            except Exception:
                pass

            # Generate ALSA config
            self._generate_asoundrc()
            self._initialized = True
            logger.info(
                "AudioMixer: initialized hw=%s, sources=%s",
                self._hw_device, list(self._sources.keys()),
            )

    def get_device(self, source: str) -> str:
        """Return ALSA device name for a source.

        Known sources get a named device (selena_tts, selena_media).
        Unknown sources get the generic selena_out device.
        """
        with self._lock:
            if source in self._sources:
                return self._sources[source].device
            # Auto-register unknown source
            device = f"selena_{source}" if self._initialized else "default"
            self._sources[source] = AudioSource(
                name=source,
                display_name=source.replace("-", " ").replace("_", " ").title(),
                device=device,
            )
            # Regenerate asoundrc to include new device
            if self._initialized:
                self._generate_asoundrc()
            return device

    def register_source(
        self, name: str, display_name: str, volume: int = 70, icon: str = "",
    ) -> str:
        """Register an audio source. Returns its ALSA device name."""
        with self._lock:
            device = f"selena_{name}"
            # Load saved volume from config
            try:
                from core.config_writer import read_config
                cfg = read_config().get("voice", {})
                saved = cfg.get(f"media_volume_{name}")
                if saved is not None:
                    volume = int(saved)
            except Exception:
                pass

            self._sources[name] = AudioSource(
                name=name,
                display_name=display_name,
                device=device,
                volume=volume,
                icon=icon,
            )
            if self._initialized:
                self._generate_asoundrc()
            return device

    def set_source_volume(self, name: str, volume: int) -> None:
        """Set per-source volume (0-100). Persists to config."""
        volume = max(0, min(100, volume))
        with self._lock:
            if name in self._sources:
                self._sources[name].volume = volume

        # Persist
        try:
            from core.config_writer import update_config
            if name == "tts":
                update_config("voice", "output_volume", volume)
            else:
                update_config("voice", f"media_volume_{name}", volume)
        except Exception:
            pass

        # Apply to running module
        self._apply_volume(name, volume)

    def set_master_volume(self, volume: int) -> None:
        """Set master volume (0-150). Applies via ALSA amixer if available."""
        volume = max(0, min(150, volume))
        self._master_volume = volume

        try:
            from core.config_writer import update_config
            update_config("voice", "output_volume", volume)
        except Exception:
            pass

        # Try hardware mixer
        if self._hw_card >= 0:
            ctrl = self._find_playback_control(self._hw_card)
            if ctrl:
                try:
                    subprocess.run(
                        ["amixer", "-c", str(self._hw_card),
                         "sset", ctrl, f"{volume}%"],
                        timeout=3, capture_output=True,
                    )
                except Exception:
                    pass

    def get_master_volume(self) -> int:
        return self._master_volume

    def get_sources(self) -> list[dict[str, Any]]:
        """Return all registered sources with volume state for the API.

        Module ID for TTS is 'voice-core' to match UI expectations.
        """
        with self._lock:
            result = []
            for src in self._sources.values():
                runtime_vol = self._get_runtime_volume(src.name)
                # UI uses "voice-core" as module ID for TTS
                module_id = "voice-core" if src.name == "tts" else src.name
                result.append({
                    "module": module_id,
                    "name": src.display_name,
                    "volume": runtime_vol if runtime_vol is not None else src.volume,
                    "icon": src.icon,
                    "device": src.device,
                })
            return result

    def is_initialized(self) -> bool:
        return self._initialized

    # ── Private ───────────────────────────────────────────────────────────

    def _detect_hw_device(self) -> None:
        """Read output device from config."""
        try:
            from core.config_writer import read_config
            cfg = read_config().get("voice", {})
            raw = cfg.get("audio_force_output", "")
        except Exception:
            raw = ""

        if not raw:
            return

        # If it's already a virtual device (selena_*), find the original hw
        if raw.startswith("selena_"):
            # Read the original hw device from a stored config key
            try:
                from core.config_writer import read_config
                raw = read_config().get("voice", {}).get("audio_hw_device", "")
            except Exception:
                raw = ""

        # Parse hw:X,Y from plughw:X,Y or hw:X,Y
        m = re.match(r"(?:plug)?hw:(\d+),(\d+)", raw)
        if m:
            self._hw_card = int(m.group(1))
            self._hw_dev = int(m.group(2))
            self._hw_device = f"hw:{self._hw_card},{self._hw_dev}"
            # Store the original HW device for future reference
            try:
                from core.config_writer import update_config
                update_config("voice", "audio_hw_device", raw)
            except Exception:
                pass
        else:
            logger.warning("AudioMixer: cannot parse device '%s'", raw)

    def _register_builtin_sources(self) -> None:
        """Register TTS and discover media modules."""
        # TTS source
        try:
            from core.config_writer import read_config
            cfg = read_config().get("voice", {})
            tts_vol = int(cfg.get("output_volume", 100))
        except Exception:
            tts_vol = 100

        self._sources["tts"] = AudioSource(
            name="tts",
            display_name="Selena TTS",
            device="selena_tts",
            volume=tts_vol,
        )

        # Media player source
        try:
            from core.config_writer import read_config
            cfg = read_config().get("voice", {})
            media_vol = int(cfg.get("media_volume_media-player", 70))
        except Exception:
            media_vol = 70

        self._sources["media-player"] = AudioSource(
            name="media-player",
            display_name="Media Player",
            device="selena_media",
            volume=media_vol,
        )

    def _generate_asoundrc(self) -> None:
        """Write /etc/asound.conf with dmix + per-source plug devices."""
        if not self._hw_device:
            return

        lines = [
            "# Generated by SelenaCore AudioMixerService",
            "# DO NOT EDIT — regenerated on each container start",
            "",
            "pcm.selena_dmix {",
            "    type dmix",
            f"    ipc_key {IPC_KEY}",
            "    ipc_perm 0666",
            "    slave {",
            f"        pcm \"{self._hw_device}\"",
            "        rate 48000",
            "        period_size 1024",
            "        buffer_size 8192",
            "    }",
            "}",
            "",
        ]

        # Per-source devices (all route through dmix via plug)
        device_names = set()
        for src in self._sources.values():
            if src.device not in device_names:
                device_names.add(src.device)
                lines.extend([
                    f"pcm.{src.device} {{",
                    "    type plug",
                    "    slave.pcm \"selena_dmix\"",
                    "}",
                    "",
                ])

        # Generic output device for future modules
        if "selena_out" not in device_names:
            lines.extend([
                "pcm.selena_out {",
                "    type plug",
                "    slave.pcm \"selena_dmix\"",
                "}",
                "",
            ])

        try:
            ASOUNDRC_PATH.write_text("\n".join(lines))
            logger.info("AudioMixer: wrote %s (%d sources)", ASOUNDRC_PATH, len(self._sources))
        except Exception as e:
            logger.error("AudioMixer: failed to write %s: %s", ASOUNDRC_PATH, e)

    def _apply_volume(self, source_name: str, volume: int) -> None:
        """Apply volume to a running module instance."""
        try:
            from core.module_loader.sandbox import get_sandbox
            sandbox = get_sandbox()

            if source_name == "tts":
                # TTS volume is read from config at playback time
                # (_get_output_volume in voice_core), no runtime apply needed
                return

            # For media modules — find the player instance
            module_name = source_name  # source name = module name
            instance = sandbox.get_in_process_module(module_name)
            if instance:
                player = getattr(instance, "_player", None)
                if player and hasattr(player, "set_volume"):
                    import asyncio
                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(player.set_volume(volume))
                    except RuntimeError:
                        # No running event loop
                        pass
        except Exception:
            pass

    def _get_runtime_volume(self, source_name: str) -> int | None:
        """Get current volume from running module."""
        try:
            from core.module_loader.sandbox import get_sandbox
            if source_name == "tts":
                from core.config_writer import read_config
                return int(read_config().get("voice", {}).get("output_volume", 100))

            instance = get_sandbox().get_in_process_module(source_name)
            if instance:
                player = getattr(instance, "_player", None)
                if player:
                    return getattr(player, "_volume", None)
        except Exception:
            pass
        return None

    @staticmethod
    def _find_playback_control(card: int) -> str | None:
        """Find the first usable playback volume control on the ALSA card."""
        try:
            result = subprocess.run(
                ["amixer", "-c", str(card), "scontrols"],
                capture_output=True, text=True, timeout=3,
            )
            for line in result.stdout.splitlines():
                m = re.match(r"Simple mixer control '(.+?)',", line)
                if not m:
                    continue
                name = m.group(1)
                info = subprocess.run(
                    ["amixer", "-c", str(card), "sget", name],
                    capture_output=True, text=True, timeout=3,
                )
                if "pvolume" in info.stdout.lower():
                    return name
        except Exception:
            pass
        return None


# ── Singleton ─────────────────────────────────────────────────────────────

_mixer: AudioMixerService | None = None


def get_mixer() -> AudioMixerService:
    global _mixer
    if _mixer is None:
        _mixer = AudioMixerService()
    return _mixer
