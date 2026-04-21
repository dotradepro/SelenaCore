"""Singleton BLE gateway for Plejd mesh.

One persistent GATT connection to any single Plejd mesh node; the mesh
forwards every command to every output. Reconnect + backoff is built in
so a power blip on the connected device doesn't require user action.

Protocol overview:
    * Scan for devices advertising Plejd service UUID
      ``31ba0001-6085-4726-be45-040c957391b5``.
    * Pick the one with the strongest RSSI (closest, fewest retransmissions).
    * Connect + discover services.
    * Write encrypted frames to the LIGHT characteristic
      ``31ba0004-6085-4726-be45-040c957391b5``.
    * Subscribe to notifications on the same characteristic for state
      updates from other outputs.
    * Encryption: XOR-stream derived from the CONNECTED device's BLE
      address + the site key (``plejd.crypto`` module).

Frame layout (from the reference integrations):
    [0:1]  output_address   — which mesh output to target
    [1:2]  opcode           — 0x00 dim absolute, 0x01 on, 0x02 off (varies)
    [2:4]  (varies by cmd)
    [4:6]  dim level        — 16-bit, present only for dim commands

The gateway exposes three methods used by the driver:
    * ``start()`` / ``stop()`` — lifecycle (spawn reconnect task)
    * ``send_on(output_address, level)`` — set brightness 0-65535 or
      plain on/off
    * ``subscribe(callback)`` — receive state notifications

Real BLE IO (``bleak``) is imported lazily so tests without bleak
installed can still exercise the scheduling / encryption / arbitration
logic. The bleak client itself is injected as a factory so tests can
stub it entirely.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from core.ble.arbiter import BLEArbiter, get_arbiter

from . import crypto

logger = logging.getLogger(__name__)

#: Plejd BLE service + characteristic UUIDs (stable across all firmware).
PLEJD_SERVICE_UUID = "31ba0001-6085-4726-be45-040c957391b5"
PLEJD_DATA_UUID    = "31ba0004-6085-4726-be45-040c957391b5"
PLEJD_LAST_DATA_UUID = "31ba0005-6085-4726-be45-040c957391b5"
PLEJD_AUTH_UUID    = "31ba0009-6085-4726-be45-040c957391b5"
PLEJD_PING_UUID    = "31ba000a-6085-4726-be45-040c957391b5"

#: Minimum / maximum backoff between reconnect attempts (seconds).
_RECONNECT_MIN = 2.0
_RECONNECT_MAX = 60.0

#: Opcodes actually emitted by Plejd devices — verified against bolstad's
#: JS integration. 16-bit big-endian after the leading output byte.
_OPCODE_STATE  = 0x00c8
_OPCODE_DIM_ON = 0x0098
_OPCODE_OFF    = 0x0097


@dataclass
class PlejdEvent:
    """Notification decoded from the Plejd mesh."""
    output_address: int
    on: bool
    dim_level: int | None = None
    raw: bytes = b""


@dataclass
class _ScanResult:
    ble_address: str            # "AA:BB:CC:DD:EE:FF"
    rssi: int
    raw: Any = None             # bleak's BLEDevice (or mock in tests)


# ── Pure encoders (testable without BLE) ──────────────────────────────────


def encode_on(output_address: int, dim_level: int | None = None) -> bytes:
    """Build the cleartext frame that turns an output on.

    ``dim_level`` is 0–65535. None = plain on without brightness tweak.
    """
    if not (0 <= output_address <= 255):
        raise ValueError(f"output_address out of range: {output_address}")
    out = bytearray()
    out.append(output_address)
    out.append(0x01)
    out.append(0x10)
    out.append(0x00)
    if dim_level is not None:
        if not (0 <= dim_level <= 0xFFFF):
            raise ValueError(f"dim_level out of range: {dim_level}")
        out.append((dim_level >> 8) & 0xFF)
        out.append(dim_level & 0xFF)
    return bytes(out)


def encode_off(output_address: int) -> bytes:
    if not (0 <= output_address <= 255):
        raise ValueError(f"output_address out of range: {output_address}")
    return bytes([output_address, 0x01, 0x10, 0x00, 0x00, 0x00])


def decode_event(data: bytes, site_key: bytes, connected_addr: bytes) -> PlejdEvent | None:
    """Decrypt + parse a notification frame.

    Returns None for frames we don't know how to interpret — callers
    skip them rather than raise.
    """
    if not data:
        return None
    payload = crypto.encrypt_decrypt(site_key, connected_addr, data)
    if len(payload) < 5:
        return None
    output_address = payload[0]
    # The opcode lives in bytes 3-4 (big-endian 16-bit).
    opcode = (payload[3] << 8) | payload[4] if len(payload) >= 5 else 0
    dim_level: int | None = None
    on = False
    if opcode == _OPCODE_DIM_ON or opcode == _OPCODE_STATE:
        # Dim on / state frame: dim_level in bytes 5-6.
        if len(payload) >= 7:
            dim_level = (payload[5] << 8) | payload[6]
            on = dim_level > 0
        else:
            on = True
    elif opcode == _OPCODE_OFF:
        on = False
    else:
        return None
    return PlejdEvent(
        output_address=output_address,
        on=on,
        dim_level=dim_level,
        raw=payload,
    )


# ── Bleak backend abstraction ────────────────────────────────────────────
#
# Real BLE IO goes through this small interface so tests can stub it
# without a BT adapter. The production implementation lives at the
# bottom of the module and imports bleak lazily.


class BleakBackend:
    """Narrow interface over the bits of bleak we use."""

    async def scan(self, service_uuid: str, timeout: float) -> list[_ScanResult]:
        raise NotImplementedError

    async def connect(self, ble_address: str) -> Any:
        """Return an opaque ``client`` that supports write_gatt_char,
        start_notify, stop_notify, disconnect."""
        raise NotImplementedError


class _RealBleakBackend(BleakBackend):
    """Thin shim over ``bleak``. Only imported at runtime."""

    async def scan(self, service_uuid: str, timeout: float) -> list[_ScanResult]:
        from bleak import BleakScanner
        devices = await BleakScanner.discover(
            timeout=timeout, service_uuids=[service_uuid],
        )
        out: list[_ScanResult] = []
        for d in devices:
            rssi = getattr(d, "rssi", None)
            if rssi is None:
                rssi = getattr(getattr(d, "metadata", {}), "get", lambda *_: None)("rssi")
            out.append(_ScanResult(
                ble_address=str(d.address),
                rssi=int(rssi) if rssi is not None else -127,
                raw=d,
            ))
        return out

    async def connect(self, ble_address: str) -> Any:
        from bleak import BleakClient
        client = BleakClient(ble_address)
        await client.connect()
        return client


# ── Gateway ──────────────────────────────────────────────────────────────


class PlejdGateway:
    """One-per-process BLE gateway for a Plejd mesh.

    Ownership of ``bleak`` is hidden behind ``BleakBackend`` so unit
    tests plug in a fake. The arbiter is injected so tests can use an
    isolated instance.
    """

    def __init__(
        self,
        *,
        site_key: bytes,
        backend: BleakBackend | None = None,
        arbiter: BLEArbiter | None = None,
    ) -> None:
        if len(site_key) != 16:
            raise ValueError("site_key must be 16 bytes")
        self._site_key = bytes(site_key)
        self._backend = backend or _RealBleakBackend()
        self._arbiter = arbiter or get_arbiter()
        self._client: Any = None
        self._connected_addr: bytes | None = None
        self._run_task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._subscribers: list[Callable[[PlejdEvent], Awaitable[None] | None]] = []
        self._write_lock = asyncio.Lock()
        self.last_error: str | None = None

    # ── Lifecycle ────────────────────────────────────────────────────

    async def start(self) -> None:
        """Kick off the scan/connect/reconnect loop as a background task.

        Returns immediately — the task lives until ``stop()`` is called.
        """
        if self._run_task is not None and not self._run_task.done():
            return
        self._stop_event.clear()
        self._run_task = asyncio.create_task(self._run(), name="plejd-gateway")

    async def stop(self) -> None:
        """Stop the reconnect loop + drop the current GATT connection."""
        self._stop_event.set()
        task = self._run_task
        self._run_task = None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await self._teardown()

    def subscribe(self, callback: Callable[[PlejdEvent], Awaitable[None] | None]) -> None:
        """Register a callback for decoded state events. Callbacks may
        be sync or async; gateway awaits async ones."""
        self._subscribers.append(callback)

    # ── Commands (thin public API) ───────────────────────────────────

    async def send_on(self, output_address: int, dim_level: int | None = None) -> None:
        await self._send(encode_on(output_address, dim_level))

    async def send_off(self, output_address: int) -> None:
        await self._send(encode_off(output_address))

    # ── Internals ────────────────────────────────────────────────────

    def is_connected(self) -> bool:
        return self._client is not None and self._connected_addr is not None

    async def _send(self, cleartext: bytes) -> None:
        if not self.is_connected():
            raise RuntimeError("plejd gateway not connected")
        async with self._write_lock:
            ct = crypto.encrypt_decrypt(self._site_key, self._connected_addr, cleartext)
            await self._client.write_gatt_char(PLEJD_DATA_UUID, ct, response=True)

    async def _run(self) -> None:
        """Scan → connect → hold forever, reconnecting on drop."""
        backoff = _RECONNECT_MIN
        while not self._stop_event.is_set():
            try:
                ok = await self._scan_and_connect()
            except asyncio.CancelledError:
                raise
            except Exception as exc:   # pragma: no cover — defensive
                logger.warning("plejd gateway scan/connect crashed: %s", exc)
                self.last_error = str(exc)
                ok = False
            if not ok:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, _RECONNECT_MAX)
                continue
            backoff = _RECONNECT_MIN
            # Hold the connection until it drops or stop is called.
            await self._hold_connection()

    async def _scan_and_connect(self) -> bool:
        async with self._arbiter.slot("plejd_gateway_scan"):
            try:
                candidates = await self._backend.scan(PLEJD_SERVICE_UUID, timeout=8.0)
            except Exception as exc:
                self.last_error = f"scan failed: {exc}"
                logger.warning("plejd scan failed: %s", exc)
                return False
        if not candidates:
            self.last_error = "no Plejd devices advertising on this adapter"
            return False
        # Strongest signal wins.
        best = max(candidates, key=lambda c: c.rssi)
        addr_bytes = crypto.parse_addr(best.ble_address)
        async with self._arbiter.slot("plejd_gateway_connect"):
            try:
                client = await self._backend.connect(best.ble_address)
            except Exception as exc:
                self.last_error = f"connect to {best.ble_address} failed: {exc}"
                logger.warning("plejd connect failed: %s", exc)
                return False
        self._client = client
        self._connected_addr = addr_bytes
        self.last_error = None
        logger.info("plejd gateway connected to %s (rssi=%d)",
                    best.ble_address, best.rssi)
        try:
            await client.start_notify(PLEJD_DATA_UUID, self._on_notify)
        except Exception as exc:
            logger.warning("plejd start_notify failed: %s", exc)
        return True

    async def _hold_connection(self) -> None:
        try:
            # Poll is_connected on the bleak client; drop out when it's gone.
            while not self._stop_event.is_set():
                client = self._client
                if client is None:
                    return
                is_conn = getattr(client, "is_connected", None)
                if callable(is_conn):
                    try:
                        alive = is_conn() if not asyncio.iscoroutinefunction(is_conn) else await is_conn()
                    except Exception:
                        alive = False
                    if not alive:
                        return
                await asyncio.sleep(1.0)
        finally:
            await self._teardown()

    async def _teardown(self) -> None:
        client = self._client
        self._client = None
        self._connected_addr = None
        if client is not None:
            for coro in (
                getattr(client, "stop_notify", None),
                getattr(client, "disconnect", None),
            ):
                if coro is None:
                    continue
                try:
                    r = coro(PLEJD_DATA_UUID) if coro.__name__ == "stop_notify" else coro()
                    if asyncio.iscoroutine(r):
                        await r
                except Exception:
                    pass

    async def _on_notify(self, sender: Any, data: bytes) -> None:
        if self._connected_addr is None:
            return
        event = decode_event(bytes(data), self._site_key, self._connected_addr)
        if event is None:
            return
        for cb in list(self._subscribers):
            try:
                res = cb(event)
                if asyncio.iscoroutine(res):
                    await res
            except Exception:
                logger.warning("plejd subscriber crashed", exc_info=True)


# ── Singleton accessor ───────────────────────────────────────────────────


_INSTANCE: PlejdGateway | None = None


def get_gateway() -> PlejdGateway | None:
    """Return the process-wide gateway if initialised, else None."""
    return _INSTANCE


def set_gateway(gw: PlejdGateway | None) -> None:
    """Install the process-wide gateway. Tests pass None to reset."""
    global _INSTANCE
    _INSTANCE = gw
