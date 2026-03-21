"""
main.py — SelenaCore Weather Module

Provides current weather and 10-day forecast via Open-Meteo (free, no API key).
Declares voice intents so IntentRouter Tier 2 can answer weather queries
without invoking the LLM.

Voice query flow:
  STT → "яка погода?" (lang: uk)
    → IntentRouter Tier 2 matches "погода" pattern
    → POST localhost:8100/api/intent {"text": "яка погода?", "lang": "uk"}
    → {"handled": true, "tts_text": "Зараз у Києві +12°С, хмарно..."}
    → TTS speaks the answer

Running:
  uvicorn main:app --host 0.0.0.0 --port 8100
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

# Resolve SelenaCore root so SDK is importable both in Docker (/app) and locally
_MODULE_DIR = Path(__file__).parent
_PROJECT_ROOT = _MODULE_DIR.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from sdk.base_module import SmartHomeModule, intent, scheduled
from weather_client import WeatherClient, WeatherData, get_default_city

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("weather-module")

CONFIG_PATH = Path(os.environ.get("WEATHER_CONFIG_PATH", "/config/weather.json"))


# ── Config model ─────────────────────────────────────────────────────────────

class WeatherConfig(BaseModel):
    city: str = "Kyiv"
    country: str = "UA"
    lat: float = 50.4501
    lon: float = 30.5234
    units: str = "celsius"    # "celsius" | "fahrenheit"
    wind_unit: str = "ms"     # "ms" | "kmh" | "mph"
    lang: str = "uk"          # "uk" | "en"


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
        CONFIG_PATH.write_text(cfg.model_dump_json(indent=2))
    except Exception as exc:
        logger.warning("Config save failed: %s", exc)


# ── Module class ──────────────────────────────────────────────────────────────

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
            return {
                "tts_text": (
                    "Не вдалося отримати дані про погоду."
                    if lang == "uk"
                    else "Could not fetch weather data."
                )
            }

        unit = "°C" if cfg.units == "celsius" else "°F"
        sign = "+" if w.temperature > 0 else ""
        fl_sign = "+" if w.feels_like > 0 else ""

        if lang == "uk":
            tts = (
                f"Зараз у {w.city}: {w.emoji} {sign}{w.temperature}{unit}, "
                f"{w.condition_uk.lower()}. "
                f"Відчувається як {fl_sign}{w.feels_like}{unit}. "
                f"Вологість {w.humidity}%, вітер {w.wind_speed}\u00a0м/с."
            )
        else:
            tts = (
                f"Currently in {w.city}: {w.emoji} {sign}{w.temperature}{unit}, "
                f"{w.condition_en.lower()}. "
                f"Feels like {fl_sign}{w.feels_like}{unit}. "
                f"Humidity {w.humidity}%, wind {w.wind_speed}\u00a0m/s."
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


# ── FastAPI app ───────────────────────────────────────────────────────────────

app = FastAPI(title="Weather Module", version="1.0.0")
module = WeatherModule()
# Register /api/intent before startup so it is available in tests and early requests
module.setup_intent_routes(app)


@app.on_event("startup")
async def _startup() -> None:
    await module.start()


@app.on_event("shutdown")
async def _shutdown() -> None:
    await module.stop()


# ── Static UI endpoints (required by SelenaCore module protocol) ──────────────

@app.get("/health")
def health() -> dict:
    return {"status": "ok", "name": module.name, "version": module.version}


@app.get("/widget.html", response_class=HTMLResponse)
def widget_html() -> HTMLResponse:
    return HTMLResponse(content=(_MODULE_DIR / "widget.html").read_text(encoding="utf-8"))


@app.get("/settings.html", response_class=HTMLResponse)
def settings_html() -> HTMLResponse:
    return HTMLResponse(content=(_MODULE_DIR / "settings.html").read_text(encoding="utf-8"))


@app.get("/icon.svg")
def icon_svg() -> FileResponse:
    return FileResponse(_MODULE_DIR / "icon.svg", media_type="image/svg+xml")


# ── Configuration API ─────────────────────────────────────────────────────────

@app.get("/api/config")
def get_config() -> dict:
    return module._config.model_dump()


class ConfigUpdate(BaseModel):
    city: str | None = None
    country: str | None = None
    lat: float | None = None
    lon: float | None = None
    units: str | None = None
    wind_unit: str | None = None
    lang: str | None = None


@app.post("/api/config")
def update_config(body: ConfigUpdate) -> dict:
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    updated = module._config.model_copy(update=updates)
    module._config = updated
    _save_config(updated)
    module._client.invalidate_cache()
    logger.info("Config updated: city=%s units=%s lang=%s", updated.city, updated.units, updated.lang)
    return updated.model_dump()


# ── Weather data API ──────────────────────────────────────────────────────────

@app.get("/api/weather")
async def get_weather() -> dict:
    cfg = module._config
    try:
        w = await module._client.get_weather(cfg.lat, cfg.lon, cfg.city, cfg.country, cfg.units)
        return {
            "city": w.city,
            "country": w.country,
            "temperature": w.temperature,
            "feels_like": w.feels_like,
            "humidity": w.humidity,
            "wind_speed": w.wind_speed,
            "wind_direction": w.wind_direction,
            "wmo_code": w.wmo_code,
            "emoji": w.emoji,
            "condition_en": w.condition_en,
            "condition_uk": w.condition_uk,
            "is_day": w.is_day,
            "units": w.units,
            "updated_at": w.updated_at,
        }
    except Exception as exc:
        logger.error("Weather API error: %s", exc)
        raise HTTPException(status_code=503, detail="Weather service temporarily unavailable")


@app.get("/api/forecast")
async def get_forecast() -> dict:
    cfg = module._config
    try:
        f = await module._client.get_forecast(cfg.lat, cfg.lon, cfg.units)
        return {
            "hourly_today": [
                {
                    "time": h.time,
                    "temperature": h.temperature,
                    "precip_prob": h.precip_prob,
                    "wind_speed": h.wind_speed,
                    "wmo_code": h.wmo_code,
                    "emoji": h.emoji,
                }
                for h in f.hourly_today
            ],
            "daily_7": [
                {
                    "date": d.date,
                    "temp_min": d.temp_min,
                    "temp_max": d.temp_max,
                    "precip_prob": d.precip_prob,
                    "wmo_code": d.wmo_code,
                    "emoji": d.emoji,
                    "condition_en": d.condition_en,
                    "condition_uk": d.condition_uk,
                }
                for d in f.daily_7
            ],
            "daily_10": [
                {
                    "date": d.date,
                    "temp_min": d.temp_min,
                    "temp_max": d.temp_max,
                    "precip_prob": d.precip_prob,
                    "wmo_code": d.wmo_code,
                    "emoji": d.emoji,
                    "condition_en": d.condition_en,
                    "condition_uk": d.condition_uk,
                }
                for d in f.daily_10
            ],
            "units": cfg.units,
        }
    except Exception as exc:
        logger.error("Forecast API error: %s", exc)
        raise HTTPException(status_code=503, detail="Forecast service temporarily unavailable")


@app.get("/api/search")
async def search_city(q: str = Query(..., min_length=2)) -> dict:
    try:
        results = await module._client.search_city(q)
        return {"results": results}
    except Exception as exc:
        logger.error("City search error: %s", exc)
        raise HTTPException(status_code=503, detail="Geocoding service temporarily unavailable")
