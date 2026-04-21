"""Convert a fetched PlejdSite into Device rows.

Mirrors the shape of importers.homeassistant.runner: the caller injects
a DB session factory + side-effect callables so this module stays pure
and testable with in-memory SQLite.

The site's AES-128 crypto_key is NOT stored in ``Device.meta`` — it
lives in the encrypted secrets_vault (one row per site_id). Rehydrated
by the gateway at startup. Per-device meta only carries the
``ble_address`` + ``output_address`` the gateway needs to route frames.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from sqlalchemy import select

from core.registry.models import Device

from .homeassistant.runner import DbSessionFactory, Publisher, WatcherStarter

logger = logging.getLogger(__name__)

#: secrets_vault service name template for Plejd site keys.
VAULT_SERVICE_TEMPLATE = "device-control_plejd_{site_id}"

EntityChangeNotifier = Callable[[str, str, str], Awaitable[None]]


@dataclass
class PlejdImportResult:
    import_id: str
    created: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "import_id": self.import_id,
            "created": list(self.created),
            "skipped": list(self.skipped),
        }


def _entity_type_for_device(device_type: str, dimmable: bool) -> tuple[str, list[str]]:
    """Best-effort map from Plejd hardware id to our (entity_type, capabilities).

    All Plejd outputs are lights or switches — even the REL-01 relay is
    typically wired to a light fixture. The UI lets the user PATCH after
    import if needed.
    """
    caps = ["on", "off"]
    if dimmable:
        caps.append("brightness")
    return "light", caps


async def _existing_plejd_addresses(session) -> set[tuple[str, int]]:
    """Return the (ble_addr, output_addr) pairs already in the DB."""
    res = await session.execute(
        select(Device).where(Device.protocol == "plejd_native"),
    )
    out: set[tuple[str, int]] = set()
    for d in res.scalars():
        try:
            meta = json.loads(d.meta or "{}")
        except json.JSONDecodeError:
            continue
        plejd = meta.get("plejd") or {}
        ble = (plejd.get("ble_address") or "").upper()
        out_addr = plejd.get("output_address")
        if ble and isinstance(out_addr, int):
            out.add((ble, out_addr))
    return out


async def run(
    *,
    site,                      # PlejdSite — avoid import cycle on module load
    selected_output_addresses: list[int],
    import_id: str,
    db_session_factory: DbSessionFactory,
    store_site_key: Callable[[str, bytes, str], Awaitable[None]] | None = None,
    publish: Publisher | None = None,
    add_watcher: WatcherStarter | None = None,
    on_entity_changed: EntityChangeNotifier | None = None,
    module_id: str = "device-control",
) -> PlejdImportResult:
    """Create Device rows for every selected output in the site.

    ``store_site_key`` is injected so tests can stub out secrets_vault IO.
    Selected outputs that are already imported (same ble_address +
    output_address) are skipped — re-running the wizard never produces
    duplicates.
    """
    result = PlejdImportResult(import_id=import_id)
    selected = set(int(x) for x in selected_output_addresses)

    async with db_session_factory() as session:
        async with session.begin():
            existing = await _existing_plejd_addresses(session)

            created_rows: list[tuple[object, Device]] = []
            for cloud_dev in site.devices:
                if cloud_dev.output_address not in selected:
                    continue
                key = (cloud_dev.ble_address.upper(), cloud_dev.output_address)
                if key in existing:
                    result.skipped.append({
                        "output_address": cloud_dev.output_address,
                        "reason": "already imported",
                    })
                    continue
                entity_type, capabilities = _entity_type_for_device(
                    cloud_dev.device_type, cloud_dev.dimmable,
                )
                meta = {
                    "plejd": {
                        "site_id":        site.site_id,
                        "ble_address":    cloud_dev.ble_address.upper(),
                        "output_address": cloud_dev.output_address,
                        "device_type":    cloud_dev.device_type,
                        "dimmable":       cloud_dev.dimmable,
                    },
                    "plejd_import": {
                        "import_id":   import_id,
                        "site_title":  site.title,
                        "imported_at": datetime.now(timezone.utc).isoformat(),
                    },
                }
                display_name = cloud_dev.title or f"Plejd {cloud_dev.output_address}"
                db_row = Device(
                    name=display_name,
                    type="actuator",
                    protocol="plejd_native",
                    entity_type=entity_type,
                    location=cloud_dev.room,
                    module_id=module_id,
                    enabled=True,
                )
                db_row.set_capabilities(capabilities)
                db_row.set_meta(meta)
                session.add(db_row)
                await session.flush()
                created_rows.append((cloud_dev, db_row))

    # Persist the site crypto_key outside the DB transaction — the vault
    # is a separate filesystem store.
    if created_rows and store_site_key is not None:
        try:
            await store_site_key(site.site_id, site.crypto_key, site.title)
        except Exception:
            logger.exception("plejd-import: store_site_key failed for %s", site.site_id)

    for cloud_dev, db_row in created_rows:
        result.created.append({
            "device_id": db_row.device_id,
            "output_address": cloud_dev.output_address,
            "ble_address": cloud_dev.ble_address,
            "name": db_row.name,
        })
        if on_entity_changed is not None:
            try:
                await on_entity_changed("device", db_row.device_id, "created")
            except Exception:
                logger.warning(
                    "plejd-import: on_entity_changed failed for %s",
                    db_row.device_id, exc_info=True,
                )
        if add_watcher is not None:
            try:
                await add_watcher(db_row.device_id)
            except Exception:
                logger.warning(
                    "plejd-import: add_watcher failed for %s",
                    db_row.device_id, exc_info=True,
                )
        if publish is not None:
            try:
                await publish("device.registered", {
                    "device_id": db_row.device_id,
                    "name": db_row.name,
                    "entity_type": db_row.entity_type,
                    "location": db_row.location,
                    "protocol": "plejd_native",
                    "capabilities": db_row.get_capabilities(),
                    "source": "plejd_import",
                    "import_id": import_id,
                })
            except Exception:
                logger.warning(
                    "plejd-import: publish failed for %s",
                    db_row.device_id, exc_info=True,
                )

    return result
