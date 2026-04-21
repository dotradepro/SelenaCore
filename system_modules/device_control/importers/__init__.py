"""
system_modules/device_control/importers — one-time migration helpers.

Each sub-package imports device records from another smart-home platform
and hands them off to the existing ``core.registry.create_device()`` path
with an appropriate ``protocol`` and ``meta`` payload. After import, the
source platform can be decommissioned — SelenaCore drives the devices
directly via its native drivers.

Importers are distinct from providers (see ``providers/catalog.py``):
providers are long-lived protocol adapters, importers run once.
"""
