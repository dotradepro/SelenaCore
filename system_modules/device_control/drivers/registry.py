"""
system_modules/device_control/drivers/registry.py — runtime driver registry.

The DRIVERS dict is no longer populated by eager imports. It is
populated at startup by ``providers.loader.ProviderLoader.load_enabled()``
which walks the ``driver_providers`` SQLite table and imports each
enabled provider's driver class via ``importlib``.

To add a new driver, register it in
``system_modules/device_control/providers/catalog.py`` instead of editing
this file.
"""
from __future__ import annotations

from typing import Any

from ..providers.catalog import PROVIDERS, get_provider
from .base import DeviceDriver, DriverError

#: Runtime driver class map. Populated by ProviderLoader.load_enabled()
#: at module startup, mutated in place by install/uninstall — never
#: rebuilt or replaced, so callers can hold a reference safely.
DRIVERS: dict[str, type[DeviceDriver]] = {}


def get_driver(device_id: str, protocol: str, meta: dict[str, Any]) -> DeviceDriver:
    """Instantiate the right driver for ``device.protocol``.

    Raises ``DriverError`` if the protocol is unknown OR if the provider
    package is not installed (loader skipped it on startup). The watcher
    catches DriverError and marks the device offline with backoff.
    """
    cls = DRIVERS.get(protocol)
    if cls is None:
        if protocol in PROVIDERS:
            raise DriverError(
                f"Provider {protocol!r} is not installed — open device-control "
                "settings → Providers tab to install it."
            )
        raise DriverError(f"Unknown driver protocol: {protocol!r}")
    return cls(device_id, meta)


def list_driver_types() -> list[dict[str, Any]]:
    """Return metadata for the UI dropdown in settings.html → Add device.

    Filtered to currently-loaded drivers so the user only sees protocols
    they can actually use right now. Provider state for the Providers
    tab comes from ``ProviderLoader.list_state()`` separately.
    """
    out: list[dict[str, Any]] = []
    for pid in DRIVERS:
        spec = get_provider(pid)
        if spec is None:
            continue
        out.append({
            "id": pid,
            "name": spec.get("name", pid),
            "needs_cloud": spec.get("needs_cloud", False),
            "fields": [],  # legacy field — front-end no longer uses it for known protocols
            "entity_types": spec.get("entity_types", []),
        })
    return out
