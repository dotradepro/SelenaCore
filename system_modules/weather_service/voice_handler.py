# system_modules/weather_service/voice_handler.py
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .module import WeatherServiceModule

logger = logging.getLogger(__name__)

# WMO weather condition codes → English descriptions
_WMO_CONDITIONS: dict[int, str] = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Foggy", 48: "Depositing rime fog",
    51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
    56: "Light freezing drizzle", 57: "Dense freezing drizzle",
    61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
    66: "Light freezing rain", 67: "Heavy freezing rain",
    71: "Slight snow", 73: "Moderate snow", 75: "Heavy snow",
    77: "Snow grains", 80: "Slight rain showers", 81: "Moderate rain showers",
    82: "Violent rain showers", 85: "Slight snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm", 96: "Thunderstorm with slight hail",
    99: "Thunderstorm with heavy hail",
}


def _condition_text(data: dict) -> str:
    """Get weather condition text from WMO code or fallback to raw condition."""
    wmo = data.get("wmo_code")
    if wmo is not None and wmo in _WMO_CONDITIONS:
        return _WMO_CONDITIONS[wmo]
    return data.get("condition", "unknown")


class WeatherVoiceHandler:
    def __init__(self, module: "WeatherServiceModule") -> None:
        self._module = module

    async def handle(self, intent: str, params: dict) -> dict | None:
        svc = self._module._weather

        if svc is None:
            return {"action": "not_ready"}

        match intent:
            case "weather.current":
                current = svc.get_current()
                if current is None:
                    return {"action": "no_data"}

                ctx: dict = {
                    "action": "current",
                    "temperature": current.get("temperature"),
                    "condition": _condition_text(current),
                }
                location = svc._location_name
                if location:
                    ctx["location"] = location
                feels = current.get("feels_like")
                temp = current.get("temperature")
                if feels is not None and temp is not None and abs(feels - temp) >= 2:
                    ctx["feels_like"] = feels
                humidity = current.get("humidity")
                if humidity is not None:
                    ctx["humidity"] = humidity
                wind = current.get("wind_speed")
                if wind is not None:
                    ctx["wind_speed"] = wind
                    ctx["units"] = current.get("units", "metric")
                return ctx

            case "weather.forecast":
                forecast = svc.get_forecast()
                if not forecast:
                    return {"action": "no_data"}

                period = params.get("period", "")

                if period and period.lower() in ("tomorrow", "завтра"):
                    day = forecast[0] if forecast else None
                    if day is None:
                        return {"action": "no_data"}
                    ctx = {
                        "action": "forecast_tomorrow",
                        "condition": _condition_text(day),
                        "temp_max": day.get("temp_max"),
                        "temp_min": day.get("temp_min"),
                    }
                    precip = day.get("precipitation", 0)
                    if precip and precip > 0:
                        ctx["precipitation"] = precip
                    return ctx
                else:
                    days = []
                    for day in forecast[:3]:
                        days.append({
                            "condition": _condition_text(day),
                            "temp_max": day.get("temp_max"),
                            "temp_min": day.get("temp_min"),
                        })
                    return {"action": "forecast_multi", "days": days}

            case "weather.temperature":
                current = svc.get_current()
                if current is None:
                    return {"action": "no_data"}

                temp = current.get("temperature")
                feels = current.get("feels_like")
                ctx = {"action": "temperature", "temperature": temp}
                if feels is not None and temp is not None and abs(feels - temp) >= 2:
                    ctx["feels_like"] = feels
                return ctx

            case _:
                logger.debug("WeatherVoiceHandler: unhandled intent '%s'", intent)
                return None
