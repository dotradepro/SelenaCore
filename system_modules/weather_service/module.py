"""
system_modules/weather_service/module.py — In-process SystemModule wrapper.

Runs inside the core process via importlib — NOT a separate uvicorn subprocess.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from core.module_loader.system_module import SystemModule
from system_modules.weather_service.weather import WeatherService
from system_modules.weather_service.voice_handler import WeatherVoiceHandler

logger = logging.getLogger(__name__)


class ConfigRequest(BaseModel):
    latitude: float | None = None
    longitude: float | None = None
    location_name: str | None = None
    update_interval_sec: int | None = Field(None, ge=60)
    units: str | None = None
    alert_rain_mm: float | None = None
    alert_wind_kmh: float | None = None


class WeatherServiceModule(SystemModule):
    name = "weather-service"

    def __init__(self) -> None:
        super().__init__()
        self._weather: WeatherService | None = None
        self._voice: WeatherVoiceHandler = WeatherVoiceHandler(self)

    async def start(self) -> None:
        lat = float(os.getenv("WEATHER_LAT", "50.4501"))
        lon = float(os.getenv("WEATHER_LON", "30.5234"))
        interval = int(os.getenv("WEATHER_INTERVAL", "1800"))
        units = os.getenv("WEATHER_UNITS", "metric")

        self._weather = WeatherService(
            publish_event_cb=self.publish,
            latitude=lat,
            longitude=lon,
            update_interval_sec=interval,
            units=units,
        )
        await self._weather.start()

        # Subscribe to EventBus for voice intents
        self.subscribe(
            ["voice.intent"],
            self._on_event,
        )

        await self.publish("module.started", {"name": self.name})

    async def stop(self) -> None:
        if self._weather:
            await self._weather.stop()
        self._cleanup_subscriptions()
        await self.publish("module.stopped", {"name": self.name})

    # ── EventBus handler ──────────────────────────────────────────────────────

    async def _on_event(self, event) -> None:
        etype = event.type
        payload = event.payload

        if etype == "voice.intent":
            intent = payload.get("intent", "")
            if intent.startswith("weather."):
                ctx = await self._voice.handle(intent, payload.get("params", {}))
                if ctx:
                    await self.speak_action(intent, ctx)

    # ── Helpers ───────────────────────────────────────────────────────────────

    # speak() is inherited from SystemModule — blocking, waits for TTS to finish

    def get_router(self) -> APIRouter:
        router = APIRouter()
        svc = self

        svc._register_health_endpoint(router)

        @router.get("/weather/current")
        async def get_current() -> JSONResponse:
            if svc._weather is None:
                raise HTTPException(503, "Service not ready")
            current = svc._weather.get_current()
            if current is None:
                raise HTTPException(503, "No weather data yet")
            return JSONResponse(current)

        @router.get("/weather/forecast")
        async def get_forecast() -> JSONResponse:
            if svc._weather is None:
                raise HTTPException(503, "Service not ready")
            return JSONResponse({"forecast": svc._weather.get_forecast()})

        @router.get("/weather/status")
        async def get_status() -> JSONResponse:
            if svc._weather is None:
                raise HTTPException(503, "Service not ready")
            return JSONResponse(svc._weather.get_status())

        @router.post("/weather/refresh")
        async def refresh() -> JSONResponse:
            if svc._weather is None:
                raise HTTPException(503, "Service not ready")
            try:
                data = await svc._weather.fetch()
                return JSONResponse({"ok": True, "data": data})
            except Exception as exc:
                raise HTTPException(502, f"Fetch failed: {exc}") from exc

        @router.post("/weather/config")
        async def configure(req: ConfigRequest) -> JSONResponse:
            if svc._weather is None:
                raise HTTPException(503, "Service not ready")
            svc._weather.configure(
                latitude=req.latitude,
                longitude=req.longitude,
                location_name=req.location_name,
                update_interval_sec=req.update_interval_sec,
                units=req.units,
                alert_rain_mm=req.alert_rain_mm,
                alert_wind_kmh=req.alert_wind_kmh,
            )
            return JSONResponse({"ok": True, "status": svc._weather.get_status()})

        @router.get("/weather/geocode")
        async def geocode(q: str = "") -> JSONResponse:
            """Search for a location using Open-Meteo Geocoding API."""
            if not q or len(q) < 2:
                return JSONResponse({"results": []})
            import httpx as _httpx
            try:
                async with _httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.get(
                        "https://geocoding-api.open-meteo.com/v1/search",
                        params={"name": q, "count": 8, "language": "en", "format": "json"},
                    )
                    resp.raise_for_status()
                    data = resp.json()
                results = []
                for r in data.get("results", []):
                    results.append({
                        "name": r.get("name", ""),
                        "country": r.get("country", ""),
                        "admin1": r.get("admin1", ""),
                        "latitude": r.get("latitude"),
                        "longitude": r.get("longitude"),
                    })
                return JSONResponse({"results": results})
            except Exception as exc:
                logger.warning("Geocoding search failed: %s", exc)
                return JSONResponse({"results": [], "error": str(exc)})

        svc._register_html_routes(router, __file__)
        return router
