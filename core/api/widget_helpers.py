"""
core/api/widget_helpers.py — Shared helpers for dashboard widget payloads.

Houses cross-module utilities used by widget endpoints (`lights-switches`,
`device-control`, anything else that emits toggle-list payloads):
mapping ``entity_type`` → lucide-style icon name, coercing heterogeneous
device state objects into the toggle-list ``"on" | "off" | "unknown"``
tristate, etc. Centralising these here keeps individual modules' route
files focused on their domain logic and avoids drifting copies (the
prior `ENTITY_ICON` dict in lights-switches and device-control routes
diverged silently — light/switch/outlet were synced but lock/fan/etc.
existed only in device-control).
"""
from __future__ import annotations

from typing import Any


#: Mapping ``entity_type`` → lucide-style icon name (resolved by the
#: frontend ``Icon`` helper to an emoji glyph). Adding a new entity type
#: in `core/registry/models.py` should mean adding an entry here too so
#: dashboard widgets render a recognisable glyph for it.
ENTITY_ICON: dict[str, str] = {
    # Lights / switches / outlets — covered by `lights-switches` widget.
    "light": "lightbulb",
    "switch": "power",
    "outlet": "zap",
    # Climate-class devices — `device-control` widget surfaces these.
    "fan": "wind",
    "ac": "thermometer",
    "climate": "thermometer",
    "thermostat": "thermometer",
    # Security / access.
    "lock": "shield",
    "camera": "eye",
    # Media.
    "tv": "tv",
    "speaker": "volume-2",
    "media_player": "music",
    # Sensors (read-only — toggle-list shows them with state="unknown").
    "sensor": "activity",
}


#: Entity types owned by the `lights-switches` widget. The
#: `device-control` widget excludes these from its toggle-list so
#: pinning both widgets simultaneously does not produce duplicate items.
#: Adding a new on/off-class entity to lights-switches' scope means
#: adding it here too.
LIGHTS_SWITCHES_ENTITY_TYPES: frozenset[str] = frozenset({
    "light", "switch", "outlet",
})


def entity_icon(entity_type: str | None) -> str | None:
    """Return the lucide-style icon name for the given entity type, or
    ``None`` if the type is unknown (the frontend falls back to a status
    dot when the icon is missing). Idempotent / case-insensitive on the
    common lower-case form used by drivers."""
    if not entity_type:
        return None
    return ENTITY_ICON.get(entity_type.lower())


def coerce_onoff_state(state: dict[str, Any]) -> str:
    """Return ``"on"`` / ``"off"`` / ``"unknown"`` from a heterogeneous
    device state dict.

    Handles the two binary conventions used across drivers:
    1. Boolean ``on`` flag (Tuya, Hue, generic).
    2. String ``power`` field (some MQTT / climate devices report
       ``power: "on"`` / ``"off"``).

    Anything else (sensor readings, media-player URIs) is reported as
    ``unknown`` so the toggle-list template grays the cell out and
    disables the click handler.
    """
    if "on" in state:
        return "on" if bool(state.get("on")) else "off"
    power = state.get("power")
    if isinstance(power, str):
        lowered = power.lower()
        if lowered == "on":
            return "on"
        if lowered == "off":
            return "off"
    return "unknown"
