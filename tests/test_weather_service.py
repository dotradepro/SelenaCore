"""tests/test_weather_service.py — pytest tests for weather_service module"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import httpx


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_service(publish=None, lat=50.45, lon=30.52, interval=1800, units="metric"):
    from system_modules.weather_service.weather import WeatherService
    return WeatherService(
        publish_event_cb=publish or AsyncMock(),
        latitude=lat,
        longitude=lon,
        update_interval_sec=interval,
        units=units,
    )


# Minimal Open-Meteo-like API response
FAKE_RESPONSE = {
    "current": {
        "temperature_2m": 21.5,
        "apparent_temperature": 19.0,
        "relative_humidity_2m": 65,
        "precipitation": 0.0,
        "wind_speed_10m": 12.3,
        "weather_code": 1,
    },
    "daily": {
        "time": ["2024-06-01", "2024-06-02", "2024-06-03", "2024-06-04"],
        "temperature_2m_max": [23.0, 25.0, 22.0, 20.0],
        "temperature_2m_min": [15.0, 16.0, 14.0, 13.0],
        "precipitation_sum": [0.0, 2.5, 10.0, 0.0],
        "weather_code": [1, 3, 63, 0],
    },
}

RAINY_RESPONSE = {
    "current": {
        "temperature_2m": 12.0,
        "apparent_temperature": 10.0,
        "relative_humidity_2m": 90,
        "precipitation": 15.0,  # above alert threshold
        "wind_speed_10m": 60.0,  # above alert threshold
        "weather_code": 63,
    },
    "daily": {
        "time": ["2024-06-01", "2024-06-02"],
        "temperature_2m_max": [14.0, 15.0],
        "temperature_2m_min": [10.0, 11.0],
        "precipitation_sum": [15.0, 5.0],
        "weather_code": [63, 3],
    },
}


def make_httpx_response(data: dict, status: int = 200):
    req = httpx.Request("GET", "https://api.open-meteo.com/v1/forecast")
    return httpx.Response(status_code=status, json=data, request=req)


# ── WMO helpers ───────────────────────────────────────────────────────────────

class TestWMOHelpers:
    def test_known_code(self):
        from system_modules.weather_service.weather import wmo_description, wmo_emoji
        assert wmo_description(0) == "Clear sky"
        assert wmo_emoji(0) == "☀️"

    def test_known_code_rain(self):
        from system_modules.weather_service.weather import wmo_description
        assert wmo_description(63) == "Moderate rain"

    def test_unknown_code(self):
        from system_modules.weather_service.weather import wmo_description, wmo_emoji
        assert wmo_description(999) == "Unknown"
        assert wmo_emoji(999) == "❓"

    def test_none_code(self):
        from system_modules.weather_service.weather import wmo_description, wmo_emoji
        assert wmo_description(None) == "Unknown"
        assert wmo_emoji(None) == "❓"


# ── Parsing ───────────────────────────────────────────────────────────────────

class TestParsing:
    def test_parse_current(self):
        svc = make_service()
        current = svc._parse_current(FAKE_RESPONSE)
        assert current["temperature"] == 21.5
        assert current["humidity"] == 65
        assert current["wind_speed"] == 12.3
        assert current["condition"] == "Mainly clear"
        assert current["units"] == "metric"

    def test_parse_forecast_returns_3_days(self):
        svc = make_service()
        forecast = svc._parse_forecast(FAKE_RESPONSE)
        assert len(forecast) == 3

    def test_parse_forecast_skips_today(self):
        svc = make_service()
        forecast = svc._parse_forecast(FAKE_RESPONSE)
        # First forecast day should be index 1 in daily data (2024-06-02)
        assert forecast[0]["date"] == "2024-06-02"

    def test_parse_forecast_fields(self):
        svc = make_service()
        forecast = svc._parse_forecast(FAKE_RESPONSE)
        day = forecast[0]
        assert "temp_max" in day
        assert "temp_min" in day
        assert "precipitation" in day
        assert "condition" in day
        assert "wmo_code" in day

    def test_parse_forecast_short_data(self):
        svc = make_service()
        # Only 2 days available (skip today → 1 forecast day)
        forecast = svc._parse_forecast(RAINY_RESPONSE)
        assert len(forecast) == 1


# ── Fetch ──────────────────────────────────────────────────────────────────────

class TestFetch:
    @pytest.mark.asyncio
    async def test_fetch_success(self):
        publish = AsyncMock()
        svc = make_service(publish=publish)

        mock_resp = make_httpx_response(FAKE_RESPONSE)
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.get.return_value = mock_resp

            result = await svc.fetch()

        assert result["current"]["temperature"] == 21.5
        assert len(result["forecast"]) == 3
        assert svc.get_current() is not None
        assert svc._error is None

    @pytest.mark.asyncio
    async def test_fetch_publishes_event(self):
        publish = AsyncMock()
        svc = make_service(publish=publish)

        mock_resp = make_httpx_response(FAKE_RESPONSE)
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.get.return_value = mock_resp
            await svc.fetch()

        calls = [c[0][0] for c in publish.call_args_list]
        assert "weather.updated" in calls

    @pytest.mark.asyncio
    async def test_fetch_alert_on_heavy_rain(self):
        publish = AsyncMock()
        svc = make_service(publish=publish)
        svc._alert_rain = 10.0  # 15mm > 10mm → alert

        mock_resp = make_httpx_response(RAINY_RESPONSE)
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.get.return_value = mock_resp
            await svc.fetch()

        calls = [c[0][0] for c in publish.call_args_list]
        assert "weather.alert" in calls

    @pytest.mark.asyncio
    async def test_fetch_no_alert_below_threshold(self):
        publish = AsyncMock()
        svc = make_service(publish=publish)
        svc._alert_rain = 50.0  # 0.0mm < 50mm → no alert
        svc._alert_wind = 100.0

        mock_resp = make_httpx_response(FAKE_RESPONSE)
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.get.return_value = mock_resp
            await svc.fetch()

        calls = [c[0][0] for c in publish.call_args_list]
        assert "weather.alert" not in calls

    @pytest.mark.asyncio
    async def test_fetch_error_sets_error_field(self):
        svc = make_service()
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.get.side_effect = httpx.ConnectError("connection refused")

            with pytest.raises(httpx.ConnectError):
                await svc.fetch()

        assert svc._error is not None
        assert svc.get_current() is None


# ── Configure ─────────────────────────────────────────────────────────────────

class TestConfigure:
    def test_configure_updates_fields(self):
        svc = make_service(lat=50.0, lon=30.0, interval=900)
        svc.configure(latitude=48.5, longitude=35.0, update_interval_sec=600, units="imperial")
        assert svc.latitude == 48.5
        assert svc.longitude == 35.0
        assert svc._interval == 600
        assert svc._units == "imperial"

    def test_configure_invalidates_cache(self):
        svc = make_service()
        svc._current = {"temperature": 20.0}
        svc._forecast = [{"date": "2024-06-02"}]
        svc.configure(latitude=48.0)
        assert svc._current is None
        assert svc._forecast == []

    def test_configure_partial(self):
        svc = make_service(lat=50.0, lon=30.0)
        svc.configure(units="imperial")
        assert svc.latitude == 50.0  # unchanged
        assert svc._units == "imperial"


# ── Status ────────────────────────────────────────────────────────────────────

class TestStatus:
    def test_status_initial(self):
        svc = make_service()
        s = svc.get_status()
        assert s["has_data"] is False
        assert s["error"] is None
        assert s["last_updated"] is None

    def test_status_after_data(self):
        svc = make_service()
        svc._current = {"temperature": 20.0}
        svc._last_updated = "2024-06-01T12:00:00+00:00"
        s = svc.get_status()
        assert s["has_data"] is True
        assert s["last_updated"] is not None


# ── Start/Stop ─────────────────────────────────────────────────────────────────

class TestStartStop:
    @pytest.mark.asyncio
    async def test_start_creates_task(self):
        svc = make_service()
        with patch.object(svc, "_update_loop", new=AsyncMock()):
            await svc.start()
            assert svc._task is not None
            await svc.stop()

    @pytest.mark.asyncio
    async def test_stop_without_start(self):
        svc = make_service()
        await svc.stop()  # should not raise


# ── API ───────────────────────────────────────────────────────────────────────

class TestWeatherAPI:
    def _make_app(self):
        import system_modules.weather_service.main as ws_main
        svc = make_service()
        ws_main._weather = svc
        return ws_main.app, svc

    @pytest.mark.asyncio
    async def test_health(self):
        from httpx import AsyncClient, ASGITransport
        app, _ = self._make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    @pytest.mark.asyncio
    async def test_current_no_data_returns_503(self):
        from httpx import AsyncClient, ASGITransport
        app, svc = self._make_app()
        svc._current = None
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/weather/current")
        assert r.status_code == 503

    @pytest.mark.asyncio
    async def test_current_with_data(self):
        from httpx import AsyncClient, ASGITransport
        app, svc = self._make_app()
        svc._current = {"temperature": 22.0, "condition": "Clear sky"}
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/weather/current")
        assert r.status_code == 200
        assert r.json()["temperature"] == 22.0

    @pytest.mark.asyncio
    async def test_forecast_empty(self):
        from httpx import AsyncClient, ASGITransport
        app, svc = self._make_app()
        svc._forecast = []
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/weather/forecast")
        assert r.status_code == 200
        assert r.json()["forecast"] == []

    @pytest.mark.asyncio
    async def test_status_endpoint(self):
        from httpx import AsyncClient, ASGITransport
        app, _ = self._make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/weather/status")
        assert r.status_code == 200
        data = r.json()
        assert "latitude" in data
        assert "has_data" in data

    @pytest.mark.asyncio
    async def test_refresh_ok(self):
        from httpx import AsyncClient, ASGITransport
        app, svc = self._make_app()
        mock_data = {"current": {"temperature": 20.0}, "forecast": []}
        with patch.object(svc, "fetch", new=AsyncMock(return_value=mock_data)):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                r = await c.post("/weather/refresh")
        assert r.status_code == 200
        assert r.json()["ok"] is True

    @pytest.mark.asyncio
    async def test_refresh_error_returns_502(self):
        from httpx import AsyncClient, ASGITransport
        app, svc = self._make_app()
        with patch.object(svc, "fetch", new=AsyncMock(side_effect=Exception("network error"))):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                r = await c.post("/weather/refresh")
        assert r.status_code == 502

    @pytest.mark.asyncio
    async def test_config_endpoint(self):
        from httpx import AsyncClient, ASGITransport
        app, svc = self._make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/weather/config", json={"latitude": 48.5, "longitude": 35.0})
        assert r.status_code == 200
        assert r.json()["ok"] is True
        assert svc.latitude == 48.5

    @pytest.mark.asyncio
    async def test_widget_served(self):
        from httpx import AsyncClient, ASGITransport
        app, _ = self._make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/widget.html")
        assert r.status_code == 200
        assert "weather" in r.text.lower()

    @pytest.mark.asyncio
    async def test_settings_served(self):
        from httpx import AsyncClient, ASGITransport
        app, _ = self._make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/settings.html")
        assert r.status_code == 200
        assert "settings" in r.text.lower()
