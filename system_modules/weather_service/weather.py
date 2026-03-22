"""
system_modules/weather_service/weather.py — WeatherService business logic

Uses Open-Meteo API (https://api.open-meteo.com) — free, no API key required.

Data provided:
  - Current conditions: temperature, feels_like, humidity, wind_speed,
                        precipitation, condition (WMO code → text)
  - 3-day daily forecast: min/max temp, precipitation sum, condition

Events published:
  weather.updated  — after each successful fetch
  weather.alert    — when precipitation > alert_threshold_mm or wind > alert_threshold_kmh
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# WMO Weather interpretation codes → (description, icon_emoji)
WMO_CODES: dict[int, tuple[str, str]] = {
    0:  ("Clear sky", "☀️"),
    1:  ("Mainly clear", "🌤️"),
    2:  ("Partly cloudy", "⛅"),
    3:  ("Overcast", "☁️"),
    45: ("Foggy", "🌫️"),
    48: ("Icy fog", "🌫️"),
    51: ("Light drizzle", "🌦️"),
    53: ("Moderate drizzle", "🌦️"),
    55: ("Dense drizzle", "🌧️"),
    61: ("Slight rain", "🌧️"),
    63: ("Moderate rain", "🌧️"),
    65: ("Heavy rain", "🌧️"),
    66: ("Light freezing rain", "🌨️"),
    67: ("Heavy freezing rain", "🌨️"),
    71: ("Slight snowfall", "❄️"),
    73: ("Moderate snowfall", "❄️"),
    75: ("Heavy snowfall", "❄️"),
    77: ("Snow grains", "🌨️"),
    80: ("Slight rain showers", "🌦️"),
    81: ("Moderate rain showers", "🌧️"),
    82: ("Violent rain showers", "⛈️"),
    85: ("Slight snow showers", "🌨️"),
    86: ("Heavy snow showers", "🌨️"),
    95: ("Thunderstorm", "⛈️"),
    96: ("Thunderstorm with hail", "⛈️"),
    99: ("Thunderstorm with heavy hail", "⛈️"),
}

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"


def wmo_description(code: int | None) -> str:
    if code is None:
        return "Unknown"
    desc, _ = WMO_CODES.get(code, ("Unknown", "❓"))
    return desc


def wmo_emoji(code: int | None) -> str:
    if code is None:
        return "❓"
    _, emoji = WMO_CODES.get(code, ("Unknown", "❓"))
    return emoji


class WeatherService:
    def __init__(
        self,
        publish_event_cb: Any,
        latitude: float = 50.4501,      # default: Kyiv
        longitude: float = 30.5234,
        update_interval_sec: int = 1800,
        units: str = "metric",          # "metric" | "imperial"
        alert_rain_mm: float = 10.0,    # threshold to fire weather.alert
        alert_wind_kmh: float = 50.0,
    ) -> None:
        self._publish = publish_event_cb
        self.latitude = latitude
        self.longitude = longitude
        self._interval = update_interval_sec
        self._units = units
        self._alert_rain = alert_rain_mm
        self._alert_wind = alert_wind_kmh

        self._current: dict[str, Any] | None = None
        self._forecast: list[dict[str, Any]] = []
        self._last_updated: str | None = None
        self._error: str | None = None
        self._task: asyncio.Task | None = None

    # ── Public API ────────────────────────────────────────────────────────────

    def get_current(self) -> dict[str, Any] | None:
        return self._current

    def get_forecast(self) -> list[dict[str, Any]]:
        return self._forecast

    def get_status(self) -> dict[str, Any]:
        return {
            "latitude": self.latitude,
            "longitude": self.longitude,
            "units": self._units,
            "update_interval_sec": self._interval,
            "last_updated": self._last_updated,
            "error": self._error,
            "has_data": self._current is not None,
        }

    def configure(
        self,
        latitude: float | None = None,
        longitude: float | None = None,
        update_interval_sec: int | None = None,
        units: str | None = None,
        alert_rain_mm: float | None = None,
        alert_wind_kmh: float | None = None,
    ) -> None:
        if latitude is not None:
            self.latitude = latitude
        if longitude is not None:
            self.longitude = longitude
        if update_interval_sec is not None:
            self._interval = update_interval_sec
        if units is not None:
            self._units = units
        if alert_rain_mm is not None:
            self._alert_rain = alert_rain_mm
        if alert_wind_kmh is not None:
            self._alert_wind = alert_wind_kmh
        # invalidate cache so next loop triggers fresh fetch
        self._current = None
        self._forecast = []

    # ── Fetch ─────────────────────────────────────────────────────────────────

    async def fetch(self) -> dict[str, Any]:
        """Fetch current + forecast from Open-Meteo. Returns parsed data dict."""
        temp_unit = "fahrenheit" if self._units == "imperial" else "celsius"
        wind_unit = "mph" if self._units == "imperial" else "kmh"

        params = {
            "latitude": self.latitude,
            "longitude": self.longitude,
            "current": [
                "temperature_2m",
                "apparent_temperature",
                "relative_humidity_2m",
                "precipitation",
                "wind_speed_10m",
                "weather_code",
            ],
            "daily": [
                "temperature_2m_max",
                "temperature_2m_min",
                "precipitation_sum",
                "weather_code",
            ],
            "temperature_unit": temp_unit,
            "wind_speed_unit": wind_unit,
            "forecast_days": 4,
            "timezone": "auto",
        }

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(OPEN_METEO_URL, params=params)
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            self._error = str(exc)
            logger.error("Weather fetch failed: %s", exc)
            raise

        current = self._parse_current(data)
        forecast = self._parse_forecast(data)

        self._current = current
        self._forecast = forecast
        self._last_updated = datetime.now(tz=timezone.utc).isoformat()
        self._error = None

        await self._publish("weather.updated", {
            "current": current,
            "forecast": forecast,
        })

        # Check alerts
        await self._check_alerts(current)

        return {"current": current, "forecast": forecast}

    def _parse_current(self, data: dict) -> dict[str, Any]:
        c = data.get("current", {})
        code = c.get("weather_code")
        return {
            "temperature": c.get("temperature_2m"),
            "feels_like": c.get("apparent_temperature"),
            "humidity": c.get("relative_humidity_2m"),
            "precipitation": c.get("precipitation"),
            "wind_speed": c.get("wind_speed_10m"),
            "condition": wmo_description(code),
            "condition_emoji": wmo_emoji(code),
            "wmo_code": code,
            "units": self._units,
        }

    def _parse_forecast(self, data: dict) -> list[dict[str, Any]]:
        daily = data.get("daily", {})
        dates = daily.get("time", [])
        max_temps = daily.get("temperature_2m_max", [])
        min_temps = daily.get("temperature_2m_min", [])
        precips = daily.get("precipitation_sum", [])
        codes = daily.get("weather_code", [])

        result = []
        # skip today (index 0), return next 3 days
        for i in range(1, min(4, len(dates))):
            code = codes[i] if i < len(codes) else None
            result.append({
                "date": dates[i] if i < len(dates) else None,
                "temp_max": max_temps[i] if i < len(max_temps) else None,
                "temp_min": min_temps[i] if i < len(min_temps) else None,
                "precipitation": precips[i] if i < len(precips) else None,
                "condition": wmo_description(code),
                "condition_emoji": wmo_emoji(code),
                "wmo_code": code,
            })
        return result

    async def _check_alerts(self, current: dict) -> None:
        alerts: list[str] = []
        prec = current.get("precipitation") or 0.0
        wind = current.get("wind_speed") or 0.0

        if prec > self._alert_rain:
            alerts.append(f"Heavy precipitation: {prec} mm")
        if wind > self._alert_wind:
            alerts.append(f"Strong wind: {wind} km/h")

        if alerts:
            await self._publish("weather.alert", {
                "alerts": alerts,
                "current": current,
            })

    # ── Background loop ────────────────────────────────────────────────────────

    async def start(self) -> None:
        self._task = asyncio.get_event_loop().create_task(self._update_loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _update_loop(self) -> None:
        while True:
            try:
                await self.fetch()
            except Exception:
                pass  # error already logged in fetch()
            await asyncio.sleep(self._interval)
