# Matter / Thread support

SelenaCore can pair and control Matter devices via the official
[python-matter-server](https://github.com/home-assistant-libs/python-matter-server)
sidecar container. Matter is the universal IoT protocol; Thread is one of
its physical transports (the other being WiFi). From SelenaCore's point of
view both look identical — a commissioned device shows up as an ordinary
`Device` row with `protocol='matter'` and is controlled through the same
voice intents (`device.on`, `device.off`, `device.lock`, …) as Tuya, Gree,
or Zigbee devices.

## Requirements

| Component | Why | When |
|---|---|---|
| `matter-server` container | WebSocket bridge to the Matter fabric | Always (any Matter device) |
| Bluetooth radio | BLE commissioning of new devices | First-time pairing |
| OpenThread Border Router (`otbr`) | Bridge between WiFi LAN and Thread mesh | **Only** for native Thread devices |
| nRF52840 USB dongle | Required by `otbr` | **Only** for native Thread devices |

Most consumer Matter bulbs, plugs, and switches sold today are
**Matter-over-WiFi** — they speak Matter directly over your WiFi network
and need only `matter-server`, not OTBR. The Thread border router is only
required for low-power battery devices that use Thread's mesh radio
(typically advertised as "Thread" or "Matter over Thread").

## Starting the services

Both companion containers are gated by docker compose `profiles` so users
without Matter devices don't pay the BLE / multicast cost.

```bash
# WiFi-only Matter (most users):
docker compose --profile matter up -d

# WiFi + Thread (battery devices, requires nRF52840 dongle on /dev/ttyACM0):
docker compose --profile matter --profile thread up -d
```

`matter-server` exposes its WebSocket on `ws://localhost:5580/ws`. The
URL is configurable via `MATTER_SERVER_URL` in `.env`.

## Installing the provider

Open device-control settings → **Providers** tab → find **Matter / Thread**
→ click **Install**. This runs `pip install python-matter-server[client]`
inside the core container and registers the driver class. After install
the **Matter / Thread** tab becomes the recommended way to pair devices.

## Pairing a device

1. Open device-control settings → **Matter / Thread** tab.
2. Read the device's QR code or 11-digit setup code from its label /
   packaging. Matter QR codes start with `MT:`.
3. Paste it into **Setup code**, give the device a friendly name, choose
   the **Entity type** (light / switch / outlet / lock / thermostat /
   sensor) and click **Pair device**.
4. Watch the spinner — Matter commissioning takes 10-30 seconds. The
   first commissioning may take longer (up to 60 seconds) because BLE
   discovery + WiFi/Thread provisioning happen back-to-back.
5. On success the device appears in the **Paired Matter devices** list
   and immediately works through the **Devices** tab + voice commands.

If pairing fails:

- **Setup code rejected** — re-read the QR / re-type the manual code.
- **`matter-server connect failed`** — check that the matter-server
  container is running: `docker compose --profile matter ps`.
- **Timeout after 60s** — the device is too far from the hub or its BLE
  radio is asleep. Power-cycle the device and try again.

## Supported entity types and clusters

| Entity type | Clusters | Logical state keys |
|---|---|---|
| `light` | OnOff, Level Control, Color Control | `on`, `brightness`, `colour_temp` |
| `switch` / `outlet` | OnOff | `on` |
| `lock` / `door_lock` | Door Lock | `locked` |
| `thermostat` | Thermostat | `temperature`, `target_temp`, `hvac_mode` |
| `sensor` | Boolean State + others | `contact` |

The full mapping table lives in `CLUSTER_MAP` at the top of
[system_modules/device_control/drivers/matter.py](../system_modules/device_control/drivers/matter.py).
Adding a new cluster is one entry in that table plus, if the cluster
needs a custom command (like Door Lock), a branch in
`MatterDriver._dispatch_logical_write`.

## Voice commands

Matter devices use the same English-only intent system as everything
else (see [module-bus-protocol.md](module-bus-protocol.md)). When a
Matter device is commissioned, the **PatternGenerator** automatically
creates English regex patterns from its `name_en` (or `name`) and
`entity_type`:

- `light` / `switch` / `outlet` → `device.on` + `device.off`
- `lock` / `door_lock` → `device.lock` + `device.unlock`
- `thermostat` / `air_conditioner` → `device.set_temperature` +
  `device.on` + `device.off`

For non-English speech, no localised patterns are generated — the LLM
tier (Tier 3) classifies any-language input and returns an English
intent name, which then matches the auto-generated patterns above.

## Removing a device

In the **Matter / Thread** tab, click **Remove** next to the device.
This calls the `remove_node` RPC on `matter-server`, which both
decommissions the node from the fabric and forgets its credentials, and
deletes the matching `Device` row from the SelenaCore registry.

## Troubleshooting

- `pip install` of `python-matter-server[client]` fails on ARM:
  ensure the host has `python3-dev` and a recent OpenSSL — Matter pulls
  in `cryptography` which compiles native code on platforms without
  prebuilt wheels.
- Devices appear in the list but don't respond to commands: check the
  matter-server container logs (`docker logs selena-matter-server`) for
  attribute subscription errors. The most common cause is the device's
  endpoint number not being `1` — set
  `meta.matter.endpoint` on the Device row to the correct value.
- Thread devices never join: confirm that `otbr` has formed a Thread
  network. The OTBR web UI is on `http://<host>:8080`.
