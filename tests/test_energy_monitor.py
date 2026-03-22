"""tests/test_energy_monitor.py — pytest tests for energy_monitor module"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch

import pytest


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_monitor(publish=None, anomaly_mult=2.0, anomaly_window=5):
    from system_modules.energy_monitor.energy import EnergyMonitor
    return EnergyMonitor(
        publish_event_cb=publish or AsyncMock(),
        db_path=":memory:",
        anomaly_multiplier=anomaly_mult,
        anomaly_window=anomaly_window,
    )


# ── Record readings ────────────────────────────────────────────────────────────

class TestRecordReadings:
    @pytest.mark.asyncio
    async def test_record_updates_current(self):
        m = make_monitor()
        await m.record_reading("device-1", 100.0)
        assert m.get_current_power()["device-1"] == 100.0

    @pytest.mark.asyncio
    async def test_record_multiple_devices(self):
        m = make_monitor()
        await m.record_reading("dev-a", 200.0)
        await m.record_reading("dev-b", 50.0)
        power = m.get_current_power()
        assert power["dev-a"] == 200.0
        assert power["dev-b"] == 50.0

    @pytest.mark.asyncio
    async def test_total_power(self):
        m = make_monitor()
        await m.record_reading("dev-a", 300.0)
        await m.record_reading("dev-b", 150.0)
        assert m.get_total_power() == pytest.approx(450.0)

    @pytest.mark.asyncio
    async def test_negative_watts_clamped_to_zero(self):
        m = make_monitor()
        await m.record_reading("dev-a", -50.0)
        assert m.get_current_power()["dev-a"] == 0.0

    @pytest.mark.asyncio
    async def test_zero_watts(self):
        m = make_monitor()
        await m.record_reading("dev-a", 0.0)
        assert m.get_current_power()["dev-a"] == 0.0


# ── Anomaly detection ─────────────────────────────────────────────────────────

class TestAnomalyDetection:
    @pytest.mark.asyncio
    async def test_anomaly_fires_event(self):
        publish = AsyncMock()
        m = make_monitor(publish=publish, anomaly_mult=2.0, anomaly_window=5)
        # Record stable baseline (5W each)
        for _ in range(4):
            await m.record_reading("fridge", 5.0)
        # Now spike to 50W (10× average → anomaly)
        await m.record_reading("fridge", 50.0)
        calls = [c[0][0] for c in publish.call_args_list]
        assert "energy.anomaly" in calls

    @pytest.mark.asyncio
    async def test_no_anomaly_below_multiplier(self):
        publish = AsyncMock()
        m = make_monitor(publish=publish, anomaly_mult=3.0)
        for _ in range(4):
            await m.record_reading("lamp", 100.0)
        await m.record_reading("lamp", 200.0)  # 2× — below 3× threshold
        calls = [c[0][0] for c in publish.call_args_list]
        assert "energy.anomaly" not in calls

    @pytest.mark.asyncio
    async def test_no_anomaly_with_single_reading(self):
        publish = AsyncMock()
        m = make_monitor(publish=publish)
        await m.record_reading("dev-1", 1000.0)
        calls = [c[0][0] for c in publish.call_args_list]
        assert "energy.anomaly" not in calls

    @pytest.mark.asyncio
    async def test_no_anomaly_below_min_avg(self):
        """When average is < 5W, anomaly check is skipped."""
        publish = AsyncMock()
        m = make_monitor(publish=publish, anomaly_mult=2.0)
        for _ in range(4):
            await m.record_reading("sensor", 1.0)  # avg=1W < min_avg=5W
        await m.record_reading("sensor", 100.0)
        calls = [c[0][0] for c in publish.call_args_list]
        assert "energy.anomaly" not in calls

    @pytest.mark.asyncio
    async def test_anomaly_payload(self):
        publish = AsyncMock()
        m = make_monitor(publish=publish, anomaly_mult=2.0, anomaly_window=5)
        for _ in range(4):
            await m.record_reading("boiler", 100.0)
        await m.record_reading("boiler", 500.0)
        # Find the energy.anomaly call
        anomaly_calls = [c for c in publish.call_args_list if c[0][0] == "energy.anomaly"]
        assert anomaly_calls
        payload = anomaly_calls[0][0][1]
        assert payload["device_id"] == "boiler"
        assert payload["watts"] == 500.0
        assert "average_watts" in payload


# ── kWh calculation ────────────────────────────────────────────────────────────

class TestKWhCalculation:
    def test_integrate_single_sample(self):
        from system_modules.energy_monitor.energy import EnergyMonitor
        # Single sample → 0 kWh (need at least 2 points)
        rows = [("100.0", "2024-06-01T12:00:00")]
        result = EnergyMonitor._integrate_kwh(rows)
        assert result == 0.0

    def test_integrate_empty(self):
        from system_modules.energy_monitor.energy import EnergyMonitor
        assert EnergyMonitor._integrate_kwh([]) == 0.0

    def test_integrate_two_points(self):
        from system_modules.energy_monitor.energy import EnergyMonitor
        # 1000W for 1 hour = 1 kWh
        rows = [
            (1000.0, "2024-06-01T12:00:00+00:00"),
            (1000.0, "2024-06-01T13:00:00+00:00"),
        ]
        result = EnergyMonitor._integrate_kwh(rows)
        assert abs(result - 1.0) < 0.001

    def test_integrate_varying_power(self):
        from system_modules.energy_monitor.energy import EnergyMonitor
        # 0W → 1000W over 1 hour → avg 500W → 0.5 kWh
        rows = [
            (0.0, "2024-06-01T12:00:00+00:00"),
            (1000.0, "2024-06-01T13:00:00+00:00"),
        ]
        result = EnergyMonitor._integrate_kwh(rows)
        assert abs(result - 0.5) < 0.001


# ── Device history ────────────────────────────────────────────────────────────

class TestDeviceHistory:
    @pytest.mark.asyncio
    async def test_get_device_history(self):
        m = make_monitor()
        await m.record_reading("heater", 2000.0)
        await m.record_reading("heater", 1800.0)
        history = m.get_device_history("heater")
        assert len(history) == 2
        assert "watts" in history[0]
        assert "ts" in history[0]

    @pytest.mark.asyncio
    async def test_get_all_devices(self):
        m = make_monitor()
        await m.record_reading("dev-x", 10.0)
        await m.record_reading("dev-y", 20.0)
        devices = m.get_all_devices()
        assert "dev-x" in devices
        assert "dev-y" in devices

    @pytest.mark.asyncio
    async def test_history_limit(self):
        m = make_monitor()
        for i in range(10):
            await m.record_reading("dev-z", float(i))
        history = m.get_device_history("dev-z", limit=3)
        assert len(history) <= 3


# ── Status ────────────────────────────────────────────────────────────────────

class TestStatus:
    @pytest.mark.asyncio
    async def test_status_initial(self):
        m = make_monitor()
        s = m.get_status()
        assert s["devices"] == 0
        assert s["total_power_w"] == 0.0

    @pytest.mark.asyncio
    async def test_status_after_readings(self):
        m = make_monitor()
        await m.record_reading("dev-1", 100.0)
        await m.record_reading("dev-2", 200.0)
        s = m.get_status()
        assert s["devices"] == 2
        assert s["total_power_w"] == pytest.approx(300.0)


# ── Start/Stop ─────────────────────────────────────────────────────────────────

class TestStartStop:
    @pytest.mark.asyncio
    async def test_start_creates_task(self):
        m = make_monitor()
        with patch.object(m, "_report_loop", new=AsyncMock()):
            await m.start()
            assert m._task is not None
            await m.stop()

    @pytest.mark.asyncio
    async def test_stop_without_start(self):
        m = make_monitor()
        await m.stop()  # should not raise


# ── API ───────────────────────────────────────────────────────────────────────

class TestEnergyAPI:
    def _make_app(self):
        import system_modules.energy_monitor.main as em_main
        mon = make_monitor()
        em_main._monitor = mon
        return em_main.app, mon

    @pytest.mark.asyncio
    async def test_health(self):
        from httpx import AsyncClient, ASGITransport
        app, _ = self._make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/health")
        assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_post_reading(self):
        from httpx import AsyncClient, ASGITransport
        app, mon = self._make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/energy/reading", json={"device_id": "my-device", "watts": 150.0})
        assert r.status_code == 201
        assert mon.get_current_power()["my-device"] == 150.0

    @pytest.mark.asyncio
    async def test_post_reading_negative_rejected(self):
        from httpx import AsyncClient, ASGITransport
        app, _ = self._make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/energy/reading", json={"device_id": "d", "watts": -10.0})
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_get_current(self):
        from httpx import AsyncClient, ASGITransport
        app, mon = self._make_app()
        await mon.record_reading("tv", 80.0)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/energy/current")
        assert r.status_code == 200
        assert r.json()["power"]["tv"] == 80.0

    @pytest.mark.asyncio
    async def test_get_today(self):
        from httpx import AsyncClient, ASGITransport
        app, _ = self._make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/energy/today")
        assert r.status_code == 200
        assert "total_kwh" in r.json()

    @pytest.mark.asyncio
    async def test_get_devices(self):
        from httpx import AsyncClient, ASGITransport
        app, mon = self._make_app()
        await mon.record_reading("dev-a", 10.0)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/energy/devices")
        assert r.status_code == 200
        assert "dev-a" in r.json()["devices"]

    @pytest.mark.asyncio
    async def test_get_device_history(self):
        from httpx import AsyncClient, ASGITransport
        app, mon = self._make_app()
        await mon.record_reading("sensor-1", 50.0)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/energy/devices/sensor-1/history")
        assert r.status_code == 200
        assert len(r.json()["history"]) >= 1

    @pytest.mark.asyncio
    async def test_status_endpoint(self):
        from httpx import AsyncClient, ASGITransport
        app, _ = self._make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/energy/status")
        assert r.status_code == 200
        assert "total_power_w" in r.json()

    @pytest.mark.asyncio
    async def test_widget_served(self):
        from httpx import AsyncClient, ASGITransport
        app, _ = self._make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/widget.html")
        assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_settings_served(self):
        from httpx import AsyncClient, ASGITransport
        app, _ = self._make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/settings.html")
        assert r.status_code == 200
