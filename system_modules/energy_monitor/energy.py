"""
system_modules/energy_monitor/energy.py — EnergyMonitor business logic

Tracks per-device watt readings in an in-memory time-series store (SQLite-backed).
Provides:
  - record_reading(device_id, watts)  — store a power sample
  - get_current_power()               — dict of device_id → latest watts
  - get_total_power()                 — sum of all current readings (W)
  - get_daily_kwh(device_id)          — today's kWh for a device
  - get_total_today_kwh()             — sum of all devices today
  - anomaly check: reading > 2× rolling average fires energy.anomaly event

Data sources:
  - device_registry: subscribe to device.state_changed, extract watts from state key
  - mqtt_topic: listen for MQTT events routed via protocol_bridge
  - manual: user sends readings via POST /energy/reading

Events published:
  energy.anomaly        — spike detected (reading > 2× rolling avg)
  energy.daily_report   — sent once per day with summary
  energy.reading        — (optional) every new reading if subscribe_to_readings=True
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime, timezone, date, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Rolling window for anomaly detection (number of samples)
ANOMALY_WINDOW = 20
# Anomaly multiplier
ANOMALY_MULTIPLIER = 2.0
# Minimum average (W) below which anomaly check is skipped (avoid false positives)
ANOMALY_MIN_AVG = 5.0


class EnergyMonitor:
    def __init__(
        self,
        publish_event_cb: Any,
        db_path: str = ":memory:",
        daily_report_hour: int = 0,   # UTC hour to send daily report
        anomaly_multiplier: float = ANOMALY_MULTIPLIER,
        anomaly_window: int = ANOMALY_WINDOW,
    ) -> None:
        self._publish = publish_event_cb
        self._db_path = db_path
        self._report_hour = daily_report_hour
        self._anomaly_mult = anomaly_multiplier
        self._anomaly_window = anomaly_window

        # In-memory state
        self._current: dict[str, float] = {}           # device_id → latest watts
        self._last_ts: dict[str, float] = {}           # device_id → monotonic ts of last reading
        self._history: dict[str, deque] = defaultdict(lambda: deque(maxlen=self._anomaly_window))
        self._last_report_date: date | None = None
        # A device is considered "active" if we received a reading from it
        # within this many seconds. Tuya plugs poll every ~30s; 5 minutes
        # gives plenty of slack for transient network hiccups.
        self._active_window_sec: float = 300.0

        self._task: asyncio.Task | None = None
        self._db: sqlite3.Connection | None = None
        self._init_db()

    # ── Database ──────────────────────────────────────────────────────────────

    def _init_db(self) -> None:
        self._db = sqlite3.connect(self._db_path, check_same_thread=False)
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS energy_readings (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id TEXT    NOT NULL,
                watts     REAL    NOT NULL,
                ts        TEXT    NOT NULL
            )
        """)
        self._db.execute("""
            CREATE INDEX IF NOT EXISTS idx_energy_device_ts
            ON energy_readings (device_id, ts)
        """)
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS energy_sources (
                id          TEXT PRIMARY KEY,
                name        TEXT NOT NULL,
                type        TEXT NOT NULL,
                config      TEXT NOT NULL DEFAULT '{}',
                enabled     INTEGER NOT NULL DEFAULT 1,
                last_reading_ts TEXT,
                created_at  TEXT NOT NULL
            )
        """)
        self._db.commit()

    def _store_reading(self, device_id: str, watts: float, ts: str) -> None:
        if self._db is None:
            return
        self._db.execute(
            "INSERT INTO energy_readings (device_id, watts, ts) VALUES (?, ?, ?)",
            (device_id, watts, ts),
        )
        self._db.commit()

    # ── Public API ────────────────────────────────────────────────────────────

    async def record_reading(self, device_id: str, watts: float) -> None:
        """Record a power sample for a device and check for anomalies."""
        if watts < 0:
            watts = 0.0
        ts = datetime.now(tz=timezone.utc).isoformat()
        self._store_reading(device_id, watts, ts)

        prev = self._current.get(device_id)
        self._current[device_id] = watts
        self._last_ts[device_id] = time.monotonic()
        self._history[device_id].append(watts)

        await self._check_anomaly(device_id, watts)

    async def _check_anomaly(self, device_id: str, watts: float) -> None:
        hist = list(self._history[device_id])
        if len(hist) < 2:
            return
        # Compare against history excluding the latest reading
        avg = sum(hist[:-1]) / len(hist[:-1])
        if avg < ANOMALY_MIN_AVG:
            return
        if watts > avg * self._anomaly_mult:
            logger.warning("Anomaly: %s %.1fW > %.1f × %.1fW avg", device_id, watts, self._anomaly_mult, avg)
            await self._publish("energy.anomaly", {
                "device_id": device_id,
                "watts": watts,
                "average_watts": round(avg, 2),
                "multiplier": self._anomaly_mult,
            })

    def get_current_power(self) -> dict[str, float]:
        return dict(self._current)

    def get_total_power(self) -> float:
        return sum(self._current.values())

    def get_daily_kwh(self, device_id: str) -> float:
        """Calculate kWh consumed today using trapezoidal integration."""
        if self._db is None:
            return 0.0
        today = date.today().isoformat()
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        rows = self._db.execute(
            "SELECT watts, ts FROM energy_readings WHERE device_id=? AND ts>=? AND ts<? ORDER BY ts",
            (device_id, today, tomorrow),
        ).fetchall()
        return self._integrate_kwh(rows)

    def get_total_today_kwh(self) -> float:
        if self._db is None:
            return 0.0
        today = date.today().isoformat()
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        rows = self._db.execute(
            "SELECT watts, ts FROM energy_readings WHERE ts>=? AND ts<? ORDER BY device_id, ts",
            (today, tomorrow),
        ).fetchall()
        return self._integrate_kwh(rows)

    def get_device_history(self, device_id: str, limit: int = 100) -> list[dict]:
        if self._db is None:
            return []
        rows = self._db.execute(
            "SELECT watts, ts FROM energy_readings WHERE device_id=? ORDER BY ts DESC LIMIT ?",
            (device_id, limit),
        ).fetchall()
        return [{"watts": w, "ts": t} for w, t in rows]

    def get_all_devices(self) -> list[str]:
        if self._db is None:
            return list(self._current.keys())
        rows = self._db.execute(
            "SELECT DISTINCT device_id FROM energy_readings ORDER BY device_id"
        ).fetchall()
        return [r[0] for r in rows]

    def get_active_devices(self) -> int:
        """Count devices that produced a reading within the active window."""
        now = time.monotonic()
        return sum(
            1 for ts in self._last_ts.values()
            if now - ts < self._active_window_sec
        )

    def get_status(self) -> dict[str, Any]:
        return {
            "devices": len(self._current),
            "active_devices": self.get_active_devices(),
            "total_power_w": round(self.get_total_power(), 2),
            "total_today_kwh": round(self.get_total_today_kwh(), 4),
            "last_report_date": self._last_report_date.isoformat() if self._last_report_date else None,
        }

    # ── Data Sources ─────────────────────────────────────────────────────────

    def get_sources(self) -> list[dict[str, Any]]:
        if self._db is None:
            return []
        rows = self._db.execute(
            "SELECT id, name, type, config, enabled, last_reading_ts, created_at "
            "FROM energy_sources ORDER BY created_at"
        ).fetchall()
        return [
            {
                "id": r[0], "name": r[1], "type": r[2],
                "config": json.loads(r[3]), "enabled": bool(r[4]),
                "last_reading_ts": r[5], "created_at": r[6],
            }
            for r in rows
        ]

    def add_source(self, name: str, source_type: str, config: dict[str, Any]) -> dict[str, Any]:
        if self._db is None:
            raise RuntimeError("Database not initialized")
        valid_types = ("device_registry", "mqtt_topic", "manual")
        if source_type not in valid_types:
            raise ValueError(f"Invalid source type: {source_type}. Must be one of: {valid_types}")
        source_id = str(uuid.uuid4())[:8]
        now = datetime.now(tz=timezone.utc).isoformat()
        self._db.execute(
            "INSERT INTO energy_sources (id, name, type, config, enabled, created_at) "
            "VALUES (?, ?, ?, ?, 1, ?)",
            (source_id, name, source_type, json.dumps(config), now),
        )
        self._db.commit()
        return {
            "id": source_id, "name": name, "type": source_type,
            "config": config, "enabled": True,
            "last_reading_ts": None, "created_at": now,
        }

    def delete_source(self, source_id: str) -> bool:
        if self._db is None:
            return False
        cur = self._db.execute("DELETE FROM energy_sources WHERE id=?", (source_id,))
        self._db.commit()
        return cur.rowcount > 0

    def toggle_source(self, source_id: str, enabled: bool) -> bool:
        if self._db is None:
            return False
        cur = self._db.execute(
            "UPDATE energy_sources SET enabled=? WHERE id=?",
            (1 if enabled else 0, source_id),
        )
        self._db.commit()
        return cur.rowcount > 0

    def _update_source_ts(self, source_id: str) -> None:
        if self._db is None:
            return
        now = datetime.now(tz=timezone.utc).isoformat()
        self._db.execute(
            "UPDATE energy_sources SET last_reading_ts=? WHERE id=?",
            (now, source_id),
        )
        self._db.commit()

    def get_source_device_ids(self) -> dict[str, str]:
        """Return mapping of device_id → source_id for device_registry sources."""
        result: dict[str, str] = {}
        for src in self.get_sources():
            if src["type"] == "device_registry" and src["enabled"]:
                dev_id = src["config"].get("device_id")
                if dev_id:
                    result[dev_id] = src["id"]
        return result

    def get_source_mqtt_topics(self) -> dict[str, dict[str, Any]]:
        """Return mapping of mqtt_topic → {source_id, state_key} for mqtt sources."""
        result: dict[str, dict[str, Any]] = {}
        for src in self.get_sources():
            if src["type"] == "mqtt_topic" and src["enabled"]:
                topic = src["config"].get("mqtt_topic")
                if topic:
                    result[topic] = {
                        "source_id": src["id"],
                        "state_key": src["config"].get("state_key", "power"),
                        "device_id": src["config"].get("device_id", topic),
                    }
        return result

    # ── Integration helper ────────────────────────────────────────────────────

    @staticmethod
    def _integrate_kwh(rows: list[tuple]) -> float:
        """Trapezoidal integration of watts over time → kWh."""
        if len(rows) < 2:
            return 0.0
        total_wh = 0.0
        for i in range(1, len(rows)):
            w1 = rows[i - 1][0]
            w2 = rows[i][0]
            try:
                t1 = datetime.fromisoformat(rows[i - 1][1])
                t2 = datetime.fromisoformat(rows[i][1])
            except ValueError:
                continue
            dt_h = (t2 - t1).total_seconds() / 3600.0
            if dt_h > 0:
                total_wh += (w1 + w2) / 2.0 * dt_h
        return total_wh / 1000.0  # Wh → kWh

    # ── Background loop ────────────────────────────────────────────────────────

    async def start(self) -> None:
        self._task = asyncio.get_event_loop().create_task(self._report_loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._db:
            self._db.close()
            self._db = None

    async def _report_loop(self) -> None:
        while True:
            now = datetime.now(tz=timezone.utc)
            today = now.date()
            if now.hour == self._report_hour and self._last_report_date != today:
                await self._send_daily_report()
                self._last_report_date = today
            await asyncio.sleep(60)  # check every minute

    async def _send_daily_report(self) -> None:
        devices = self.get_all_devices()
        report: dict[str, Any] = {
            "date": date.today().isoformat(),
            "total_kwh": round(self.get_total_today_kwh(), 4),
            "devices": {},
        }
        for dev in devices:
            report["devices"][dev] = {
                "kwh": round(self.get_daily_kwh(dev), 4),
                "current_w": self._current.get(dev, 0.0),
            }
        logger.info("Daily energy report: %.4f kWh total", report["total_kwh"])
        await self._publish("energy.daily_report", report)
