#!/usr/bin/env python3
"""Re-classify Device.entity_type when the name prefix disagrees.

Historical Hue / Z2M / MQTT importers hardcoded ``entity_type="light"``
regardless of the device's actual class. This script reads the ``name``
column and remaps ``entity_type`` per the canonical prefix table below.

Idempotent — running twice is a no-op.

Run inside the selena-core container:

    docker exec -t selena-core python3 /opt/selena-core/scripts/migrate_entity_types.py
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

# Make sure imports work whether we're inside the container or a host checkout.
_here = Path(__file__).resolve()
for candidate in (_here.parents[1], Path("/opt/selena-core")):
    if (candidate / "core" / "registry" / "models.py").is_file() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("migrate_entity_types")


# Prefix → canonical entity_type. Prefix match is case-insensitive and
# applies to the English word that appears before "via" in legacy names.
PREFIX_MAP: dict[str, str] = {
    "light":        "light",
    "switch":       "switch",
    "outlet":       "outlet",
    "fan":          "fan",
    "thermostat":   "thermostat",
    "sensor":       "sensor",
    "lock":         "door_lock",
    "blind":        "curtain",
    "curtain":      "curtain",
    "camera":       "camera",
    "vacuum":       "vacuum",
    "speaker":      "speaker",
    "media player": "media_player",
    "humidifier":   "humidifier",
    "kettle":       "kettle",
    "air conditioner": "air_conditioner",
}


def _classify_by_name(name: str) -> str | None:
    """Return the canonical entity_type for a name starting with a known
    prefix (case-insensitive), or None if the name doesn't match any."""
    if not name:
        return None
    lname = name.strip().lower()
    for prefix, et in PREFIX_MAP.items():
        if lname.startswith(prefix + " ") or lname == prefix:
            return et
        if lname.startswith(prefix + " via ") or lname.startswith(prefix + " "):
            return et
    return None


async def main() -> int:
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import (
        AsyncSession, async_sessionmaker, create_async_engine,
    )

    from core.registry.models import Device

    db = "/var/lib/selena/selena.db"
    if not Path(db).is_file():
        db = "/var/lib/selena/db/selena.db"
    if not Path(db).is_file():
        log.error("registry sqlite not found at /var/lib/selena/(db/)selena.db")
        return 1

    engine = create_async_engine(f"sqlite+aiosqlite:///{db}")
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    flipped = 0
    skipped = 0
    unknown: list[str] = []

    async with Session() as session:
        rows = (await session.execute(select(Device))).scalars().all()
        for d in rows:
            new_et = _classify_by_name(d.name or "")
            if new_et is None:
                unknown.append(f"{d.device_id[:8]} {d.name!r}")
                skipped += 1
                continue
            if (d.entity_type or "").lower() == new_et:
                skipped += 1
                continue
            log.info(
                "flip %-30s  %-18s -> %s",
                (d.name or "")[:30], d.entity_type or "-", new_et,
            )
            d.entity_type = new_et
            flipped += 1
        if flipped:
            await session.commit()

    log.info("done: flipped=%d skipped=%d unknown=%d", flipped, skipped, len(unknown))
    if unknown:
        log.info("unclassifiable names (left as-is):")
        for line in unknown:
            log.info("  %s", line)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
