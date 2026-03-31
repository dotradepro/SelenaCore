# system_modules/weather_service/voice_handler.py
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .module import WeatherServiceModule

logger = logging.getLogger(__name__)


def _temp_unit_label(units: str) -> str:
    """Return TTS-friendly temperature unit label."""
    return "fahrenheit" if units == "imperial" else "celsius"


def _wind_unit_label(units: str) -> str:
    """Return TTS-friendly wind speed unit label."""
    return "miles per hour" if units == "imperial" else "kilometers per hour"


class WeatherVoiceHandler:
    def __init__(self, module: "WeatherServiceModule") -> None:
        self._module = module

    async def handle(self, intent: str, params: dict) -> None:
        m = self._module
        svc = m._weather

        if svc is None:
            await m.speak("Weather service is not ready yet.")
            return

        match intent:
            case "weather.current":
                current = svc.get_current()
                if current is None:
                    await m.speak("I don't have weather data yet. Please try again in a moment.")
                    return

                temp = current.get("temperature")
                feels = current.get("feels_like")
                condition = current.get("condition", "unknown")
                humidity = current.get("humidity")
                wind = current.get("wind_speed")
                units = current.get("units", "metric")
                t_unit = _temp_unit_label(units)
                w_unit = _wind_unit_label(units)

                location = svc._location_name
                loc_str = f" in {location}" if location else ""

                text = f"Currently{loc_str}: {condition}, {temp} degrees {t_unit}."
                if feels is not None and temp is not None and abs(feels - temp) >= 2:
                    text += f" Feels like {feels} degrees."
                if humidity is not None:
                    text += f" Humidity {humidity} percent."
                if wind is not None:
                    text += f" Wind speed {wind} {w_unit}."
                await m.speak(text)

            case "weather.forecast":
                forecast = svc.get_forecast()
                if not forecast:
                    await m.speak("I don't have forecast data yet. Please try again in a moment.")
                    return

                units = "metric"
                current = svc.get_current()
                if current:
                    units = current.get("units", "metric")
                t_unit = _temp_unit_label(units)

                period = params.get("period", "")

                if period and period.lower() in ("tomorrow", "завтра"):
                    # Only tomorrow
                    day = forecast[0] if forecast else None
                    if day is None:
                        await m.speak("No forecast data available for tomorrow.")
                        return
                    cond = day.get("condition", "unknown")
                    hi = day.get("temp_max")
                    lo = day.get("temp_min")
                    precip = day.get("precipitation", 0)
                    text = f"Tomorrow: {cond}, high {hi}, low {lo} degrees {t_unit}."
                    if precip and precip > 0:
                        text += f" Precipitation {precip} millimeters."
                    await m.speak(text)
                else:
                    # Full 3-day forecast
                    parts = ["Here is the forecast."]
                    day_labels = ["Tomorrow", "Day after tomorrow", "In three days"]
                    for i, day in enumerate(forecast[:3]):
                        label = day_labels[i] if i < len(day_labels) else day.get("date", "")
                        cond = day.get("condition", "unknown")
                        hi = day.get("temp_max")
                        lo = day.get("temp_min")
                        parts.append(f"{label}: {cond}, {hi} to {lo} degrees.")
                    await m.speak(" ".join(parts))

            case "weather.temperature":
                current = svc.get_current()
                if current is None:
                    await m.speak("I don't have temperature data yet. Please try again in a moment.")
                    return

                temp = current.get("temperature")
                feels = current.get("feels_like")
                units = current.get("units", "metric")
                t_unit = _temp_unit_label(units)

                text = f"The temperature is {temp} degrees {t_unit}."
                if feels is not None and temp is not None and abs(feels - temp) >= 2:
                    text += f" Feels like {feels} degrees."
                await m.speak(text)

            case _:
                logger.debug("WeatherVoiceHandler: unhandled intent '%s'", intent)
