# system_modules/weather_service/voice_handler.py
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from core.i18n import t

if TYPE_CHECKING:
    from .module import WeatherServiceModule

logger = logging.getLogger(__name__)


def _localized_condition(data: dict) -> str:
    """Get weather condition text in the current locale via WMO code."""
    wmo = data.get("wmo_code")
    if wmo is not None:
        localized = t(f"wmo.{wmo}")
        if not localized.startswith("wmo."):
            return localized
    return data.get("condition", "")


class WeatherVoiceHandler:
    def __init__(self, module: "WeatherServiceModule") -> None:
        self._module = module

    async def handle(self, intent: str, params: dict) -> None:
        m = self._module
        svc = m._weather

        if svc is None:
            await m.speak(t("weather.not_ready"))
            return

        match intent:
            case "weather.current":
                current = svc.get_current()
                if current is None:
                    await m.speak(t("weather.no_data"))
                    return

                temp = current.get("temperature")
                feels = current.get("feels_like")
                condition = _localized_condition(current)
                humidity = current.get("humidity")
                wind = current.get("wind_speed")
                units = current.get("units", "metric")

                location = svc._location_name
                loc_str = f" ({location})" if location else ""

                text = t("weather.current", location=loc_str, condition=condition, temp=temp)
                if feels is not None and temp is not None and abs(feels - temp) >= 2:
                    text += t("weather.current_feels", feels=feels)
                if humidity is not None:
                    text += t("weather.current_humidity", humidity=humidity)
                if wind is not None:
                    wind_key = "weather.current_wind_imperial" if units == "imperial" else "weather.current_wind"
                    text += t(wind_key, wind=wind)
                await m.speak(text)

            case "weather.forecast":
                forecast = svc.get_forecast()
                if not forecast:
                    await m.speak(t("weather.no_data"))
                    return

                period = params.get("period", "")

                if period and period.lower() in ("tomorrow", "завтра"):
                    day = forecast[0] if forecast else None
                    if day is None:
                        await m.speak(t("weather.no_data"))
                        return
                    cond = _localized_condition(day)
                    hi = day.get("temp_max")
                    lo = day.get("temp_min")
                    precip = day.get("precipitation", 0)
                    text = t("weather.forecast_tomorrow", condition=cond, hi=hi, lo=lo)
                    if precip and precip > 0:
                        text += t("weather.forecast_precip", precip=precip)
                    await m.speak(text)
                else:
                    labels = [
                        t("weather.forecast_label_1"),
                        t("weather.forecast_label_2"),
                        t("weather.forecast_label_3"),
                    ]
                    parts = [t("weather.forecast_intro")]
                    for i, day in enumerate(forecast[:3]):
                        label = labels[i] if i < len(labels) else day.get("date", "")
                        cond = _localized_condition(day)
                        hi = day.get("temp_max")
                        lo = day.get("temp_min")
                        parts.append(t("weather.forecast_day", label=label, condition=cond, hi=hi, lo=lo))
                    await m.speak(" ".join(parts))

            case "weather.temperature":
                current = svc.get_current()
                if current is None:
                    await m.speak(t("weather.no_data"))
                    return

                temp = current.get("temperature")
                feels = current.get("feels_like")

                text = t("weather.temperature", temp=temp)
                if feels is not None and temp is not None and abs(feels - temp) >= 2:
                    text += t("weather.temperature_feels", feels=feels)
                await m.speak(text)

            case _:
                logger.debug("WeatherVoiceHandler: unhandled intent '%s'", intent)
