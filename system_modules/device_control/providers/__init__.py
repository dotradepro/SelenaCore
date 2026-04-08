"""Provider system — pluggable smart-device protocol libraries.

A *provider* maps a protocol id (e.g. ``gree``, ``tuya_local``) to a
pip-installable Python package and a concrete ``DeviceDriver`` subclass.
``ProviderLoader`` reads the ``driver_providers`` SQLite table on startup,
attempts to import each enabled provider's driver module, and exposes
the result as the runtime ``DRIVERS`` dict.

This decouples device-control from any specific protocol library:
greeclimate / tinytuya / phue / etc. are no longer eager imports — they
become runtime opt-ins managed via the Providers tab in settings.
"""
from .catalog import PROVIDERS, ProviderSpec  # noqa: F401
from .loader import ProviderLoader, ProviderError  # noqa: F401
