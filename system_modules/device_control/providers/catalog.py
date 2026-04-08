"""
system_modules/device_control/providers/catalog.py

Static catalog of known device-protocol providers. Each entry maps a
protocol id (the value stored in ``Device.protocol``) to a pip package +
driver module + driver class. The catalog is the *only* place to declare
a new provider — adding one is a two-step process:

  1. Add an entry to ``PROVIDERS`` here.
  2. Create the driver class in ``system_modules/device_control/drivers/``.

The Providers tab in device-control settings shows every entry in this
dict. Built-in providers (``builtin: True``) are auto-detected and
enabled on first start if their package is already importable. Opt-in
providers are installed via the UI through ``ProviderLoader.install()``,
which runs ``pip install`` then registers the row in the
``driver_providers`` SQLite table.
"""
from __future__ import annotations

from typing import TypedDict


class ProviderSpec(TypedDict, total=False):
    id: str                 # short protocol id, matches Device.protocol
    name: str               # display name (untranslated)
    description: str        # short description (untranslated)
    package: str | None     # pip package name; None means stub/no install
    version: str            # version spec, e.g. ">=2.1"
    driver_module: str      # full python path to module containing driver class
    driver_class: str       # class name within driver_module
    entity_types: list[str]  # entity types this provider can produce
    needs_cloud: bool       # if True, provider also needs OAuth/cloud creds
    builtin: bool           # if True, ships with the container image
    icon: str               # short emoji or filename for the provider card
    homepage: str           # vendor / project URL
    needs_external_service: bool  # for adapters that bridge to other services


PROVIDERS: dict[str, ProviderSpec] = {
    # ── Built-in providers (pre-baked into the container) ──────────────
    "tuya_local": {
        "id": "tuya_local",
        "name": "Tuya / Smart Life (LAN)",
        "description": "Local control of Tuya-protocol devices over the LAN. "
                       "Persistent TCP socket on port 6668. Push-based DPS updates.",
        "package": "tinytuya",
        "version": ">=1.13.0",
        "driver_module": "system_modules.device_control.drivers.tuya_local",
        "driver_class": "TuyaLocalDriver",
        "entity_types": ["switch", "outlet", "light"],
        "needs_cloud": False,
        "builtin": True,
        "icon": "🟧",
        "homepage": "https://github.com/jasonacox/tinytuya",
    },
    "tuya_cloud": {
        "id": "tuya_cloud",
        "name": "Tuya / Smart Life (cloud)",
        "description": "Cloud control via Tuya user-code OAuth. Used as fallback "
                       "when LAN credentials are unavailable. Same SDK as HA 2024.2+.",
        "package": "tuya-device-sharing-sdk",
        "version": ">=0.2",
        "driver_module": "system_modules.device_control.drivers.tuya_cloud",
        "driver_class": "TuyaCloudDriver",
        "entity_types": ["switch", "outlet", "light"],
        "needs_cloud": True,
        "builtin": True,
        "icon": "☁️",
        "homepage": "https://github.com/tuya/tuya-device-sharing-sdk",
    },
    "gree": {
        "id": "gree",
        "name": "Gree / Pular WiFi A/C",
        "description": "Local control of Gree-protocol air conditioners "
                       "(Pular GWH12, Cooper&Hunter, EWT). UDP/7000 + AES-ECB.",
        "package": "greeclimate",
        "version": ">=2.1",
        "driver_module": "system_modules.device_control.drivers.gree",
        "driver_class": "GreeDriver",
        "entity_types": ["air_conditioner"],
        "needs_cloud": False,
        "builtin": True,
        "icon": "❄️",
        "homepage": "https://github.com/cmroche/greeclimate",
    },
    "mqtt": {
        "id": "mqtt",
        "name": "MQTT / Zigbee bridge",
        "description": "Stub driver — relays state via the protocol-bridge "
                       "module. No direct pip dependency.",
        "package": None,
        "version": "",
        "driver_module": "system_modules.device_control.drivers.mqtt_bridge",
        "driver_class": "MqttBridgeDriver",
        "entity_types": ["switch", "light", "sensor"],
        "needs_cloud": False,
        "builtin": True,
        "icon": "📡",
        "homepage": "",
    },

    # ── Opt-in providers (NOT pre-installed) ───────────────────────────
    "philips_hue": {
        "id": "philips_hue",
        "name": "Philips Hue (Bridge LAN)",
        "description": "Local control of Philips Hue lights via the Hue Bridge "
                       "LAN API. No cloud account required.",
        "package": "phue",
        "version": ">=1.1",
        "driver_module": "system_modules.device_control.drivers.philips_hue",
        "driver_class": "PhilipsHueDriver",
        "entity_types": ["light"],
        "needs_cloud": False,
        "builtin": False,
        "icon": "💡",
        "homepage": "https://github.com/studioimaginaire/phue",
    },
    "esphome": {
        "id": "esphome",
        "name": "ESPHome (native API)",
        "description": "Direct connection to ESPHome devices over the LAN "
                       "using the native asyncio API. Push-based.",
        "package": "aioesphomeapi",
        "version": ">=21.0",
        "driver_module": "system_modules.device_control.drivers.esphome",
        "driver_class": "ESPHomeDriver",
        "entity_types": ["switch", "light", "sensor", "outlet"],
        "needs_cloud": False,
        "builtin": False,
        "icon": "🔌",
        "homepage": "https://github.com/esphome/aioesphomeapi",
    },
    "zigbee2mqtt": {
        "id": "zigbee2mqtt",
        "name": "Zigbee2MQTT bridge",
        "description": "Adapter for the Zigbee2MQTT external service. Requires "
                       "a separately running Z2M instance with MQTT broker.",
        "package": None,
        "version": "",
        "driver_module": "system_modules.device_control.drivers.zigbee2mqtt",
        "driver_class": "Zigbee2MqttDriver",
        "entity_types": ["light", "switch", "sensor"],
        "needs_cloud": False,
        "builtin": False,
        "needs_external_service": True,
        "icon": "🐝",
        "homepage": "https://www.zigbee2mqtt.io/",
    },
    "matter": {
        "id": "matter",
        "name": "Matter / Thread",
        "description": "Universal IoT protocol with WiFi and Thread transports. "
                       "Requires the matter-server companion container "
                       "(``docker compose --profile matter up -d``); use "
                       "``--profile thread`` additionally for native Thread "
                       "devices via an nRF52840 Border Router.",
        "package": "python-matter-server[client]",
        "version": ">=6.0",
        "driver_module": "system_modules.device_control.drivers.matter",
        "driver_class": "MatterDriver",
        "entity_types": ["light", "switch", "outlet", "sensor", "lock", "thermostat"],
        "needs_cloud": False,
        "builtin": False,
        "needs_external_service": True,
        "icon": "◈",
        "homepage": "https://github.com/home-assistant-libs/python-matter-server",
    },
}


def get_provider(provider_id: str) -> ProviderSpec | None:
    """Look up a provider spec by id. Returns None for unknown ids."""
    return PROVIDERS.get(provider_id)


def builtin_provider_ids() -> list[str]:
    """Ids of providers that should auto-seed on first DB initialisation."""
    return [pid for pid, spec in PROVIDERS.items() if spec.get("builtin", False)]
