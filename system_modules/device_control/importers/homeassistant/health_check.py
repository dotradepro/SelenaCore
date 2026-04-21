"""Post-import reachability check.

After ``runner.run`` returns a set of freshly-created Device rows, we
fire a short connect/get_state probe per device to confirm the native
driver can actually talk to it. The UI uses the result to render the
"✓ Ready to disconnect Home Assistant" banner — if any device is
unreachable the user sees it before turning HA off.

The check is best-effort:
    - timeouts are short (driver.connect is usually < 1s on LAN)
    - a failed probe doesn't delete or disable the row, it only flips
      ``meta.ha_import.health`` so the UI and rollback dialog can act
      on it
    - no retries inside the checker — watchers will keep trying in the
      background once the import finishes, and the user can re-run the
      health check explicitly if they rearrange the network
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from sqlalchemy import select

from core.registry.models import Device

from .runner import DbSessionFactory

logger = logging.getLogger(__name__)

DriverFactory = Callable[[str, str, dict[str, Any]], Any]

#: Default timeout for a single device probe (connect + get_state).
DEFAULT_TIMEOUT_S = 5.0


@dataclass
class HealthResult:
    reachable: list[str] = field(default_factory=list)
    unreachable: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "reachable": list(self.reachable),
            "unreachable": list(self.unreachable),
        }


async def _probe_one(
    device: Device,
    *,
    driver_factory: DriverFactory,
    timeout: float,
) -> tuple[bool, str]:
    """Run connect() + get_state() within ``timeout`` seconds.

    Returns (ok, reason). ``reason`` is the user-facing diagnostic when
    ok=False; empty when ok=True. Never raises — all exceptions are
    captured as unreachable with the exception message as reason.
    """
    try:
        meta = json.loads(device.meta or "{}")
    except json.JSONDecodeError as exc:
        return False, f"corrupt meta JSON: {exc}"

    try:
        driver = driver_factory(device.device_id, device.protocol, meta)
    except Exception as exc:
        return False, f"driver init failed: {exc}"

    try:
        await asyncio.wait_for(driver.connect(), timeout=timeout)
        # get_state is optional — some drivers only push updates via the
        # event stream. If it's present and fast, we call it to validate
        # the channel end-to-end.
        getter = getattr(driver, "get_state", None)
        if callable(getter):
            try:
                await asyncio.wait_for(getter(), timeout=timeout)
            except Exception:
                # get_state failing doesn't mean unreachable — the
                # connect() call already succeeded, which is enough to
                # call the device reachable.
                pass
        return True, ""
    except asyncio.TimeoutError:
        return False, f"timeout after {timeout}s"
    except Exception as exc:
        return False, str(exc)
    finally:
        disconnect = getattr(driver, "disconnect", None)
        if callable(disconnect):
            try:
                await asyncio.wait_for(disconnect(), timeout=timeout)
            except Exception:
                pass


async def run(
    *,
    import_id: str,
    db_session_factory: DbSessionFactory,
    driver_factory: DriverFactory,
    timeout: float = DEFAULT_TIMEOUT_S,
    concurrency: int = 4,
) -> HealthResult:
    """Ping every device tagged with ``import_id`` and stamp the outcome
    into ``meta.ha_import.health``.
    """
    # Fetch rows in one session, release before the probe loop so we're
    # not holding the DB lock while waiting on network.
    async with db_session_factory() as session:
        res = await session.execute(select(Device))
        rows = [
            d for d in res.scalars()
            if _import_id(d) == import_id
        ]

    if not rows:
        return HealthResult()

    sem = asyncio.Semaphore(max(1, concurrency))

    async def _probe_guarded(d: Device) -> tuple[str, bool, str]:
        async with sem:
            ok, reason = await _probe_one(
                d, driver_factory=driver_factory, timeout=timeout,
            )
        return d.device_id, ok, reason

    outcomes = await asyncio.gather(*[_probe_guarded(d) for d in rows])

    # Write the health stamps back. One session, one transaction.
    result = HealthResult()
    async with db_session_factory() as session:
        async with session.begin():
            res = await session.execute(select(Device))
            by_id = {d.device_id: d for d in res.scalars()}
            for device_id, ok, reason in outcomes:
                device = by_id.get(device_id)
                if device is None:
                    continue   # deleted between fetch and stamp
                meta = json.loads(device.meta or "{}")
                ha = meta.setdefault("ha_import", {})
                ha["health"] = "reachable" if ok else "unreachable"
                if not ok:
                    ha["health_reason"] = reason
                else:
                    ha.pop("health_reason", None)
                device.meta = json.dumps(meta)
                if ok:
                    result.reachable.append(device_id)
                else:
                    result.unreachable.append({
                        "device_id": device_id,
                        "reason": reason,
                    })
    return result


def _import_id(device: Device) -> str | None:
    try:
        return (json.loads(device.meta or "{}").get("ha_import") or {}).get("import_id")
    except json.JSONDecodeError:
        return None
