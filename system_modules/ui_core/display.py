"""
system_modules/ui_core/display.py — display mode autodetection

Possible modes:
  headless    — no display at all (SSH-only)
  kiosk       — HDMI connected, Chromium available
  framebuffer — HDMI connected, no X, /dev/fb0 exists
  tty         — fallback terminal (no HDMI / no framebuffer)
"""
from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

DisplayMode = str  # "headless" | "kiosk" | "framebuffer" | "tty"


def detect_display_mode() -> DisplayMode:
    """Detect the available display output mode on this device."""

    # 1. DISPLAY env set → X11/Wayland available (kiosk possible)
    if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
        if _chromium_available():
            return "kiosk"

    # 2. Framebuffer device exists
    if Path("/dev/fb0").exists():
        return "framebuffer"

    # 3. HDMI hotplug detect via /sys
    for edid_path in Path("/sys/class/drm").glob("*/edid"):
        try:
            if edid_path.stat().st_size > 0:
                return "framebuffer"
        except OSError:
            continue

    # 4. TTY — we have a terminal but no graphics
    if os.isatty(1):
        return "tty"

    # 5. Truly headless
    return "headless"


def _chromium_available() -> bool:
    for binary in ("chromium-browser", "chromium", "google-chrome"):
        result = subprocess.run(
            ["which", binary], capture_output=True, timeout=3
        )
        if result.returncode == 0:
            return True
    return False


def launch_kiosk(url: str = "http://localhost:8080") -> subprocess.Popen | None:
    """Launch Chromium in kiosk mode pointing at the UI URL."""
    for binary in ("chromium-browser", "chromium", "google-chrome"):
        result = subprocess.run(["which", binary], capture_output=True, timeout=3)
        if result.returncode == 0:
            logger.info("Launching kiosk: %s %s", binary, url)
            return subprocess.Popen([
                binary,
                "--kiosk",
                "--no-sandbox",
                "--disable-infobars",
                "--disable-session-crashed-bubble",
                "--disable-restore-session-state",
                "--autoplay-policy=no-user-gesture-required",
                url,
            ])
    logger.warning("No Chromium binary found for kiosk mode")
    return None
