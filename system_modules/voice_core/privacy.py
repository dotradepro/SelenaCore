"""
system_modules/voice_core/privacy.py — Privacy mode control

Privacy mode disables all microphone processing:
  - Wake word detector is paused
  - STT is not invoked
  - Can be toggled via:
    a) GPIO button (physical button press)
    b) Voice command (handled by intent router)
    c) API call POST /api/v1/voice/privacy
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Callable

logger = logging.getLogger(__name__)

GPIO_PRIVACY_PIN = int(os.environ.get("GPIO_PRIVACY_PIN", "17"))
_privacy_enabled = False
_callbacks: list[Callable] = []


def is_privacy_mode() -> bool:
    return _privacy_enabled


def on_privacy_change(callback: Callable[[bool], None]) -> None:
    """Register a callback called with True/False when privacy mode changes."""
    _callbacks.append(callback)


async def set_privacy_mode(enabled: bool) -> None:
    """Set privacy mode state and notify all registered listeners."""
    global _privacy_enabled

    if _privacy_enabled == enabled:
        return

    _privacy_enabled = enabled
    logger.info("Privacy mode: %s", "ON" if enabled else "OFF")

    # Notify listeners (wake word detector, event bus, etc.)
    for cb in _callbacks:
        try:
            if asyncio.iscoroutinefunction(cb):
                await cb(enabled)
            else:
                cb(enabled)
        except Exception as e:
            logger.error("Privacy callback error: %s", e)


async def toggle_privacy_mode() -> bool:
    """Toggle privacy mode. Returns new state."""
    await set_privacy_mode(not _privacy_enabled)
    return _privacy_enabled


async def gpio_listener_loop() -> None:
    """Listen for GPIO button press to toggle privacy mode.

    Requires RPi.GPIO or gpiozero. Gracefully skips if not on Raspberry Pi.
    """
    try:
        import RPi.GPIO as GPIO

        GPIO.setmode(GPIO.BCM)
        GPIO.setup(GPIO_PRIVACY_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        logger.info("GPIO privacy button on pin %d", GPIO_PRIVACY_PIN)

        last_state = GPIO.input(GPIO_PRIVACY_PIN)

        while True:
            current = GPIO.input(GPIO_PRIVACY_PIN)
            # Detect falling edge (button press, active low)
            if last_state == 1 and current == 0:
                await toggle_privacy_mode()
                await asyncio.sleep(0.3)  # debounce
            last_state = current
            await asyncio.sleep(0.05)

    except ImportError:
        logger.info("RPi.GPIO not available — GPIO privacy button disabled")
    except Exception as e:
        logger.error("GPIO listener error: %s", e)
