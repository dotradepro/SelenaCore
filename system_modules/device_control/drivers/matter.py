"""
system_modules/device_control/drivers/matter.py — Matter / Thread driver.

Talks to a sidecar matter-server container (python-matter-server) over its
WebSocket JSON-RPC API. One MatterClient instance is shared process-wide
across all MatterDriver instances — opening one WebSocket per Device PK
would burn file descriptors and complicate fabric management.

The Matter cluster model is rich (40+ clusters across the spec). This
driver intentionally covers only the entity_types declared in
``providers/catalog.py``: light, switch, outlet, sensor, lock, thermostat.
Adding new cluster support is a matter of extending ``CLUSTER_MAP``.

Per-device meta layout::

    {
        "matter": {
            "node_id": 4,            # set by /matter/commission route
            "vendor_id": 4660,       # optional, populated from node info
            "product_id": 24578,     # optional
            "endpoint": 1,           # default 1; some bridges expose multiple
        }
    }
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from typing import Any, AsyncGenerator, Callable

from .base import DeviceDriver, DriverError

logger = logging.getLogger(__name__)


# ── Cluster ↔ logical state mapping ──────────────────────────────────────
# Single source of truth for both read (Matter → logical) and write
# (logical → Matter). Keys: (cluster_id, attribute_name). Values:
# (logical_key, decode_fn, encode_fn). encode_fn is None for read-only attrs.

def _decode_temp(v: Any) -> float:
    """Matter encodes temperatures in centi-degrees C (2150 → 21.5°C)."""
    return float(v) / 100.0


def _encode_temp(v: Any) -> int:
    return int(round(float(v) * 100))


def _decode_lock(v: Any) -> bool:
    # Matter Door Lock cluster: 0=NotFullyLocked, 1=Locked, 2=Unlocked
    return int(v) == 1


def _encode_lock(v: Any) -> int:
    # Door Lock commands are sent via cluster commands, not attribute writes.
    # This encoder is unused; lock/unlock dispatch handled in set_state().
    return 1 if bool(v) else 2


CLUSTER_MAP: dict[tuple[int, str], tuple[str, Callable[[Any], Any], Callable[[Any], Any] | None]] = {
    # OnOff cluster (0x0006)
    (0x0006, "on_off"): ("on", bool, lambda v: bool(v)),
    # Level Control cluster (0x0008) — brightness 0-254
    (0x0008, "current_level"): ("brightness", lambda v: int(v), lambda v: int(v)),
    # Color Control cluster (0x0300) — color temperature in mireds
    (0x0300, "color_temperature_mireds"): ("colour_temp", lambda v: int(v), lambda v: int(v)),
    # Thermostat cluster (0x0201)
    (0x0201, "local_temperature"): ("temperature", _decode_temp, None),
    (0x0201, "occupied_heating_setpoint"): ("target_temp", _decode_temp, _encode_temp),
    (0x0201, "system_mode"): ("hvac_mode", lambda v: str(v), lambda v: str(v)),
    # Door Lock cluster (0x0101) — read attribute, write via command (see below)
    (0x0101, "lock_state"): ("locked", _decode_lock, None),
    # Boolean State cluster (0x0045) — contact / occupancy sensors
    (0x0045, "state_value"): ("contact", bool, None),
}

#: Reverse lookup: logical key → list of (cluster_id, attribute_name) it
#: maps to. Built lazily on first use.
_LOGICAL_TO_MATTER: dict[str, list[tuple[int, str]]] = {}


def _logical_to_matter(key: str) -> list[tuple[int, str]]:
    if not _LOGICAL_TO_MATTER:
        for (cid, attr), (lkey, _dec, _enc) in CLUSTER_MAP.items():
            _LOGICAL_TO_MATTER.setdefault(lkey, []).append((cid, attr))
    return _LOGICAL_TO_MATTER.get(key, [])


# ── Shared client singleton ──────────────────────────────────────────────


class _Sentinel:
    """Marker pushed into per-device queues when the upstream WebSocket dies.

    The watcher loop in ``DeviceControlModule._watch_device`` reads
    ``stream_events`` until it sees this and re-raises the wrapped error,
    which triggers the standard reconnect-with-backoff path.
    """

    def __init__(self, exc: Exception) -> None:
        self.exc = exc


class _MatterClientHolder:
    """Process-wide singleton wrapping the matter_server.client.MatterClient.

    Lazily imports the client library so installations without Matter
    support never pay the import cost. All MatterDriver instances share the
    same WebSocket — node subscriptions are tracked per device_id and the
    matching attribute deltas are forwarded to the right device queue.
    """

    def __init__(self) -> None:
        self._client: Any = None
        self._session: Any = None
        self._listen_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        #: device_id → asyncio.Queue[dict | _Sentinel]
        self._queues: dict[str, asyncio.Queue] = {}
        #: device_id → node_id
        self._node_for_device: dict[str, int] = {}
        self._url = os.environ.get("MATTER_SERVER_URL", "ws://localhost:5580/ws")

    async def _ensure_connected(self) -> Any:
        async with self._lock:
            if self._client is not None:
                return self._client
            try:
                # Lazy import — only fails when matter is actually used.
                from matter_server.client.client import MatterClient  # type: ignore
                import aiohttp  # type: ignore
            except ImportError as exc:
                raise DriverError(
                    "matter provider not installed — open device-control "
                    "settings → Providers → Matter and click Install"
                ) from exc

            session = aiohttp.ClientSession()
            client = MatterClient(self._url, session)
            init_ready: asyncio.Event = asyncio.Event()
            try:
                await client.connect()
                # start_listening() is a long-running coroutine that loops
                # over WebSocket frames forever. We must NOT await it
                # directly — spawn it as a task and wait for init_ready
                # which the client sets after the SERVER_INFO handshake.
                self._listen_task = asyncio.create_task(
                    self._run_listener(client, session, init_ready),
                    name="matter_listen",
                )
                # Bound the wait so a stuck matter-server doesn't deadlock
                # the whole core startup.
                try:
                    await asyncio.wait_for(init_ready.wait(), timeout=15.0)
                except asyncio.TimeoutError:
                    self._listen_task.cancel()
                    raise DriverError(
                        "matter-server did not become ready within 15s"
                    )
            except DriverError:
                with contextlib.suppress(Exception):
                    await session.close()
                raise
            except Exception as exc:
                with contextlib.suppress(Exception):
                    await session.close()
                raise DriverError(f"matter-server connect failed: {exc}") from exc

            client.subscribe_events(self._on_node_event)  # type: ignore[attr-defined]
            self._client = client
            self._session = session
            logger.info("matter: connected to %s", self._url)
            return self._client

    async def _run_listener(
        self, client: Any, session: Any, init_ready: asyncio.Event,
    ) -> None:
        """Wrap ``client.start_listening`` so we always clean up on exit.

        When the WebSocket dies (matter-server crashed, OOM, restart),
        ``start_listening`` returns or raises. Either way we must:
          1. Push a sentinel into every device queue so the watcher loops
             in DeviceControlModule._watch_device see a DriverError and
             trigger their backoff/reconnect path.
          2. Invalidate the singleton so the next ``_ensure_connected``
             call builds a fresh WebSocket instead of reusing the dead one.
        """
        exc: Exception | None = None
        try:
            await client.start_listening(init_ready)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            exc = e
            logger.warning("matter: listener died: %s", e)
        else:
            logger.warning("matter: listener exited cleanly (server closed)")
        finally:
            sentinel = _Sentinel(
                exc or DriverError("matter-server connection closed")
            )
            # Drain a sentinel into every active device queue so each
            # watcher loop wakes up exactly once.
            for did, queue in list(self._queues.items()):
                try:
                    queue.put_nowait(sentinel)
                except Exception:
                    pass
            # Invalidate the singleton so the next get_node()/send_command()
            # rebuilds the WebSocket from scratch.
            self._client = None
            self._listen_task = None
            with contextlib.suppress(Exception):
                await session.close()
            self._session = None

    def _on_node_event(self, event: Any) -> None:
        """Forward node attribute updates to the matching device queue."""
        try:
            node_id = getattr(event, "node_id", None)
            if node_id is None:
                return
            # Reverse-lookup which device(s) are bound to this node.
            for did, nid in self._node_for_device.items():
                if nid != node_id:
                    continue
                queue = self._queues.get(did)
                if queue is None:
                    continue
                # Translate the event into a logical state delta.
                delta = self._translate_node_event(event)
                if delta:
                    queue.put_nowait(delta)
        except Exception as exc:
            logger.debug("matter: node event dispatch error: %s", exc)

    @staticmethod
    def _translate_node_event(event: Any) -> dict[str, Any] | None:
        """Convert a matter_server attribute-change event into logical keys."""
        # The event shape from python-matter-server:
        #   event.data == {"path": (endpoint, cluster_id, attribute_id), "value": <new>}
        try:
            data = getattr(event, "data", None) or {}
            path = data.get("path") or ()
            if len(path) < 3:
                return None
            _endpoint, cluster_id, attribute_name = path[0], path[1], path[2]
        except Exception:
            return None

        key_pair = (int(cluster_id), str(attribute_name))
        mapping = CLUSTER_MAP.get(key_pair)
        if mapping is None:
            return None
        logical_key, decode_fn, _enc = mapping
        try:
            return {logical_key: decode_fn(data.get("value"))}
        except Exception as exc:
            logger.debug("matter: decode failed for %s: %s", key_pair, exc)
            return None

    async def get_node(self, node_id: int) -> Any:
        client = await self._ensure_connected()
        return await client.get_node(node_id)

    async def commission_with_code(self, setup_code: str) -> int:
        client = await self._ensure_connected()
        node = await client.commission_with_code(setup_code)
        return int(getattr(node, "node_id", node))

    async def remove_node(self, node_id: int) -> None:
        client = await self._ensure_connected()
        await client.remove_node(node_id)

    async def send_command(
        self, node_id: int, endpoint: int, cluster_id: int, command: str, payload: dict | None = None,
    ) -> Any:
        client = await self._ensure_connected()
        return await client.send_device_command(
            node_id=node_id,
            endpoint_id=endpoint,
            cluster_id=cluster_id,
            command_name=command,
            payload=payload or {},
        )

    def register_device(self, device_id: str, node_id: int) -> asyncio.Queue:
        """Allocate a per-device push queue and bind it to a Matter node."""
        queue: asyncio.Queue = asyncio.Queue()
        self._queues[device_id] = queue
        self._node_for_device[device_id] = node_id
        return queue

    def unregister_device(self, device_id: str) -> None:
        self._queues.pop(device_id, None)
        self._node_for_device.pop(device_id, None)

    async def shutdown(self) -> None:
        """Graceful teardown — cancels the listener and closes the WebSocket.

        Called from ``DeviceControlModule.stop()``. Idempotent.
        """
        async with self._lock:
            task = self._listen_task
            session = self._session
            self._client = None
            self._listen_task = None
            self._session = None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(BaseException):
                await task
        if session is not None:
            with contextlib.suppress(Exception):
                await session.close()


_HOLDER = _MatterClientHolder()


# ── Driver ────────────────────────────────────────────────────────────────


class MatterDriver(DeviceDriver):
    protocol = "matter"

    def __init__(self, device_id: str, meta: dict[str, Any]) -> None:
        super().__init__(device_id, meta)
        cfg = (meta or {}).get("matter") or {}
        self._node_id: int | None = cfg.get("node_id")
        self._endpoint: int = int(cfg.get("endpoint", 1))
        self._queue: asyncio.Queue | None = None

    async def connect(self) -> dict[str, Any]:
        if self._node_id is None:
            raise DriverError(
                f"MatterDriver {self.device_id}: meta.matter.node_id missing — "
                "device must be commissioned before use"
            )
        try:
            node = await _HOLDER.get_node(int(self._node_id))
        except DriverError:
            raise
        except Exception as exc:
            raise DriverError(
                f"matter: get_node({self._node_id}) failed: {exc}"
            ) from exc

        # Allocate the push queue exactly once per driver lifetime.
        if self._queue is None:
            self._queue = _HOLDER.register_device(self.device_id, int(self._node_id))

        return self._read_logical_state(node)

    async def disconnect(self) -> None:
        # Drop the per-device queue, but leave the shared client alive — other
        # Matter devices may still be using it.
        _HOLDER.unregister_device(self.device_id)
        self._queue = None

    async def set_state(self, state: dict[str, Any]) -> None:
        if not state or self._node_id is None:
            return
        for key, value in state.items():
            try:
                await self._dispatch_logical_write(key, value)
            except Exception as exc:
                raise DriverError(
                    f"matter set_state {self.device_id} {key}={value!r}: {exc}"
                ) from exc

    async def _dispatch_logical_write(self, key: str, value: Any) -> None:
        """Translate a single logical key write into a Matter cluster command."""
        # Door Lock — uses cluster commands, not attribute writes.
        if key == "locked":
            command = "LockDoor" if bool(value) else "UnlockDoor"
            await _HOLDER.send_command(
                node_id=int(self._node_id),  # type: ignore[arg-type]
                endpoint=self._endpoint,
                cluster_id=0x0101,
                command=command,
            )
            return

        # OnOff — On / Off / Toggle commands (preferred over attribute writes).
        if key == "on":
            command = "On" if bool(value) else "Off"
            await _HOLDER.send_command(
                node_id=int(self._node_id),  # type: ignore[arg-type]
                endpoint=self._endpoint,
                cluster_id=0x0006,
                command=command,
            )
            return

        # Level Control — MoveToLevel.
        if key == "brightness":
            await _HOLDER.send_command(
                node_id=int(self._node_id),  # type: ignore[arg-type]
                endpoint=self._endpoint,
                cluster_id=0x0008,
                command="MoveToLevel",
                payload={"level": int(value), "transition_time": 0,
                         "options_mask": 0, "options_override": 0},
            )
            return

        # Generic attribute writes (thermostat target_temp, hvac_mode, …).
        for cluster_id, attr in _logical_to_matter(key):
            mapping = CLUSTER_MAP[(cluster_id, attr)]
            _lkey, _dec, encode = mapping
            if encode is None:
                continue  # read-only — silently skip
            await _HOLDER.send_command(
                node_id=int(self._node_id),  # type: ignore[arg-type]
                endpoint=self._endpoint,
                cluster_id=cluster_id,
                command="WriteAttribute",
                payload={"attribute": attr, "value": encode(value)},
            )
            return

        logger.debug("matter: ignoring unmapped logical key %r", key)

    async def get_state(self) -> dict[str, Any]:
        if self._node_id is None:
            return {}
        try:
            node = await _HOLDER.get_node(int(self._node_id))
        except Exception as exc:
            raise DriverError(f"matter get_state {self.device_id}: {exc}") from exc
        return self._read_logical_state(node)

    def _read_logical_state(self, node: Any) -> dict[str, Any]:
        """Walk a node object's attributes and project them into logical keys."""
        out: dict[str, Any] = {}
        # python-matter-server exposes attributes via node.attributes which is
        # a {(endpoint, cluster_id, attribute_name): value} dict-like.
        attributes = getattr(node, "attributes", None)
        if attributes is None:
            return out
        try:
            items = attributes.items()
        except AttributeError:
            items = []
        for path, value in items:
            try:
                if len(path) < 3:
                    continue
                cluster_id, attr_name = int(path[1]), str(path[2])
            except Exception:
                continue
            mapping = CLUSTER_MAP.get((cluster_id, attr_name))
            if mapping is None:
                continue
            logical_key, decode_fn, _enc = mapping
            try:
                out[logical_key] = decode_fn(value)
            except Exception:
                continue
        return out

    async def stream_events(self) -> AsyncGenerator[dict[str, Any], None]:
        if self._queue is None:
            await self.connect()
        assert self._queue is not None
        while True:
            item = await self._queue.get()
            if isinstance(item, _Sentinel):
                raise DriverError(f"matter: upstream lost: {item.exc}")
            yield item
