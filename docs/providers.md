# Provider System

`device-control` is a runtime-pluggable provider system. Each provider is a Python package that implements a `DeviceDriver` interface and can be installed without rebuilding the container or restarting the core.

This document is the user-facing intro. For implementation internals — `DriverProvider` ORM, hot-reload contract, restart resilience, integrity-agent compatibility — see [provider-system-and-modules.md](provider-system-and-modules.md).

## What problem it solves

Smart home protocols change constantly. Adding a new vendor used to mean rebuilding the Docker image and shipping a new release. With the provider system:

- New device families ship as **opt-in packages** that install in one click.
- The Integrity Agent's hash manifest is unaffected — providers live outside `/opt/selena-core/core/**/*.py`.
- Failed installs are isolated and surfaced through `last_error` in the provider card.
- Removing a provider is a single click; the Device Registry keeps its devices but reports them offline until another provider claims them.

## Built-in providers

Pre-installed and always available.

| Provider     | Protocol            | Package         | Entity types                | Cloud account |
|--------------|---------------------|-----------------|-----------------------------|---------------|
| `tuya_local` | Tuya LAN API        | `tinytuya`      | light, switch, outlet, A/C  | ❌            |
| `tuya_cloud` | Tuya Sharing SDK    | `tuya-sharing`  | All Tuya categories         | ❌            |
| `gree`       | Gree UDP / AES-ECB  | `greeclimate`   | air_conditioner             | ❌            |
| `mqtt`       | MQTT bridge (relay) | (uses `protocol_bridge`) | any                | ❌            |

`tuya_cloud` does not need a Tuya developer account — it uses the same Device Sharing SDK that the Smart Life mobile app uses.

`gree` covers the entire Gree-protocol family: Pular, Cooper&Hunter, EWT, Ewpe Smart and most rebadged units.

## Opt-in providers

Not pre-installed. Install from the UI when you need them.

| Provider      | Protocol             | Package | Notes               |
|---------------|----------------------|---------|---------------------|
| `philips_hue` | Hue Bridge LAN API   | `phue`  | Requires bridge IP  |
| `esphome`     | Native asyncio API   | `aioesphomeapi` | Push-based      |

More providers can be added over time without changing the core.

## Installing a provider

### From the UI (recommended)

1. Open **Settings → device-control → Providers**.
2. Find the provider in the catalog.
3. Click **Install**. Pip runs in a background thread; progress is shown in the card.
4. When status flips to `loaded`, click **Scan** (or import devices) to register hardware.

### From the API

```http
POST /api/ui/modules/device-control/providers/{provider_id}/install
Authorization: Bearer <module_token>
```

Status is then visible at:

```http
GET /api/ui/modules/device-control/providers
```

The same endpoints expose `uninstall` and `reload`.

## Importing devices

Each provider has its own import flow. The most common ones:

- **Tuya local:** scan the LAN — devices that respond to the Tuya discovery beacon are listed for import.
- **Tuya cloud:** sign in via the Tuya Sharing SDK QR flow.
- **Gree / Pular:** UDP broadcast scan on the local subnet.
- **Philips Hue:** auto-discover the bridge, press the link button, import lights and groups.
- **ESPHome:** point the import flow at an mDNS-discovered host or paste the device address.

When a device is imported, `device-control` publishes `device.registered` with an enriched payload (`entity_type`, `location`, `capabilities`). Three modules listen and react automatically:

| Subscriber        | Reaction                                                |
|-------------------|---------------------------------------------------------|
| `energy_monitor`  | Auto-creates an energy source                           |
| `climate`         | Invalidates room cache when `entity_type=air_conditioner|thermostat` |
| `lights_switches` | Invalidates room cache when `entity_type=light|switch|outlet` |

You don't have to wire any of this manually.

## Building your own provider

A provider is a Python package that exports a class implementing `DeviceDriver`:

```python
class DeviceDriver:
    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    async def send_command(self, device_id: str, command: dict) -> dict: ...
    async def get_state(self, device_id: str) -> dict: ...
    async def discover(self) -> list[dict]: ...
```

Register it in `system_modules/device_control/providers/catalog.py` (for built-ins) or ship it as a pip-installable package referenced from a custom catalog entry.

The full developer contract — `DriverProvider` ORM, the loader, the hot-reload protocol, the integrity-agent boundary — is documented in [provider-system-and-modules.md](provider-system-and-modules.md).

## Troubleshooting

- **`last_error` shown in the card** — open the provider card; the error message is verbatim from pip or the loader.
- **Provider stuck in `installing`** — check `docker compose logs selena-core --tail=200`. Pip output is logged with the `device_control.providers` logger.
- **Devices reported offline after uninstall** — expected. Reinstall the provider or hand the devices to another one.
- **Integrity Agent flagged a provider** — providers must be installed under `/var/lib/selena/providers/` (outside the core hash glob). If you see this, file an issue.

## See also

- [provider-system-and-modules.md](provider-system-and-modules.md) — internals
- [climate-and-gree.md](climate-and-gree.md) — Gree protocol details
- [modules.md#device_control](modules.md#device_control) — module reference
