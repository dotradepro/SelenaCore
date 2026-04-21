"""Process-wide BLE coordination primitives.

SelenaCore has (at least) two BLE consumers: the presence_detection
module (periodic ``BleakScanner`` scans for user-tracker beacons) and
the Plejd gateway (persistent GATT connection to a mesh device). Both
fight for the same hci0 adapter; without coordination, one's scan can
tear down the other's connection on every pass.

``arbiter`` is a cooperative reservation system. Every BLE user asks the
arbiter for a slot before touching the adapter; the arbiter serialises
operations and guarantees that long-lived GATT holders don't starve
short scan bursts or vice-versa. Nothing in this module actually talks
to BLE — it is pure asyncio scheduling glue.
"""

from .arbiter import BLEArbiter, BLEBusy, get_arbiter

__all__ = ["BLEArbiter", "BLEBusy", "get_arbiter"]
