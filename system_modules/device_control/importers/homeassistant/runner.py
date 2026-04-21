"""Persist HA-imported devices into the SelenaCore registry.

Runner is the only step that mutates state — everything before it
(connect / preview) is read-only. The split matters for the contract in
the plan: the user approves a readiness report before any DB row is
created, and can roll back by ``import_id`` if the post-import health
check fails.

Idempotency: each created Device row stores
``meta.ha_import = {ha_device_id, entry_id, imported_at, import_id}``.
Re-running the runner for the same ``ha_device_id`` finds the existing
row and skips — never creates a duplicate.

Side-effect wiring (publish / watcher / entity-change) is injected by
the caller so this module is pure and testable without FastAPI or the
live event bus.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from sqlalchemy import select

from core.registry.models import Device

from . import extractors
from .types import HADevice

logger = logging.getLogger(__name__)

#: Side-effect callables the caller wires up. All optional except the DB
#: factory — when omitted, runner still creates Device rows but skips the
#: corresponding side effect. This keeps tests fast.
DbSessionFactory = Callable[[], Any]  # () -> async context manager
Publisher = Callable[[str, dict[str, Any]], Awaitable[None]]
WatcherStarter = Callable[[str], Awaitable[None]]
EntityChangeNotifier = Callable[[str, str, str], Awaitable[None]]


@dataclass
class RunResult:
    import_id: str
    created: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    failed: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "import_id": self.import_id,
            "created": list(self.created),
            "skipped": list(self.skipped),
            "failed": list(self.failed),
        }


def _ha_import_device_ids(db_device: Device) -> str | None:
    """Return the HA device id a Selena device was imported from, if any."""
    try:
        meta = json.loads(db_device.meta or "{}")
    except json.JSONDecodeError:
        return None
    ha = meta.get("ha_import") or {}
    return ha.get("ha_device_id") or None


async def _load_existing_ha_device_ids(session) -> set[str]:
    """Fetch every Device row that carries an ha_import stamp.

    We do a Python-side JSON decode because ``meta`` is a Text column
    (not native JSON) and LIKE-matching on substrings is too fragile
    across dialects. HA imports happen once per deployment so the O(N)
    scan is never on a hot path.
    """
    res = await session.execute(select(Device))
    existing: set[str] = set()
    for row in res.scalars():
        ha_id = _ha_import_device_ids(row)
        if ha_id:
            existing.add(ha_id)
    return existing


def _build_device_row(
    *,
    device: HADevice,
    result,
    import_id: str,
    module_id: str,
) -> Device:
    protocol = result.protocol or device.integration
    entity_type = result.entity_type
    capabilities = list(result.capabilities or [])
    display_name = device.name or f"HA {device.integration} {device.id[:6]}"
    location = device.area

    # Per-protocol meta nest — mirrors the shape each driver expects
    # (e.g. tuya_local reads meta['tuya'], esphome reads meta['esphome']).
    protocol_meta_key = {
        "tuya_local":  "tuya",
        "tuya_cloud":  "tuya",
        "esphome":     "esphome",
        "philips_hue": "hue",
        "mqtt":        "mqtt",
        "zigbee2mqtt": "zigbee2mqtt",
    }.get(protocol, protocol)

    meta: dict[str, Any] = {
        protocol_meta_key: dict(result.credentials),
        "ha_import": {
            "ha_device_id":  device.id,
            "entry_id":      device.entry_id,
            "integration":   device.integration,
            "import_id":     import_id,
            "imported_at":   datetime.now(timezone.utc).isoformat(),
        },
    }

    db_device = Device(
        name=display_name,
        type="actuator",
        protocol=protocol,
        entity_type=entity_type,
        location=location,
        module_id=module_id,
        enabled=True,
    )
    db_device.set_capabilities(capabilities)
    db_device.set_meta(meta)
    return db_device


async def run(
    *,
    devices: list[HADevice],
    selected_ids: list[str],
    context: dict[str, Any] | None,
    import_id: str,
    db_session_factory: DbSessionFactory,
    publish: Publisher | None = None,
    add_watcher: WatcherStarter | None = None,
    on_entity_changed: EntityChangeNotifier | None = None,
    module_id: str = "device-control",
) -> RunResult:
    """Create Device rows for every selected green HA device.

    ``selected_ids`` limits which HA devices are materialised — the
    preview UI lets the user uncheck individual rows. Rows not in the
    selection are ignored entirely (they don't appear in any of the
    result buckets)."""
    result = RunResult(import_id=import_id)
    selected = set(selected_ids or [])
    ctx = context or {}

    # Pass 1: run extractors on selected devices and collect the
    # (device, extraction) pairs we actually intend to create. Extractors
    # are cheap so this costs nothing.
    intent: list[tuple[HADevice, Any]] = []
    for device in devices:
        if device.id not in selected:
            continue
        extraction = extractors.extract(device, ctx)
        if extraction.status != "ok":
            result.skipped.append({
                "ha_device_id": device.id,
                "reason": extraction.reason or f"status={extraction.status}",
            })
            continue
        intent.append((device, extraction))

    if not intent:
        return result

    # Pass 2: open ONE DB session, check idempotency once, then create.
    async with db_session_factory() as session:
        async with session.begin():
            existing = await _load_existing_ha_device_ids(session)

            created_rows: list[tuple[HADevice, Any, Device]] = []
            for device, extraction in intent:
                if device.id in existing:
                    result.skipped.append({
                        "ha_device_id": device.id,
                        "reason": "already imported",
                    })
                    continue
                try:
                    row = _build_device_row(
                        device=device,
                        result=extraction,
                        import_id=import_id,
                        module_id=module_id,
                    )
                    session.add(row)
                    await session.flush()
                except Exception as exc:  # pragma: no cover — defensive
                    logger.exception(
                        "ha-import: failed to build row for %s", device.id,
                    )
                    result.failed.append({
                        "ha_device_id": device.id,
                        "reason": str(exc),
                    })
                    continue
                created_rows.append((device, extraction, row))

        # Side effects fire outside the transaction so a slow event bus
        # doesn't hold the write lock. The rows are committed at this
        # point — we record the full result before firing.
        for device, extraction, row in created_rows:
            result.created.append({
                "device_id": row.device_id,
                "ha_device_id": device.id,
                "name": row.name,
                "protocol": row.protocol,
                "entity_type": row.entity_type,
            })

    for device, extraction, row in created_rows:
        if on_entity_changed is not None:
            try:
                await on_entity_changed("device", row.device_id, "created")
            except Exception:
                logger.warning(
                    "ha-import: on_entity_changed failed for %s", row.device_id,
                    exc_info=True,
                )
        if add_watcher is not None:
            try:
                await add_watcher(row.device_id)
            except Exception:
                logger.warning(
                    "ha-import: add_watcher failed for %s", row.device_id,
                    exc_info=True,
                )
        if publish is not None:
            try:
                await publish("device.registered", {
                    "device_id": row.device_id,
                    "name": row.name,
                    "entity_type": row.entity_type,
                    "location": row.location,
                    "protocol": row.protocol,
                    "capabilities": row.get_capabilities(),
                    "source": "ha_import",
                    "import_id": import_id,
                })
            except Exception:
                logger.warning(
                    "ha-import: publish failed for %s", row.device_id,
                    exc_info=True,
                )

    return result


async def rollback(
    *,
    import_id: str,
    db_session_factory: DbSessionFactory,
    publish: Publisher | None = None,
) -> list[str]:
    """Delete every Device row created by the given import_id.

    Returns the ids that were deleted. Missing import_id → empty list
    (rollback is idempotent)."""
    deleted: list[str] = []
    async with db_session_factory() as session:
        async with session.begin():
            res = await session.execute(select(Device))
            for row in list(res.scalars()):
                try:
                    meta = json.loads(row.meta or "{}")
                except json.JSONDecodeError:
                    continue
                if (meta.get("ha_import") or {}).get("import_id") != import_id:
                    continue
                deleted.append(row.device_id)
                await session.delete(row)
    if publish is not None and deleted:
        for did in deleted:
            try:
                await publish("device.deleted", {"device_id": did, "source": "ha_import_rollback"})
            except Exception:
                logger.warning("ha-import: rollback publish failed for %s", did, exc_info=True)
    return deleted
