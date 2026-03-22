"""
system_modules/presence_detection/presence.py — PresenceDetector business logic

Detection methods (in priority order):
  1. ICMP ping   — check if device IP responds (requires icmplib)
  2. TCP port ping — fallback when ICMP not available
  3. MAC in ARP  — check ARP table for MAC (+ resolve IP from MAC)
  4. Bluetooth BLE — check if BT device is discoverable (requires bleak)

Network discovery:
  - ARP sweep (passive /proc/net/arp + active arping)
  - Reverse DNS / NetBIOS for hostname detection
  - OUI lookup for manufacturer identification

QR Invite flow:
  - Admin creates invite (name → token → QR code)
  - Person scans QR → phone opens join page → device auto-captured
  - IP/MAC/User-Agent captured on device registration

Persistence:  SQLite (users + invites + history survive restart)

Events published:
  presence.home   — user arrived (was away)
  presence.away   — user left (was home)
  presence.scan   — periodic scan result (all users)
"""
from __future__ import annotations

import asyncio
import io
import json
import ipaddress
import logging
import secrets
import socket
import sqlite3
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any

logger = logging.getLogger(__name__)

# Optional: pywebpush
try:
    from pywebpush import webpush, WebPushException
    from py_vapid import Vapid
    WEBPUSH_AVAILABLE = True
except ImportError:
    webpush = None  # type: ignore[assignment]
    WebPushException = Exception  # type: ignore[misc,assignment]
    Vapid = None  # type: ignore[assignment,misc]
    WEBPUSH_AVAILABLE = False
    logger.info("pywebpush not available — push notifications disabled")

# Optional: icmplib
try:
    from icmplib import async_ping as icmplib_async_ping
    ICMPLIB_AVAILABLE = True
except ImportError:
    icmplib_async_ping = None  # type: ignore[assignment]
    ICMPLIB_AVAILABLE = False
    logger.info("icmplib not available — using TCP fallback for ARP ping")

# Optional: bleak (Bluetooth)
try:
    import bleak  # noqa: F401
    BLEAK_AVAILABLE = True
except ImportError:
    BLEAK_AVAILABLE = False
    logger.info("bleak not available — Bluetooth detection disabled")


# ── Low-level ping helpers ────────────────────────────────────────────────────

async def _icmp_ping(address: str, timeout: float = 2.0) -> bool:
    """ICMP ping via icmplib if available."""
    if not ICMPLIB_AVAILABLE or icmplib_async_ping is None:
        return False
    try:
        result = await icmplib_async_ping(address, count=1, timeout=timeout)
        return result.is_alive
    except Exception:
        return False


async def _tcp_ping(address: str, timeout: float = 2.0) -> bool:
    """TCP connect ping — try common ports. Returns True if any port responds."""
    ports = [80, 443, 22, 8080, 7]
    for port in ports:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(address, port),
                timeout=timeout,
            )
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            return True
        except (ConnectionRefusedError, asyncio.TimeoutError, OSError):
            continue
    return False


async def ping_ip(address: str, timeout: float = 2.0) -> bool:
    """Ping IP address — tries ICMP first, then TCP."""
    if ICMPLIB_AVAILABLE:
        result = await _icmp_ping(address, timeout)
        if result:
            return True
    # TCP fallback
    return await _tcp_ping(address, timeout)


def _read_arp_table() -> dict[str, str]:
    """Read /proc/net/arp and return {ip: mac} dict."""
    arp: dict[str, str] = {}
    try:
        with open("/proc/net/arp") as f:
            next(f)  # skip header
            for line in f:
                parts = line.split()
                if len(parts) >= 4:
                    ip, mac = parts[0], parts[3]
                    if mac not in ("00:00:00:00:00:00", ""):
                        arp[ip] = mac.lower()
    except Exception as exc:
        logger.debug(f"Could not read ARP table: {exc}")
    return arp


def mac_in_arp_table(mac: str) -> tuple[bool, str | None]:
    """Check if a MAC address appears in ARP table. Returns (found, ip_address)."""
    target = mac.lower().replace("-", ":").strip()
    arp = _read_arp_table()
    for ip, m in arp.items():
        if m == target:
            return True, ip
    return False, None


def _resolve_hostname(ip: str) -> str:
    """Resolve IP → hostname via reverse DNS. Returns '' if not found."""
    try:
        name, _, _ = socket.gethostbyaddr(ip)
        # Strip common suffixes
        for suffix in (".local", ".lan", ".home", ".localdomain"):
            if name.endswith(suffix):
                name = name[: -len(suffix)]
        return name
    except (socket.herror, socket.gaierror, OSError):
        return ""


async def _resolve_hostname_async(ip: str) -> str:
    """Non-blocking hostname resolution."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _resolve_hostname, ip)


# ── Network Discovery ────────────────────────────────────────────────────────


@dataclass
class NetworkDevice:
    """A device discovered on the local network."""
    ip: str
    mac: str
    hostname: str = ""
    manufacturer: str = ""


async def discover_network(do_active_sweep: bool = False) -> list[NetworkDevice]:
    """Scan the local network and return list of discovered devices with names.

    Uses:
      1. /proc/net/arp (passive — always)
      2. arping sweep (active — optional, sends ARP requests)
      3. Reverse DNS for hostnames (gethostbyaddr)
      4. OUI lookup for manufacturer names
    """
    arp = _read_arp_table()

    # Optional active sweep: ping broadcast to refresh ARP cache
    if do_active_sweep:
        try:
            await _refresh_arp_cache()
            # Re-read after refresh
            arp = _read_arp_table()
        except Exception as exc:
            logger.debug("ARP refresh failed: %s", exc)

    # OUI lookup (try network_scanner, fallback gracefully)
    oui_lookup = None
    try:
        from system_modules.network_scanner.oui_lookup import lookup as _oui
        oui_lookup = _oui
    except ImportError:
        pass

    devices: list[NetworkDevice] = []
    # Resolve hostnames in parallel
    hostname_tasks = {ip: _resolve_hostname_async(ip) for ip in arp}
    hostnames = {}
    if hostname_tasks:
        results = await asyncio.gather(*hostname_tasks.values(), return_exceptions=True)
        for ip, result in zip(hostname_tasks.keys(), results):
            hostnames[ip] = result if isinstance(result, str) else ""

    for ip, mac in arp.items():
        hostname = hostnames.get(ip, "")
        manufacturer = oui_lookup(mac) if oui_lookup else ""
        if manufacturer == "Unknown":
            manufacturer = ""
        devices.append(NetworkDevice(ip=ip, mac=mac, hostname=hostname, manufacturer=manufacturer))

    # Sort: devices with hostnames first, then by IP
    devices.sort(key=lambda d: (0 if d.hostname else 1, d.ip))
    return devices


async def _refresh_arp_cache() -> None:
    """Send broadcast pings to refresh ARP cache (requires network access)."""
    # Quick ping sweep to populate ARP cache
    try:
        # First, get our subnet
        proc = await asyncio.create_subprocess_exec(
            "ip", "-4", "route", "show", "default",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await proc.communicate()
        # Find subnet of default interface
        proc2 = await asyncio.create_subprocess_exec(
            "ip", "-4", "-o", "addr", "show",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        stdout2, _ = await proc2.communicate()
        for line in stdout2.decode().splitlines():
            parts = line.split()
            for i, p in enumerate(parts):
                if "/" in p and p.count(".") == 3:
                    try:
                        net = ipaddress.ip_network(p, strict=False)
                        if net.num_addresses <= 256:
                            # Ping broadcast
                            broadcast = str(net.broadcast_address)
                            ping_proc = await asyncio.create_subprocess_exec(
                                "ping", "-c", "1", "-W", "1", "-b", broadcast,
                                stdout=asyncio.subprocess.DEVNULL,
                                stderr=asyncio.subprocess.DEVNULL,
                            )
                            await asyncio.wait_for(ping_proc.wait(), timeout=3)
                            await asyncio.sleep(1)  # Let ARP replies arrive
                            return
                    except (ValueError, asyncio.TimeoutError):
                        continue
    except Exception as exc:
        logger.debug("ARP cache refresh failed: %s", exc)


# ── PresenceDetector ─────────────────────────────────────────────────────────

class PresenceDetector:
    """Tracks user presence based on device IP/MAC/BT availability.

    Users are persisted in SQLite — survive restarts.
    """

    def __init__(
        self,
        publish_event_cb: Callable,
        scan_interval_sec: int = 60,
        away_threshold_sec: int = 180,
        db_path: str = ":memory:",
    ) -> None:
        self._publish_event = publish_event_cb
        self._scan_interval = scan_interval_sec
        self._away_threshold = away_threshold_sec
        self._users: dict[str, dict] = {}
        self._task: asyncio.Task | None = None
        self._db_path = db_path
        self._db: sqlite3.Connection | None = None
        self._init_db()
        self._load_users_from_db()

    # ── Database ───────────────────────────────────────────────────────────────

    def _init_db(self) -> None:
        self._db = sqlite3.connect(self._db_path, check_same_thread=False)
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS presence_users (
                user_id  TEXT PRIMARY KEY,
                name     TEXT NOT NULL,
                devices  TEXT NOT NULL DEFAULT '[]'
            )
        """)
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS presence_invites (
                token       TEXT PRIMARY KEY,
                name        TEXT NOT NULL,
                created_at  TEXT NOT NULL,
                expires_at  TEXT NOT NULL,
                status      TEXT NOT NULL DEFAULT 'pending',
                user_id     TEXT
            )
        """)
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS presence_history (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    TEXT NOT NULL,
                state      TEXT NOT NULL,
                timestamp  TEXT NOT NULL
            )
        """)
        self._db.execute("""
            CREATE INDEX IF NOT EXISTS idx_history_user
            ON presence_history (user_id, timestamp DESC)
        """)
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key    TEXT PRIMARY KEY,
                value  TEXT NOT NULL
            )
        """)
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS push_subscriptions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     TEXT NOT NULL,
                endpoint    TEXT NOT NULL UNIQUE,
                p256dh      TEXT NOT NULL,
                auth        TEXT NOT NULL,
                user_agent  TEXT,
                platform    TEXT,
                created_at  TEXT NOT NULL
            )
        """)
        self._db.commit()
        self._vapid_private: str | None = None
        self._vapid_public: str | None = None
        self._init_vapid()

    def _load_users_from_db(self) -> None:
        if self._db is None:
            return
        rows = self._db.execute("SELECT user_id, name, devices FROM presence_users").fetchall()
        for user_id, name, devices_json in rows:
            devices = json.loads(devices_json)
            self._users[user_id] = {
                "user_id": user_id,
                "name": name,
                "devices": devices,
                "state": "unknown",
                "last_seen": None,
                "confidence": 0.0,
            }
        if rows:
            logger.info("Loaded %d presence users from DB", len(rows))

    def _save_user_to_db(self, user_id: str) -> None:
        if self._db is None:
            return
        user = self._users.get(user_id)
        if not user:
            return
        self._db.execute(
            "INSERT OR REPLACE INTO presence_users (user_id, name, devices) VALUES (?, ?, ?)",
            (user_id, user["name"], json.dumps(user["devices"])),
        )
        self._db.commit()

    def _delete_user_from_db(self, user_id: str) -> None:
        if self._db is None:
            return
        self._db.execute("DELETE FROM presence_users WHERE user_id=?", (user_id,))
        self._db.execute("DELETE FROM push_subscriptions WHERE user_id=?", (user_id,))
        self._db.commit()

    # ── Settings (key/value) ───────────────────────────────────────────────────

    def _get_setting(self, key: str) -> str | None:
        if self._db is None:
            return None
        row = self._db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def _set_setting(self, key: str, value: str) -> None:
        if self._db is None:
            return
        self._db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, value))
        self._db.commit()

    # ── VAPID keys ─────────────────────────────────────────────────────────────

    def _init_vapid(self) -> None:
        if not WEBPUSH_AVAILABLE or Vapid is None:
            logger.info("VAPID init skipped — pywebpush not installed")
            return
        private = self._get_setting("vapid_private_key")
        if not private:
            import base64
            from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
            v = Vapid()
            v.generate_keys()
            raw_priv = v.private_pem()
            pub_bytes = v.public_key.public_bytes(
                encoding=Encoding.X962,
                format=PublicFormat.UncompressedPoint,
            )
            raw_pub = base64.urlsafe_b64encode(pub_bytes).decode().rstrip("=")
            if isinstance(raw_priv, bytes):
                raw_priv = raw_priv.decode()
            self._set_setting("vapid_private_key", raw_priv)
            self._set_setting("vapid_public_key", raw_pub)
            logger.info("Generated new VAPID key pair")
        self._vapid_private = self._get_setting("vapid_private_key")
        self._vapid_public = self._get_setting("vapid_public_key")

    def get_vapid_public_key(self) -> str | None:
        return self._vapid_public

    # ── Push subscriptions ─────────────────────────────────────────────────────

    def save_push_subscription(
        self,
        user_id: str,
        endpoint: str,
        p256dh: str,
        auth: str,
        user_agent: str = "",
    ) -> None:
        if self._db is None:
            return
        platform = _detect_platform(user_agent)
        now = datetime.now(tz=timezone.utc).isoformat()
        # Upsert: if endpoint exists update user_id
        self._db.execute(
            """INSERT INTO push_subscriptions (user_id, endpoint, p256dh, auth, user_agent, platform, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(endpoint) DO UPDATE SET user_id=excluded.user_id, p256dh=excluded.p256dh, auth=excluded.auth, user_agent=excluded.user_agent, platform=excluded.platform""",
            (user_id, endpoint, p256dh, auth, user_agent, platform, now),
        )
        self._db.commit()
        logger.info("Saved push subscription for user %s (%s)", user_id, platform)

    def get_push_subscriptions_for_user(self, user_id: str) -> list[dict]:
        if self._db is None:
            return []
        rows = self._db.execute(
            "SELECT endpoint, p256dh, auth FROM push_subscriptions WHERE user_id=?",
            (user_id,),
        ).fetchall()
        return [{"endpoint": r[0], "p256dh": r[1], "auth": r[2]} for r in rows]

    def get_all_push_subscriptions(self) -> list[dict]:
        if self._db is None:
            return []
        rows = self._db.execute(
            "SELECT user_id, endpoint, p256dh, auth, platform, created_at FROM push_subscriptions ORDER BY created_at DESC"
        ).fetchall()
        return [
            {"user_id": r[0], "endpoint": r[1], "p256dh": r[2], "auth": r[3], "platform": r[4], "created_at": r[5]}
            for r in rows
        ]

    def delete_push_subscription(self, endpoint: str) -> None:
        if self._db is None:
            return
        self._db.execute("DELETE FROM push_subscriptions WHERE endpoint=?", (endpoint,))
        self._db.commit()

    async def send_push_to_user(self, user_id: str, title: str, body: str, data: dict | None = None) -> dict:
        if not WEBPUSH_AVAILABLE or not self._vapid_private:
            return {"sent": 0, "failed": 0, "error": "webpush not available"}
        subs = self.get_push_subscriptions_for_user(user_id)
        sent, failed = 0, 0
        loop = asyncio.get_event_loop()
        for sub in subs:
            try:
                await loop.run_in_executor(None, self._send_single_push, sub, title, body, data)
                sent += 1
            except Exception as exc:
                if hasattr(exc, "response") and getattr(exc.response, "status_code", 0) == 410:
                    self.delete_push_subscription(sub["endpoint"])
                    logger.info("Removed expired push subscription: %s", sub["endpoint"][:50])
                else:
                    logger.error("Push failed for %s: %s", user_id, exc)
                failed += 1
        return {"sent": sent, "failed": failed}

    async def send_push_to_all(self, title: str, body: str, data: dict | None = None) -> dict:
        if self._db is None:
            return {"sent": 0, "failed": 0}
        rows = self._db.execute("SELECT DISTINCT user_id FROM push_subscriptions").fetchall()
        total_sent, total_failed = 0, 0
        for (uid,) in rows:
            result = await self.send_push_to_user(uid, title, body, data)
            total_sent += result["sent"]
            total_failed += result["failed"]
        return {"sent": total_sent, "failed": total_failed}

    def _send_single_push(self, sub: dict, title: str, body: str, data: dict | None) -> None:
        """Synchronous push send — called inside run_in_executor."""
        webpush(
            subscription_info={
                "endpoint": sub["endpoint"],
                "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]},
            },
            data=json.dumps({"title": title, "body": body, "data": data or {}}),
            vapid_private_key=self._vapid_private,
            vapid_claims={"sub": "mailto:selena@local.home"},
            timeout=10,
        )

    # ── History ────────────────────────────────────────────────────────────────

    def _log_history(self, user_id: str, state: str) -> None:
        if self._db is None:
            return
        now = datetime.now(tz=timezone.utc).isoformat()
        self._db.execute(
            "INSERT INTO presence_history (user_id, state, timestamp) VALUES (?, ?, ?)",
            (user_id, state, now),
        )
        self._db.commit()
        # Prune old entries (keep last 500 per user)
        self._db.execute("""
            DELETE FROM presence_history WHERE id NOT IN (
                SELECT id FROM presence_history WHERE user_id=?
                ORDER BY timestamp DESC LIMIT 500
            ) AND user_id=?
        """, (user_id, user_id))
        self._db.commit()

    def get_user_history(self, user_id: str, limit: int = 50) -> list[dict]:
        if self._db is None:
            return []
        rows = self._db.execute(
            "SELECT state, timestamp FROM presence_history WHERE user_id=? ORDER BY timestamp DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        return [{"state": r[0], "timestamp": r[1]} for r in rows]

    # ── Invite system ─────────────────────────────────────────────────────────

    def create_invite(self, name: str, expires_minutes: int = 15) -> dict:
        """Create an invite token. Returns {token, name, expires_at, status}."""
        token = secrets.token_urlsafe(24)
        now = datetime.now(tz=timezone.utc)
        expires_at = now + timedelta(minutes=expires_minutes)
        if self._db:
            self._db.execute(
                "INSERT INTO presence_invites (token, name, created_at, expires_at, status) VALUES (?, ?, ?, ?, ?)",
                (token, name, now.isoformat(), expires_at.isoformat(), "pending"),
            )
            self._db.commit()
        return {
            "token": token,
            "name": name,
            "created_at": now.isoformat(),
            "expires_at": expires_at.isoformat(),
            "status": "pending",
        }

    def get_invite(self, token: str) -> dict | None:
        if self._db is None:
            return None
        row = self._db.execute(
            "SELECT token, name, created_at, expires_at, status, user_id FROM presence_invites WHERE token=?",
            (token,),
        ).fetchone()
        if not row:
            return None
        invite = {
            "token": row[0], "name": row[1], "created_at": row[2],
            "expires_at": row[3], "status": row[4], "user_id": row[5],
        }
        # Check expiry
        if invite["status"] == "pending":
            expires = datetime.fromisoformat(invite["expires_at"])
            if datetime.now(tz=timezone.utc) > expires:
                invite["status"] = "expired"
                self._db.execute(
                    "UPDATE presence_invites SET status='expired' WHERE token=?", (token,),
                )
                self._db.commit()
        return invite

    def complete_invite(self, token: str, ip: str, mac: str, user_agent: str) -> dict | None:
        """Complete an invite — register the device and create the user."""
        invite = self.get_invite(token)
        if not invite or invite["status"] != "pending":
            return None

        # Determine device name from User-Agent
        device_name = _parse_device_name(user_agent)
        name = invite["name"]
        user_id = name.lower().replace(" ", "-")
        user_id = "".join(c for c in user_id if c.isalnum() or c == "-") or f"user-{token[:8]}"

        # Avoid duplicate user_id
        if user_id in self._users:
            user_id = f"{user_id}-{token[:6]}"

        devices: list[dict] = []
        if mac and mac != "00:00:00:00:00:00":
            devices.append({"type": "mac", "address": mac})
        if ip:
            devices.append({"type": "ip", "address": ip})

        user = self.add_user({
            "user_id": user_id,
            "name": name,
            "devices": devices,
        })

        # Mark invite completed
        if self._db:
            self._db.execute(
                "UPDATE presence_invites SET status='completed', user_id=? WHERE token=?",
                (user_id, token),
            )
            self._db.commit()

        return {
            "user_id": user_id,
            "name": name,
            "device_name": device_name,
            "ip": ip,
            "mac": mac,
            "devices": devices,
        }

    def list_invites(self, include_expired: bool = False) -> list[dict]:
        if self._db is None:
            return []
        if include_expired:
            rows = self._db.execute(
                "SELECT token, name, created_at, expires_at, status, user_id FROM presence_invites ORDER BY created_at DESC LIMIT 50"
            ).fetchall()
        else:
            rows = self._db.execute(
                "SELECT token, name, created_at, expires_at, status, user_id FROM presence_invites WHERE status='pending' ORDER BY created_at DESC"
            ).fetchall()
        result = []
        for r in rows:
            result.append({
                "token": r[0], "name": r[1], "created_at": r[2],
                "expires_at": r[3], "status": r[4], "user_id": r[5],
            })
        return result

    def generate_qr_svg(self, data: str) -> str:
        """Generate a QR code as SVG string."""
        try:
            import qrcode
            import qrcode.image.svg
            qr = qrcode.QRCode(version=1, box_size=10, border=2)
            qr.add_data(data)
            qr.make(fit=True)
            factory = qrcode.image.svg.SvgPathImage
            img = qr.make_image(image_factory=factory)
            buf = io.BytesIO()
            img.save(buf)
            return buf.getvalue().decode("utf-8")
        except ImportError:
            logger.warning("qrcode library not available — QR generation disabled")
            return ""

    async def start(self) -> None:
        self._task = asyncio.create_task(self._scan_loop(), name="presence_scan")
        logger.info("PresenceDetector started (%d users loaded)", len(self._users))

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._db:
            self._db.close()
            self._db = None

    # ── User management ────────────────────────────────────────────────────────

    def add_user(self, definition: dict) -> dict:
        user_id = definition["user_id"]
        if user_id not in self._users:
            self._users[user_id] = {
                **definition,
                "state": "unknown",
                "last_seen": None,
                "confidence": 0.0,
            }
        else:
            # Update devices list
            self._users[user_id].update({
                k: v for k, v in definition.items()
                if k in ("name", "devices")
            })
        self._save_user_to_db(user_id)
        return self._users[user_id]

    def remove_user(self, user_id: str) -> bool:
        if user_id in self._users:
            del self._users[user_id]
            self._delete_user_from_db(user_id)
            return True
        return False

    def get_user(self, user_id: str) -> dict | None:
        return self._users.get(user_id)

    def list_users(self) -> list[dict]:
        return list(self._users.values())

    # ── Scan loop ──────────────────────────────────────────────────────────────

    async def _scan_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self._scan_interval)
                await self._scan_all()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"Presence scan error: {exc}")

    async def _scan_all(self) -> None:
        results = []
        for user_id, user in list(self._users.items()):
            detected = await self._detect_user(user)
            prev_state = user["state"]
            now = datetime.now(tz=timezone.utc).isoformat()

            if detected:
                user["last_seen"] = now
                user["confidence"] = 1.0
                if prev_state != "home":
                    user["state"] = "home"
                    self._log_history(user_id, "home")
                    await self._publish_event("presence.home", {
                        "user_id": user_id,
                        "name": user.get("name", ""),
                        "timestamp": now,
                    })
            else:
                # Only switch to away after threshold
                if user["last_seen"] is not None:
                    last_seen_dt = datetime.fromisoformat(user["last_seen"])
                    now_dt = datetime.now(tz=timezone.utc)
                    elapsed = (now_dt - last_seen_dt).total_seconds()
                    if elapsed >= self._away_threshold and prev_state == "home":
                        user["state"] = "away"
                        user["confidence"] = 0.0
                        self._log_history(user_id, "away")
                        await self._publish_event("presence.away", {
                            "user_id": user_id,
                            "name": user.get("name", ""),
                            "timestamp": now,
                        })
                elif prev_state == "unknown":
                    user["state"] = "away"

            results.append({"user_id": user_id, "state": user["state"]})

        await self._publish_event("presence.scan", {
            "users": results,
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        })

    async def _detect_user(self, user: dict) -> bool:
        """Return True if any device for the user is reachable."""
        devices = user.get("devices", [])
        checks = [self._check_device(d) for d in devices]
        if not checks:
            return False
        results = await asyncio.gather(*checks, return_exceptions=True)
        return any(r is True for r in results)

    async def _check_device(self, device: dict) -> bool:
        dtype = device.get("type", "")
        address = device.get("address", "")
        if not address:
            return False

        if dtype == "ip":
            return await ping_ip(address)
        elif dtype == "mac":
            # Check ARP table for MAC → also get IP if found
            found, resolved_ip = mac_in_arp_table(address)
            if found:
                return True
            # MAC not in ARP cache — try pinging known IP to refresh it
            if resolved_ip:
                if await ping_ip(resolved_ip):
                    return True
            return False
        elif dtype == "bluetooth":
            return await self._check_bluetooth(address)
        return False

    async def _check_bluetooth(self, bt_address: str) -> bool:
        """Check if a Bluetooth device is discoverable (requires bleak)."""
        if not BLEAK_AVAILABLE:
            return False
        try:
            import bleak
            devices = await bleak.BleakScanner.discover(timeout=3.0)
            return any(d.address.lower() == bt_address.lower() for d in devices)
        except Exception as exc:
            logger.debug(f"BT scan failed: {exc}")
            return False

    # ── Manual trigger ─────────────────────────────────────────────────────────

    async def trigger_scan_now(self) -> list[dict]:
        await self._scan_all()
        return self.list_users()

    # ── Network discovery ─────────────────────────────────────────────────────

    async def discover_network_devices(self, active: bool = True) -> list[dict[str, str]]:
        """Discover devices on local network. Returns list of {ip, mac, hostname, manufacturer}."""
        devices = await discover_network(do_active_sweep=active)
        return [
            {
                "ip": d.ip,
                "mac": d.mac,
                "hostname": d.hostname,
                "manufacturer": d.manufacturer,
            }
            for d in devices
        ]

    # ── Status ─────────────────────────────────────────────────────────────────

    def get_status(self) -> dict:
        home_count = sum(1 for u in self._users.values() if u["state"] == "home")
        return {
            "users_total": len(self._users),
            "users_home": home_count,
            "users_away": sum(1 for u in self._users.values() if u["state"] == "away"),
            "scan_interval_sec": self._scan_interval,
            "away_threshold_sec": self._away_threshold,
            "icmplib_available": ICMPLIB_AVAILABLE,
            "bluetooth_available": BLEAK_AVAILABLE,
        }


def _parse_device_name(user_agent: str) -> str:
    """Extract a readable device name from User-Agent string."""
    ua = user_agent.lower()
    if "iphone" in ua:
        return "iPhone"
    if "ipad" in ua:
        return "iPad"
    if "macintosh" in ua or "mac os" in ua:
        return "Mac"
    if "android" in ua:
        # Try to extract model: "... Build/MODEL ..." or "... ; MODEL Build/..."
        import re
        m = re.search(r";\s*([^;)]+?)\s*build/", ua)
        if m:
            return m.group(1).strip().title()
        return "Android"
    if "windows" in ua:
        return "Windows PC"
    if "linux" in ua:
        return "Linux"
    return "Unknown device"


def _detect_platform(user_agent: str) -> str:
    """Detect platform from User-Agent for push subscription tagging."""
    ua = user_agent.lower()
    if "iphone" in ua or "ipad" in ua or "ipod" in ua:
        return "ios"
    if "android" in ua:
        return "android"
    return "desktop"
