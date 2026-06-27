#!/usr/bin/env python3
"""Seed one representative device per entity_type missing from the registry.

The bench corpus covers canonical entity_types (humidifier, kettle,
outlet, fan, radiator, heater, tv, …) that the user's imports may not
include. Without a row of that type, IntentRouter's type+location query
returns 0 matches and every bench case for the type falls to
``not_found`` before the group path can exercise. This script adds one
placeholder row per missing canonical type so the bench path is fully
covered.

Rows are created with ``protocol="dummy"`` and a clearly synthetic name
so they can't be mistaken for real hardware. They can be deleted via
the admin UI at any time; re-running the script then recreates them.

Run inside the selena-core container:

    docker exec -t selena-core python3 /opt/selena-core/scripts/seed_missing_types.py
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path

_here = Path(__file__).resolve()
for candidate in (_here.parents[1], Path("/opt/selena-core")):
    if (candidate / "core" / "registry" / "models.py").is_file() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("seed_missing_types")

# Canonical entity types we expect to exercise in bench + the user's
# home. Maps to (EN room, UK room, friendly EN name, friendly UK name).
CANONICAL: dict[str, tuple[str, str, str, str]] = {
    "humidifier":   ("bedroom",     "спальня",   "Humidifier",     "Зволожувач"),
    "kettle":       ("kitchen",     "кухня",     "Kettle",         "Чайник"),
    "outlet":       ("living_room", "вітальня",  "Outlet",         "Розетка"),
    "fan":          ("bedroom",     "спальня",   "Fan",            "Вентилятор"),
    "radiator":     ("living_room", "вітальня",  "Radiator",       "Радіатор"),
    "tv":           ("living_room", "вітальня",  "TV",             "Телевізор"),
}


async def main() -> int:
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import (
        AsyncSession, async_sessionmaker, create_async_engine,
    )

    from core.registry.models import Device

    db = "/var/lib/selena/selena.db"
    if not Path(db).is_file():
        db = "/var/lib/selena/db/selena.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db}")
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    created = 0
    skipped = 0

    async with Session() as session:
        existing = (await session.execute(select(Device.entity_type))).scalars().all()
        existing_types = {t for t in existing if t}

        for et, (en_room, uk_room, en_name, uk_name) in CANONICAL.items():
            if et in existing_types:
                skipped += 1
                log.info("skip %-12s (already %d row(s) in registry)", et, sum(1 for t in existing if t == et))
                continue
            d = Device(
                name=uk_name,
                type="actuator",
                protocol="dummy",
                entity_type=et,
                location=uk_room,
                module_id="device-control",
                enabled=True,
            )
            d.set_capabilities(["on", "off"])
            d.set_meta({
                "name_en": en_name.lower(),
                "location_en": en_room,
                "synthetic": True,
            })
            session.add(d)
            created += 1
            log.info("seed %-12s  %-14s  %-12s", et, uk_name, uk_room)
        if created:
            await session.commit()

    log.info("done: created=%d skipped=%d", created, skipped)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
