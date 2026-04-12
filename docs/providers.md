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

| Provider       | Protocol             | Package                        | Entity types            | Notes                                      |
|----------------|----------------------|--------------------------------|-------------------------|--------------------------------------------|
| `philips_hue`  | Hue Bridge LAN API   | `phue`                         | light                   | Poll-based (3 s), bridge button-press required on first pairing |
| `esphome`      | ESPHome native API   | `aioesphomeapi`                | switch, light, sensor, outlet | Push-based, auto-discovers entities on connect |
| `zigbee2mqtt`  | Zigbee2MQTT MQTT bridge | *(none — uses protocol_bridge)* | light, switch, sensor   | Requires running Z2M instance + MQTT broker |
| `matter`       | Matter / Thread      | `python-matter-server[client]` | light, switch, outlet, sensor, lock, thermostat | Requires matter-server sidecar container   |

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
- **Philips Hue:** enter bridge IP, press the physical link button on the bridge, then add lights by ID.
- **ESPHome:** enter device IP (port 6053 by default); the driver auto-discovers all entities on connect.
- **Zigbee2MQTT:** add devices with their Z2M `friendly_name`; state is relayed through `protocol_bridge`.
- **Matter:** enter the setup code from the device; the matter-server sidecar commissions and manages it.

When a device is imported, `device-control` publishes `device.registered` with an enriched payload (`entity_type`, `location`, `capabilities`). Three modules listen and react automatically:

| Subscriber        | Reaction                                                |
|-------------------|---------------------------------------------------------|
| `energy_monitor`  | Auto-creates an energy source                           |
| `climate`         | Invalidates room cache when `entity_type=air_conditioner|thermostat` |
| `lights_switches` | Invalidates room cache when `entity_type=light|switch|outlet` |

You don't have to wire any of this manually.

## Building your own provider

A provider is a Python package that exports a class implementing `DeviceDriver` (`system_modules/device_control/drivers/base.py`):

```python
class DeviceDriver(ABC):
    protocol: str = ""

    def __init__(self, device_id: str, meta: dict[str, Any]) -> None: ...

    async def connect(self) -> dict[str, Any]:        # open connection, return initial state
    async def disconnect(self) -> None:                 # close resources (idempotent)
    async def set_state(self, state: dict) -> None:     # apply partial state update
    async def get_state(self) -> dict[str, Any]:        # return current state snapshot
    def stream_events(self) -> AsyncGenerator[dict]:    # push loop (yields state diffs)
    def consume_metering(self) -> dict | None:          # optional power reading
```

Three driver patterns exist in the codebase:

| Pattern | Example | When to use |
|---------|---------|-------------|
| **EventBus delegation** | `mqtt_bridge`, `zigbee2mqtt` | Protocol handled by another module (e.g. `protocol_bridge`) |
| **Poll + diff** | `gree`, `philips_hue` | Device doesn't push; poll every N seconds, yield on change |
| **Push + queue** | `matter`, `esphome` | Library provides a push callback; route into `asyncio.Queue` |

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
