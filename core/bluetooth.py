"""
core/bluetooth.py — async wrapper around `bluetoothctl` (bluez).

Runs inside the selena-core container. The host DBus system bus is
bind-mounted (/var/run/dbus/system_bus_socket) and /var/lib/bluetooth
is volume-mounted so paired-device state survives restarts.

Two flavours of operation:

  * One-shot commands (status, devices, scan, power, connect, …) —
    spawned with `asyncio.create_subprocess_exec`, stdout parsed, done.

  * Pair sessions — long-lived interactive bluetoothctl processes that
    register a pairing agent, feed `pair <mac>` into stdin and stream
    agent prompts (PIN / numeric comparison) to the UI via
    `PairSession.events`. The UI responds through `PairSession.respond()`
    which writes back into stdin.

No shell=True, no eval, every subprocess call wrapped in
`asyncio.wait_for()` so a dead adapter can never hang a request.
"""
from __future__ import annotations

import asyncio
import logging
import re
import shutil
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

MAC_RE = re.compile(r"^[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}$")
_DEV_LINE_RE = re.compile(r"^Device\s+([0-9A-Fa-f:]{17})\s+(.+)$")

DEFAULT_TIMEOUT = 15.0
SCAN_TIMEOUT = 10


def _is_mac(value: str) -> bool:
    return bool(MAC_RE.match(value or ""))


def bluetoothctl_available() -> bool:
    return shutil.which("bluetoothctl") is not None


async def _run(*args: str, timeout: float = DEFAULT_TIMEOUT) -> tuple[int, str, str]:
    """Run `bluetoothctl <args>` non-interactively. Returns (rc, stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        "bluetoothctl",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise
    return proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")


# ================================================================== #
#  Adapter status                                                      #
# ================================================================== #

async def get_adapter_status() -> dict[str, Any]:
    """Return adapter info. `available=False` means no controller at all."""
    if not bluetoothctl_available():
        return {"available": False, "reason": "bluetoothctl_missing"}
    try:
        rc, out, _ = await _run("show")
    except asyncio.TimeoutError:
        return {"available": False, "reason": "timeout"}
    if rc != 0 or "No default controller available" in out:
        return {"available": False, "reason": "no_controller"}

    info: dict[str, Any] = {
        "available": True,
        "powered": False,
        "discovering": False,
        "pairable": False,
        "address": None,
        "name": None,
        "alias": None,
    }
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("Controller "):
            info["address"] = line.split()[1]
        elif line.startswith("Name:"):
            info["name"] = line.split(":", 1)[1].strip()
        elif line.startswith("Alias:"):
            info["alias"] = line.split(":", 1)[1].strip()
        elif line.startswith("Powered:"):
            info["powered"] = "yes" in line.lower()
        elif line.startswith("Discovering:"):
            info["discovering"] = "yes" in line.lower()
        elif line.startswith("Pairable:"):
            info["pairable"] = "yes" in line.lower()
    return info


async def set_power(on: bool) -> bool:
    rc, out, _ = await _run("power", "on" if on else "off")
    return rc == 0 and "succeeded" in out.lower()


# ================================================================== #
#  Device listing                                                      #
# ================================================================== #

def _parse_info_block(text: str) -> dict[str, Any]:
    d: dict[str, Any] = {
        "connected": False,
        "paired": False,
        "trusted": False,
        "icon": None,
        "alias": None,
        "name": None,
    }
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("Name:"):
            d["name"] = line.split(":", 1)[1].strip()
        elif line.startswith("Alias:"):
            d["alias"] = line.split(":", 1)[1].strip()
        elif line.startswith("Icon:"):
            d["icon"] = line.split(":", 1)[1].strip()
        elif line.startswith("Paired:"):
            d["paired"] = "yes" in line.lower()
        elif line.startswith("Trusted:"):
            d["trusted"] = "yes" in line.lower()
        elif line.startswith("Connected:"):
            d["connected"] = "yes" in line.lower()
    return d


async def _info(mac: str) -> dict[str, Any]:
    try:
        _, out, _ = await _run("info", mac)
    except asyncio.TimeoutError:
        return {"mac": mac, "name": mac, "error": "timeout"}
    d = _parse_info_block(out)
    d["mac"] = mac
    if not d["name"]:
        d["name"] = d["alias"] or mac
    return d


async def _devices_raw(filter_: str | None = None) -> list[tuple[str, str]]:
    """Return [(mac, name)] pairs. filter_ ∈ {None, "Paired", "Connected"}."""
    args = ["devices"] + ([filter_] if filter_ else [])
    try:
        _, out, _ = await _run(*args)
    except asyncio.TimeoutError:
        return []
    pairs: list[tuple[str, str]] = []
    for line in out.splitlines():
        m = _DEV_LINE_RE.match(line.strip())
        if m:
            pairs.append((m.group(1), m.group(2).strip()))
    return pairs


async def list_paired() -> list[dict[str, Any]]:
    pairs = await _devices_raw("Paired")
    if not pairs:
        return []
    # Parallel info lookup, bounded concurrency via gather
    infos = await asyncio.gather(*(_info(mac) for mac, _ in pairs), return_exceptions=True)
    result: list[dict[str, Any]] = []
    for (mac, name), info in zip(pairs, infos):
        if isinstance(info, Exception):
            result.append({"mac": mac, "name": name, "connected": False, "paired": True})
        else:
            if not info.get("name"):
                info["name"] = name
            result.append(info)
    return result


def _looks_like_mac_placeholder(name: str, mac: str) -> bool:
    """True if `name` is just the MAC rendered as a string (no real name)."""
    if not name:
        return True
    stripped = name.replace(":", "").replace("-", "").replace(" ", "").lower()
    mac_hex = mac.replace(":", "").lower()
    return stripped == mac_hex


async def scan(timeout_sec: int = SCAN_TIMEOUT) -> list[dict[str, Any]]:
    """Run a discovery scan and return non-paired devices with resolved names."""
    # bluetoothctl --timeout N scan on will run scan for N seconds then exit
    try:
        await _run("--timeout", str(timeout_sec), "scan", "on", timeout=timeout_sec + 5)
    except asyncio.TimeoutError:
        pass
    # After scan, list all known devices and filter out paired
    all_pairs = await _devices_raw(None)
    paired_set = {m for m, _ in await _devices_raw("Paired")}
    candidates = [(m, n) for m, n in all_pairs if m not in paired_set]
    if not candidates:
        return []

    # Parallel `info <mac>` so names from advertisement records populate —
    # plain `devices` output often holds just the MAC for devices that haven't
    # broadcast their name yet.
    infos = await asyncio.gather(
        *(_info(m) for m, _ in candidates), return_exceptions=True
    )
    out: list[dict[str, Any]] = []
    for (mac, devices_name), info in zip(candidates, infos):
        name = devices_name
        icon = None
        if isinstance(info, dict):
            # Prefer a human alias, then the advertised name, then the
            # `devices`-output fallback.
            for candidate in (info.get("alias"), info.get("name"), devices_name):
                if candidate and not _looks_like_mac_placeholder(candidate, mac):
                    name = candidate
                    break
            icon = info.get("icon")
        if _looks_like_mac_placeholder(name, mac):
            # Devices advertising with a randomised MAC and no name — hide
            # until they reveal themselves. iPhone does the same.
            continue
        out.append({
            "mac": mac,
            "name": name,
            "paired": False,
            "connected": False,
            "icon": icon,
        })
    # Stable sort: named devices first, then by name
    out.sort(key=lambda d: (d["name"] or "").lower())
    return out


# ================================================================== #
#  Connect / disconnect / forget / rename                              #
# ================================================================== #

async def connect(mac: str) -> bool:
    if not _is_mac(mac):
        return False
    rc, out, _ = await _run("connect", mac, timeout=20.0)
    return rc == 0 and "successful" in out.lower()


async def disconnect(mac: str) -> bool:
    if not _is_mac(mac):
        return False
    rc, out, _ = await _run("disconnect", mac, timeout=15.0)
    return rc == 0 and "successful" in out.lower()


async def unpair(mac: str) -> bool:
    if not _is_mac(mac):
        return False
    rc, out, _ = await _run("remove", mac)
    return rc == 0 and ("removed" in out.lower() or "Device has been removed" in out)


async def rename(mac: str, alias: str) -> bool:
    """Set the persistent alias on a paired device."""
    if not _is_mac(mac):
        return False
    alias = (alias or "").strip()
    if not alias:
        return False
    # bluetoothctl command: `set-alias <name>` on the device's menu.
    # Simpler one-shot form:  `bluetoothctl -- device <mac> alias <name>` is not
    # universally supported; the reliable way is a short scripted session.
    proc = await asyncio.create_subprocess_exec(
        "bluetoothctl",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    script = (
        f"menu device\n"
        f"alias {mac} {alias}\n"
        f"back\n"
        f"quit\n"
    )
    # Older bluez versions don't have `menu device alias`; fall back to
    # `devices` menu via DBus set-property if we detect failure.
    try:
        out, _err = await asyncio.wait_for(
            proc.communicate(script.encode()), timeout=DEFAULT_TIMEOUT
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return False
    text = out.decode(errors="replace").lower()
    if "invalid" in text or "error" in text:
        # Fallback: busctl property set on org.bluez.Device1.Alias
        return await _rename_via_dbus(mac, alias)
    return True


async def _rename_via_dbus(mac: str, alias: str) -> bool:
    """Set org.bluez.Device1 Alias property via busctl."""
    # Path format:  /org/bluez/hci0/dev_XX_XX_XX_XX_XX_XX
    # We auto-detect adapter by parsing `bluetoothctl list`.
    try:
        _, out, _ = await _run("list")
    except asyncio.TimeoutError:
        return False
    hci = "hci0"
    for line in out.splitlines():
        if line.strip().startswith("Controller "):
            # Controller <mac> <name> [default]
            # We don't know hci index from bluetoothctl list — assume hci0
            break
    dev_path = f"/org/bluez/{hci}/dev_" + mac.replace(":", "_").upper()
    proc = await asyncio.create_subprocess_exec(
        "busctl", "--system", "set-property",
        "org.bluez", dev_path, "org.bluez.Device1", "Alias", "s", alias,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        await asyncio.wait_for(proc.wait(), timeout=5.0)
    except asyncio.TimeoutError:
        proc.kill()
        return False
    return proc.returncode == 0


# ================================================================== #
#  Pair — interactive stdin session                                    #
# ================================================================== #

@dataclass
class PairSession:
    """Long-lived bluetoothctl session for pairing a single MAC.

    Events surfaced on `self.events` (asyncio.Queue[dict]):
      * {"type": "started"}
      * {"type": "pin_required", "prompt": "Enter PIN code:"}
      * {"type": "confirm_code", "code": "123456"}
      * {"type": "success"}
      * {"type": "failed", "reason": "AuthenticationFailed"}
    """

    mac: str
    events: asyncio.Queue = field(default_factory=asyncio.Queue)
    _proc: asyncio.subprocess.Process | None = None
    _reader: asyncio.Task | None = None
    _timeout: float = 90.0
    _done: bool = False

    async def start(self) -> None:
        if not _is_mac(self.mac):
            await self.events.put({"type": "failed", "reason": "invalid_mac"})
            self._done = True
            return
        self._proc = await asyncio.create_subprocess_exec(
            "bluetoothctl",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        await self._write("agent KeyboardDisplay\n")
        await self._write("default-agent\n")
        await self._write(f"pair {self.mac}\n")
        await self.events.put({"type": "started"})
        self._reader = asyncio.create_task(self._read_loop())

    async def _write(self, text: str) -> None:
        if self._proc and self._proc.stdin:
            self._proc.stdin.write(text.encode())
            await self._proc.stdin.drain()

    async def _read_loop(self) -> None:
        assert self._proc and self._proc.stdout
        try:
            end_deadline = asyncio.get_event_loop().time() + self._timeout
            while not self._done:
                remaining = end_deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    await self._fail("timeout")
                    return
                try:
                    raw = await asyncio.wait_for(
                        self._proc.stdout.readline(), timeout=remaining
                    )
                except asyncio.TimeoutError:
                    await self._fail("timeout")
                    return
                if not raw:
                    await self._fail("eof")
                    return
                line = raw.decode(errors="replace").strip()
                await self._handle_line(line)
        except Exception as e:
            logger.exception("pair read loop crashed: %s", e)
            await self._fail("read_error")

    async def _handle_line(self, line: str) -> None:
        low = line.lower()
        # Success markers
        if "pairing successful" in low:
            await self._write(f"trust {self.mac}\n")
            await self._write(f"connect {self.mac}\n")
            # Wait for Connected: yes or Failed to connect
            return
        if "connection successful" in low or (
            "connected: yes" in low and self.mac.lower() in low
        ):
            await self.events.put({"type": "success"})
            await self._write("quit\n")
            self._done = True
            return
        # Auth / agent prompts
        if "confirm passkey" in low:
            # Format: [agent] Confirm passkey 123456 (yes/no):
            m = re.search(r"(\d{4,6})", line)
            if m:
                await self.events.put({"type": "confirm_code", "code": m.group(1)})
            return
        if "request passkey" in low or "enter pin" in low or "enter passkey" in low:
            await self.events.put({"type": "pin_required", "prompt": line})
            return
        if "authorize service" in low:
            # Just-works: auto-accept
            await self._write("yes\n")
            return
        if "authorize" in low and "(yes/no)" in low:
            await self._write("yes\n")
            return
        # Failure markers
        for marker in (
            "failed to pair",
            "failed to connect",
            "authenticationfailed",
            "authentication failed",
            "connection refused",
            "no such device",
            "already exists",
        ):
            if marker in low:
                reason = marker.replace(" ", "_")
                await self._fail(reason)
                return

    async def respond_pin(self, pin: str) -> None:
        pin = (pin or "").strip()
        if not pin:
            return
        await self._write(f"{pin}\n")

    async def respond_confirm(self, accept: bool) -> None:
        await self._write("yes\n" if accept else "no\n")
        if not accept:
            await self._fail("user_cancelled")

    async def cancel(self) -> None:
        await self._fail("cancelled")

    async def _fail(self, reason: str) -> None:
        if self._done:
            return
        self._done = True
        await self.events.put({"type": "failed", "reason": reason})
        await self._cleanup()

    async def _cleanup(self) -> None:
        if self._proc and self._proc.returncode is None:
            try:
                await self._write("quit\n")
            except Exception:
                pass
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                self._proc.kill()
                await self._proc.wait()


_sessions: dict[str, PairSession] = {}
_sessions_lock = asyncio.Lock()


async def start_pair_session(mac: str) -> PairSession:
    async with _sessions_lock:
        old = _sessions.get(mac)
        if old and not old._done:
            await old.cancel()
        session = PairSession(mac=mac)
        _sessions[mac] = session
    await session.start()
    return session


def get_pair_session(mac: str) -> PairSession | None:
    return _sessions.get(mac)


async def clear_pair_session(mac: str) -> None:
    async with _sessions_lock:
        s = _sessions.pop(mac, None)
    if s and not s._done:
        await s.cancel()
