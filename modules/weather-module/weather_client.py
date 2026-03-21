"""
weather_client.py — Open-Meteo API client for weather-module

No API key required. Free, no rate-limit for reasonable usage.
  Geocoding:  https://geocoding-api.open-meteo.com/v1/search
  Forecast:   https://api.open-meteo.com/v1/forecast
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

CACHE_TTL = 600  # 10 minutes

# WMO Weather Interpretation Codes → (emoji, English description, Ukrainian description)
WMO_CODES: dict[int, tuple[str, str, str]] = {
    0:  ("☀️",  "Clear sky",               "Ясне небо"),
    1:  ("🌤️", "Mainly clear",             "Переважно ясно"),
    2:  ("⛅",  "Partly cloudy",            "Хмарно з проясненнями"),
    3:  ("☁️",  "Overcast",                "Похмуро"),
    45: ("🌫️", "Fog",                      "Туман"),
    48: ("🌫️", "Freezing fog",             "Ожеледиця"),
    51: ("🌦️", "Light drizzle",            "Легка мряка"),
    53: ("🌦️", "Moderate drizzle",         "Мряка"),
    55: ("🌧️", "Dense drizzle",            "Сильна мряка"),
    61: ("🌧️", "Slight rain",              "Невеликий дощ"),
    63: ("🌧️", "Moderate rain",            "Дощ"),
    65: ("🌧️", "Heavy rain",               "Сильний дощ"),
    71: ("🌨️", "Slight snow",              "Невеликий сніг"),
    73: ("❄️",  "Moderate snow",            "Сніг"),
    75: ("❄️",  "Heavy snow",               "Сильний сніг"),
    77: ("🌨️", "Snow grains",              "Сніжна крупа"),
    80: ("🌦️", "Slight rain showers",      "Невеликі зливи"),
    81: ("🌧️", "Moderate rain showers",    "Зливи"),
    82: ("⛈️",  "Violent rain showers",    "Сильні зливи"),
    85: ("🌨️", "Slight snow showers",      "Невеликий снігопад"),
    86: ("❄️",  "Heavy snow showers",       "Сильний снігопад"),
    95: ("⛈️",  "Thunderstorm",             "Гроза"),
    96: ("⛈️",  "Thunderstorm with hail",   "Гроза з градом"),
    99: ("⛈️",  "Thunderstorm, heavy hail", "Гроза із сильним градом"),
}

# TZ environment variable → default city (covers common deployments)
TZ_CITY_MAP: dict[str, dict[str, Any]] = {
    "Europe/Kyiv":        {"name": "Kyiv",        "lat": 50.4501,  "lon": 30.5234,   "country": "UA"},
    "Europe/Kiev":        {"name": "Kyiv",        "lat": 50.4501,  "lon": 30.5234,   "country": "UA"},
    "Europe/London":      {"name": "London",      "lat": 51.5074,  "lon": -0.1278,   "country": "GB"},
    "Europe/Berlin":      {"name": "Berlin",      "lat": 52.5200,  "lon": 13.4050,   "country": "DE"},
    "Europe/Paris":       {"name": "Paris",       "lat": 48.8566,  "lon": 2.3522,    "country": "FR"},
    "Europe/Warsaw":      {"name": "Warsaw",      "lat": 52.2297,  "lon": 21.0122,   "country": "PL"},
    "Europe/Moscow":      {"name": "Moscow",      "lat": 55.7558,  "lon": 37.6176,   "country": "RU"},
    "Europe/Amsterdam":   {"name": "Amsterdam",   "lat": 52.3676,  "lon": 4.9041,    "country": "NL"},
    "Europe/Prague":      {"name": "Prague",      "lat": 50.0755,  "lon": 14.4378,   "country": "CZ"},
    "Europe/Bucharest":   {"name": "Bucharest",   "lat": 44.4268,  "lon": 26.1025,   "country": "RO"},
    "America/New_York":   {"name": "New York",    "lat": 40.7128,  "lon": -74.0060,  "country": "US"},
    "America/Chicago":    {"name": "Chicago",     "lat": 41.8781,  "lon": -87.6298,  "country": "US"},
    "America/Los_Angeles":{"name": "Los Angeles", "lat": 34.0522,  "lon": -118.2437, "country": "US"},
    "Asia/Tokyo":         {"name": "Tokyo",       "lat": 35.6762,  "lon": 139.6503,  "country": "JP"},
    "Asia/Dubai":         {"name": "Dubai",       "lat": 25.2048,  "lon": 55.2708,   "country": "AE"},
    "Australia/Sydney":   {"name": "Sydney",      "lat": -33.8688, "lon": 151.2093,  "country": "AU"},
}

DEFAULT_CITY: dict[str, Any] = {
    "name": "Kyiv", "lat": 50.4501, "lon": 30.5234, "country": "UA"
}


def wmo_info(code: int) -> tuple[str, str, str]:
    """Return (emoji, english_description, ukrainian_description) for a WMO code."""
    return WMO_CODES.get(code, ("🌡️", "Unknown", "Невідомо"))


def get_default_city() -> dict[str, Any]:
    """Determine default city from TZ environment variable.

    Falls back to Kyiv if timezone is not mapped.
    """
    tz = os.environ.get("TZ", "")
    return TZ_CITY_MAP.get(tz, DEFAULT_CITY).copy()


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class WeatherData:
    city: str
    country: str
    lat: float
    lon: float
    temperature: float
    feels_like: float
    humidity: int
    wind_speed: float
    wind_direction: int
    wmo_code: int
    emoji: str
    condition_en: str
    condition_uk: str
    is_day: bool
    units: str
    updated_at: float = field(default_factory=time.time)


@dataclass
class HourlyEntry:
    time: str        # ISO "2026-03-21T14:00"
    temperature: float
    precip_prob: int
    wind_speed: float
    wmo_code: int
    emoji: str


@dataclass
class DailyEntry:
    date: str        # "2026-03-21"
    temp_min: float
    temp_max: float
    precip_prob: int
    wmo_code: int
    emoji: str
    condition_en: str
    condition_uk: str


@dataclass
class ForecastData:
    hourly_today: list[HourlyEntry]
    daily_7: list[DailyEntry]
    daily_10: list[DailyEntry]


# ── Client ────────────────────────────────────────────────────────────────────

class WeatherClient:
    """Async Open-Meteo client with 10-minute in-memory cache."""

    def __init__(self) -> None:
        self._weather_cache: WeatherData | None = None
        self._forecast_cache: ForecastData | None = None
        self._cache_time: float = 0.0

    def _cache_valid(self) -> bool:
        return (time.time() - self._cache_time) < CACHE_TTL

    def invalidate_cache(self) -> None:
        self._cache_time = 0.0
        self._weather_cache = None
        self._forecast_cache = None

    async def search_city(self, query: str, limit: int = 6) -> list[dict[str, Any]]:
        """Search cities by name using Open-Meteo Geocoding API."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(GEOCODING_URL, params={
                "name": query,
                "count": limit,
                "language": "en",
                "format": "json",
            })
            resp.raise_for_status()
            data = resp.json()

        results = data.get("results", [])
        return [
            {
                "name": r.get("name", ""),
                "country": r.get("country_code", ""),
                "admin1": r.get("admin1", ""),
                "lat": r.get("latitude", 0.0),
                "lon": r.get("longitude", 0.0),
                "display": ", ".join(filter(None, [
                    r.get("name"), r.get("admin1"), r.get("country_code"),
                ])),
            }
            for r in results
        ]

    async def get_weather(
        self,
        lat: float,
        lon: float,
        city: str,
        country: str,
        units: str = "celsius",
    ) -> WeatherData:
        """Fetch current weather. Returns cached data if fresh (< 10 min)."""
        if self._cache_valid() and self._weather_cache is not None:
            return self._weather_cache

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(FORECAST_URL, params={
                "latitude": lat,
                "longitude": lon,
                "current_weather": "true",
                "current": "relative_humidity_2m,apparent_temperature",
                "temperature_unit": "celsius" if units == "celsius" else "fahrenheit",
                "wind_speed_unit": "ms",
                "timezone": "auto",
                "forecast_days": 1,
            })
            resp.raise_for_status()
            data = resp.json()

        cw = data.get("current_weather", {})
        current = data.get("current", {})
        code = int(cw.get("weathercode", 0))
        emoji, cond_en, cond_uk = wmo_info(code)

        weather = WeatherData(
            city=city,
            country=country,
            lat=lat,
            lon=lon,
            temperature=round(float(cw.get("temperature", 0)), 1),
            feels_like=round(float(current.get("apparent_temperature", cw.get("temperature", 0))), 1),
            humidity=int(current.get("relative_humidity_2m", 0)),
            wind_speed=round(float(cw.get("windspeed", 0)), 1),
            wind_direction=int(cw.get("winddirection", 0)),
            wmo_code=code,
            emoji=emoji,
            condition_en=cond_en,
            condition_uk=cond_uk,
            is_day=bool(cw.get("is_day", 1)),
            units=units,
        )
        self._weather_cache = weather
        self._cache_time = time.time()
        return weather

    async def get_forecast(
        self,
        lat: float,
        lon: float,
        units: str = "celsius",
    ) -> ForecastData:
        """Fetch hourly forecast for today + daily for 10 days."""
        if self._cache_valid() and self._forecast_cache is not None:
            return self._forecast_cache

        temperature_unit = "celsius" if units == "celsius" else "fahrenheit"

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(FORECAST_URL, params={
                "latitude": lat,
                "longitude": lon,
                "hourly": "temperature_2m,precipitation_probability,wind_speed_10m,weathercode",
                "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weathercode",
                "temperature_unit": temperature_unit,
                "wind_speed_unit": "ms",
                "timezone": "auto",
                "forecast_days": 10,
            })
            resp.raise_for_status()
            data = resp.json()

        hourly = data.get("hourly", {})
        daily = data.get("daily", {})

        # Extract today's date from the first hourly timestamp
        h_times: list[str] = hourly.get("time", [])
        today = h_times[0][:10] if h_times else ""

        h_temps:  list[float] = hourly.get("temperature_2m", [])
        h_precip: list[int]   = hourly.get("precipitation_probability", [])
        h_wind:   list[float] = hourly.get("wind_speed_10m", [])
        h_codes:  list[int]   = hourly.get("weathercode", [])

        def _safe(lst: list, idx: int, default: Any = 0) -> Any:
            return lst[idx] if idx < len(lst) else default

        hourly_today: list[HourlyEntry] = []
        for i, t in enumerate(h_times):
            if not t.startswith(today):
                continue
            code = int(_safe(h_codes, i, 0) or 0)
            emoji, _, _ = wmo_info(code)
            hourly_today.append(HourlyEntry(
                time=t,
                temperature=round(float(_safe(h_temps, i, 0) or 0), 1),
                precip_prob=int(_safe(h_precip, i, 0) or 0),
                wind_speed=round(float(_safe(h_wind, i, 0) or 0), 1),
                wmo_code=code,
                emoji=emoji,
            ))

        d_times:  list[str]   = daily.get("time", [])
        d_maxes:  list[float] = daily.get("temperature_2m_max", [])
        d_mins:   list[float] = daily.get("temperature_2m_min", [])
        d_precip: list[int]   = daily.get("precipitation_probability_max", [])
        d_codes:  list[int]   = daily.get("weathercode", [])

        daily_entries: list[DailyEntry] = []
        for i, d in enumerate(d_times):
            code = int(_safe(d_codes, i, 0) or 0)
            emoji, cond_en, cond_uk = wmo_info(code)
            daily_entries.append(DailyEntry(
                date=d,
                temp_min=round(float(_safe(d_mins, i, 0) or 0), 1),
                temp_max=round(float(_safe(d_maxes, i, 0) or 0), 1),
                precip_prob=int(_safe(d_precip, i, 0) or 0),
                wmo_code=code,
                emoji=emoji,
                condition_en=cond_en,
                condition_uk=cond_uk,
            ))

        result = ForecastData(
            hourly_today=hourly_today,
            daily_7=daily_entries[:7],
            daily_10=daily_entries,
        )
        self._forecast_cache = result
        return result
