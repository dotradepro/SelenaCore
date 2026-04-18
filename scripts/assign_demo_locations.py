#!/usr/bin/env python3
"""Assign deterministic room locations to demo 'via X' device rows.

The Hue-imported demo rows created by the user during testing arrived
with ``location=None``. That blocks the type+location resolver from
exercising the group path — every command lacks the coordinate it
needs. This script fills a room for each demo row using
``hash(device_id) % 5`` so the layout is stable across runs.

Real devices (Gree AC with a real location already set) are untouched.
Idempotent: rows that already have a location are left alone.

Run inside the selena-core container:

    docker exec -t selena-core python3 /opt/selena-core/scripts/assign_demo_locations.py
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import sys
from pathlib import Path

_here = Path(__file__).resolve()
for candidate in (_here.parents[1], Path("/opt/selena-core")):
    if (candidate / "core" / "registry" / "models.py").is_file() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("assign_demo_locations")

ROOMS_EN = ("bedroom", "kitchen", "living_room", "office", "bathroom")
ROOMS_UK = ("спальня",  "кухня",   "вітальня",    "кабінет", "ванна")


def _pick_rooms(device_id: str) -> tuple[str, str]:
    """Pick (en, uk) room pair for a device deterministically."""
    h = int(hashlib.md5(device_id.encode("utf-8")).hexdigest(), 16)
    i = h % len(ROOMS_EN)
    return ROOMS_EN[i], ROOMS_UK[i]


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

    flipped = 0
    skipped = 0

    async with Session() as session:
        rows = (await session.execute(select(Device))).scalars().all()
        for d in rows:
            if d.location:
                skipped += 1
                continue
            # Only touch demo-ish rows (names with "via" in them) — don't
            # auto-assign locations to real imports the user may intend
            # to configure manually later.
            if " via " not in (d.name or ""):
                skipped += 1
                continue
            en_room, uk_room = _pick_rooms(d.device_id)
            d.location = uk_room  # user-language column — registry uses UK
            meta = json.loads(d.meta or "{}")
            meta.setdefault("location_en", en_room)
            d.meta = json.dumps(meta, ensure_ascii=False)
            log.info(
                "set %-30s  location=%-12s  (en=%s)",
                (d.name or "")[:30], uk_room, en_room,
            )
            flipped += 1
        if flipped:
            await session.commit()

    log.info("done: assigned=%d skipped=%d", flipped, skipped)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
