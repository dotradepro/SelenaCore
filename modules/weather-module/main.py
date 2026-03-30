"""
main.py — SelenaCore Weather Module (WebSocket bus client)

Provides current weather and 10-day forecast via Open-Meteo (free, no API key).
Declares voice intents so IntentRouter Tier 2 can answer weather queries
without invoking the LLM.

Voice query flow (via Module Bus):
  STT → "яка погода?" (lang: uk)
    → IntentRouter Tier 2 → bus.route_intent() matches "погода" pattern
    → intent message via WebSocket → handle_weather_intent()
    → {"handled": true, "tts_text": "Зараз у Києві +12°С, хмарно..."}
    → TTS speaks the answer

Running:
  python main.py
  (connects to core bus via SELENA_BUS_URL env var)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

# Resolve SelenaCore root so SDK is importable both in Docker (/app) and locally
_MODULE_DIR = Path(__file__).parent
_PROJECT_ROOT = _MODULE_DIR.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from sdk.base_module import SmartHomeModule, intent, on_event, scheduled
from weather_client import WeatherClient, WeatherData, get_default_city

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("weather-module")

CONFIG_PATH = Path(os.environ.get("WEATHER_CONFIG_PATH", "/config/weather.json"))


# ── Config ──────────────────────────────────────────────────────────────────

class WeatherConfig:
    def __init__(
        self,
        city: str = "Kyiv",
        country: str = "UA",
        lat: float = 50.4501,
        lon: float = 30.5234,
        units: str = "celsius",
        wind_unit: str = "ms",
        lang: str = "uk",
    ) -> None:
        self.city = city
        self.country = country
        self.lat = lat
        self.lon = lon
        self.units = units
        self.wind_unit = wind_unit
        self.lang = lang

    def to_dict(self) -> dict[str, Any]:
        return {
            "city": self.city,
            "country": self.country,
            "lat": self.lat,
            "lon": self.lon,
            "units": self.units,
            "wind_unit": self.wind_unit,
            "lang": self.lang,
        }


def _load_config() -> WeatherConfig:
    if CONFIG_PATH.exists():
        try:
            return WeatherConfig(**json.loads(CONFIG_PATH.read_text()))
        except Exception as exc:
            logger.warning("Config load failed, using defaults: %s", exc)
    city_info = get_default_city()
    return WeatherConfig(
        city=city_info["name"],
        country=city_info["country"],
        lat=city_info["lat"],
        lon=city_info["lon"],
    )


def _save_config(cfg: WeatherConfig) -> None:
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(cfg.to_dict(), indent=2))
    except Exception as exc:
        logger.warning("Config save failed: %s", exc)


# ── Module class ─────────────────────────────────────────────────────────────

class WeatherModule(SmartHomeModule):
    name = "weather-module"
    version = "1.0.0"

    def __init__(self) -> None:
        super().__init__()
        self._client = WeatherClient()
        self._config = _load_config()

    async def on_start(self) -> None:
        logger.info("Weather module started. Default city: %s", self._config.city)
        await self.publish_event("weather.module_started", {
            "city": self._config.city,
            "lat": self._config.lat,
            "lon": self._config.lon,
        })

    @intent(r"погода|прогноз|дощ|сніг|температур|яка погода|яке небо"
            r"|weather|forecast|rain|snow|temperatur"
            r"|what.*weather|how.*weather|is it raining|is it snowing")
    async def handle_weather_intent(
        self, text: str, context: dict[str, Any]
    ) -> dict[str, Any]:
        """Answer voice weather queries in Ukrainian or English."""
        lang = context.get("_lang", self._config.lang)
        cfg = self._config
        try:
            w: WeatherData = await self._client.get_weather(
                cfg.lat, cfg.lon, cfg.city, cfg.country, cfg.units
            )
        except Exception as exc:
            logger.error("Intent: weather fetch failed: %s", exc)
            return {"tts_text": self.t("fetch_error", lang=lang)}

        unit = "°C" if cfg.units == "celsius" else "°F"
        sign = "+" if w.temperature > 0 else ""
        fl_sign = "+" if w.feels_like > 0 else ""
        condition = w.condition_uk if lang == "uk" else w.condition_en

        tts = self.t(
            "current_weather", lang=lang,
            city=w.city, emoji=w.emoji,
            sign=sign, temp=w.temperature, unit=unit,
            condition=condition.lower(),
            fl_sign=fl_sign, feels_like=w.feels_like,
            humidity=w.humidity, wind=w.wind_speed,
        )

        return {
            "tts_text": tts,
            "data": {
                "city": w.city,
                "temperature": w.temperature,
                "emoji": w.emoji,
                "condition": w.condition_uk if lang == "uk" else w.condition_en,
                "humidity": w.humidity,
                "wind_speed": w.wind_speed,
            },
        }

    @scheduled("every:10m")
    async def _refresh_cache(self) -> None:
        """Invalidate weather cache so next request fetches fresh data."""
        self._client.invalidate_cache()
        logger.debug("Weather cache invalidated")


# ── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    module = WeatherModule()
    asyncio.run(module.start())
