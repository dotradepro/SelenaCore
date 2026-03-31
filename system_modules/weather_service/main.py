"""system_modules/weather_service/main.py — FastAPI entry point."""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from system_modules.weather_service.weather import WeatherService

logger = logging.getLogger(__name__)

# ── Core API client ───────────────────────────────────────────────────────────

CORE_URL = os.getenv("CORE_API_URL", "http://localhost:7070")
MODULE_TOKEN = os.getenv("MODULE_TOKEN", "weather-service-token")


async def _publish(event_type: str, payload: dict[str, Any]) -> None:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                f"{CORE_URL}/api/v1/events/publish",
                headers={"Authorization": f"Bearer {MODULE_TOKEN}"},
                json={"type": event_type, "source": "weather-service", "payload": payload},
            )
    except Exception as exc:
        logger.warning("Failed to publish %s: %s", event_type, exc)


# ── App state ─────────────────────────────────────────────────────────────────

_weather: WeatherService | None = None

# ── Lifespan ──────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _weather
    lat = float(os.getenv("WEATHER_LAT", "50.4501"))
    lon = float(os.getenv("WEATHER_LON", "30.5234"))
    interval = int(os.getenv("WEATHER_INTERVAL", "1800"))
    units = os.getenv("WEATHER_UNITS", "metric")

    _weather = WeatherService(
        publish_event_cb=_publish,
        latitude=lat,
        longitude=lon,
        update_interval_sec=interval,
        units=units,
    )
    await _weather.start()
    yield
    await _weather.stop()


app = FastAPI(title="WeatherService", lifespan=lifespan)

# ── Request models ────────────────────────────────────────────────────────────


class ConfigRequest(BaseModel):
    latitude: float | None = None
    longitude: float | None = None
    update_interval_sec: int | None = Field(None, ge=60)
    units: str | None = None
    alert_rain_mm: float | None = None
    alert_wind_kmh: float | None = None


# ── Routes ────────────────────────────────────────────────────────────────────


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "module": "weather-service"}


@app.get("/weather/current")
async def get_current() -> JSONResponse:
    if _weather is None:
        raise HTTPException(status_code=503, detail="Service not ready")
    current = _weather.get_current()
    if current is None:
        raise HTTPException(status_code=503, detail="No weather data yet")
    return JSONResponse(current)


@app.get("/weather/forecast")
async def get_forecast() -> JSONResponse:
    if _weather is None:
        raise HTTPException(status_code=503, detail="Service not ready")
    return JSONResponse({"forecast": _weather.get_forecast()})


@app.get("/weather/status")
async def get_status() -> JSONResponse:
    if _weather is None:
        raise HTTPException(status_code=503, detail="Service not ready")
    return JSONResponse(_weather.get_status())


@app.post("/weather/refresh")
async def refresh() -> JSONResponse:
    """Trigger an immediate weather fetch."""
    if _weather is None:
        raise HTTPException(status_code=503, detail="Service not ready")
    try:
        data = await _weather.fetch()
        return JSONResponse({"ok": True, "data": data})
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Fetch failed: {exc}") from exc


@app.post("/weather/config")
async def configure(req: ConfigRequest) -> JSONResponse:
    if _weather is None:
        raise HTTPException(status_code=503, detail="Service not ready")
    _weather.configure(
        latitude=req.latitude,
        longitude=req.longitude,
        update_interval_sec=req.update_interval_sec,
        units=req.units,
        alert_rain_mm=req.alert_rain_mm,
        alert_wind_kmh=req.alert_wind_kmh,
    )
    return JSONResponse({"ok": True, "status": _weather.get_status()})


@app.get("/widget.html", response_class=HTMLResponse)
async def widget() -> HTMLResponse:
    import importlib.resources
    path = importlib.resources.files("system_modules.weather_service").joinpath("widget.html")
    return HTMLResponse(path.read_text())


@app.get("/settings.html", response_class=HTMLResponse)
async def settings() -> HTMLResponse:
    import importlib.resources
    path = importlib.resources.files("system_modules.weather_service").joinpath("settings.html")
    return HTMLResponse(path.read_text())



# System module — loaded in-process by SelenaCore via importlib.
# No standalone entry point needed.
