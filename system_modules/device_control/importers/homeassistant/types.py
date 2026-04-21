"""Common dataclasses shared across the HA importer pipeline.

Kept isolated from `client.py`/`fetcher.py` so extractor tests can import
these types without pulling aiohttp into the test runtime.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

ExtractionStatus = Literal["ok", "needs_user_input", "unsupported"]


@dataclass
class HAEntity:
    """One row from Home Assistant's entity_registry/list."""
    entity_id: str                    # "light.kitchen_ceiling"
    unique_id: str | None             # integration-specific id
    platform: str                     # "tuya", "esphome", "hue", ...
    device_class: str | None = None
    disabled_by: str | None = None    # "user" | "integration" | None


@dataclass
class HADevice:
    """Normalised Home Assistant device record.

    Fields mirror the union of device_registry + config_entries; the
    fetcher is responsible for filling both sides. Extractors consume
    exactly this shape.
    """
    id: str                           # HA's device_id (UUID)
    name: str                         # user-facing name (name_by_user ?? name)
    area: str | None                  # human-readable area name
    integration: str                  # config_entry.domain ("tuya", "esphome", ...)
    entry_id: str                     # config_entry_id
    entry_data: dict[str, Any]        # config_entry.data (integration-specific)
    entry_options: dict[str, Any]     # config_entry.options
    identifiers: list[list[str]] = field(default_factory=list)
    manufacturer: str | None = None
    model: str | None = None
    sw_version: str | None = None
    entities: list[HAEntity] = field(default_factory=list)


@dataclass
class ExtractionResult:
    """Output of one extractor for one HA device.

    ``status`` drives the colour in the UI readiness report:
        - "ok"               → green, will be imported
        - "needs_user_input" → yellow, user must supply extra creds
        - "unsupported"      → red, cannot migrate (informational)
    """
    status: ExtractionStatus
    protocol: str | None = None       # target SelenaCore protocol ("tuya_local", ...)
    entity_type: str | None = None    # "light" | "switch" | ...
    credentials: dict[str, Any] = field(default_factory=dict)
    capabilities: list[str] = field(default_factory=list)
    reason: str = ""                  # human-readable explanation (primarily for red)
    needs: list[str] = field(default_factory=list)  # which inputs are missing (yellow)


@dataclass
class ReadinessRow:
    """One row in the preview table shown to the user."""
    ha_device_id: str
    ha_device_name: str
    integration: str
    status: ExtractionStatus
    protocol: str | None = None
    entity_type: str | None = None
    reason: str = ""
    needs: list[str] = field(default_factory=list)


@dataclass
class ReadinessReport:
    """Aggregate of all extractor results for a preview call."""
    green: int = 0
    yellow: int = 0
    red: int = 0
    rows: list[ReadinessRow] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "green": self.green,
            "yellow": self.yellow,
            "red": self.red,
            "rows": [
                {
                    "ha_device_id": r.ha_device_id,
                    "ha_device_name": r.ha_device_name,
                    "integration": r.integration,
                    "status": r.status,
                    "protocol": r.protocol,
                    "entity_type": r.entity_type,
                    "reason": r.reason,
                    "needs": list(r.needs),
                }
                for r in self.rows
            ],
        }
