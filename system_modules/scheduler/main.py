"""
system_modules/scheduler/main.py — точка входа FastAPI модуля scheduler [#69]
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from system_modules.scheduler.scheduler import SchedulerService

logger = logging.getLogger(__name__)

MODULE_NAME = "scheduler"
CORE_API = os.environ.get("SELENA_CORE_API", "http://localhost:7070/api/v1")
MODULE_TOKEN = os.environ.get("MODULE_TOKEN", "")
DATA_DIR = Path(os.environ.get("SELENA_DATA_DIR", "/var/lib/selena")) / "modules" / MODULE_NAME

_config: dict = {
    "latitude": None,
    "longitude": None,
    "timezone": "UTC",
}
_service: SchedulerService | None = None


# ── Pydantic models ──────────────────────────────────────────────────────────

class RegisterJobRequest(BaseModel):
    job_id: str
    trigger: str
    event_type: str
    payload: dict = {}
    owner: str = "unknown"


class ConfigUpdateRequest(BaseModel):
    latitude: float | None = None
    longitude: float | None = None
    timezone: str = "UTC"


# ── Core API helpers ─────────────────────────────────────────────────────────

async def _publish_event(event_type: str, payload: dict) -> None:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                f"{CORE_API}/events/publish",
                headers={"Authorization": f"Bearer {MODULE_TOKEN}"},
                json={"type": event_type, "source": MODULE_NAME, "payload": payload},
            )
    except Exception as exc:
        logger.error(f"Failed to publish event {event_type}: {exc}")


async def _subscribe_events() -> None:
    """Subscribe to scheduler.register / scheduler.unregister events."""
    webhook_url = f"http://localhost:8111/webhook/events"
    event_types = [
        "scheduler.register",
        "scheduler.unregister",
        "scheduler.list_jobs",
    ]
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(
                f"{CORE_API}/events/subscribe",
                headers={"Authorization": f"Bearer {MODULE_TOKEN}"},
                json={"event_types": event_types, "webhook_url": webhook_url},
            )
        logger.info(f"Subscribed to events: {event_types}")
    except Exception as exc:
        logger.warning(f"Event subscription failed (will retry on next event): {exc}")


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _service
    _service = SchedulerService(publish_callback=_publish_event, config=_config)
    await _service.start()
    await _service.load_jobs(DATA_DIR)
    await _subscribe_events()
    await _publish_event("module.started", {"name": MODULE_NAME})
    yield
    if _service:
        await _service.save_jobs(DATA_DIR)
        await _service.stop()
    await _publish_event("module.stopped", {"name": MODULE_NAME})


app = FastAPI(title="SelenaCore Scheduler", version="0.1.0", lifespan=lifespan)

# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "module": MODULE_NAME}


@app.get("/jobs")
async def list_jobs() -> dict:
    if _service is None:
        raise HTTPException(status_code=503, detail="Scheduler not running")
    return {"jobs": _service.list_jobs()}


@app.post("/jobs", status_code=201)
async def register_job(req: RegisterJobRequest) -> dict:
    if _service is None:
        raise HTTPException(status_code=503, detail="Scheduler not running")
    result = await _service.register_job(req.model_dump())
    if result is None:
        raise HTTPException(status_code=400, detail="Failed to register job — check trigger format")
    await _service.save_jobs(DATA_DIR)
    return result


@app.delete("/jobs/{job_id}", status_code=200)
async def remove_job(job_id: str) -> dict:
    if _service is None:
        raise HTTPException(status_code=503, detail="Scheduler not running")
    removed = await _service.remove_job(job_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Job not found")
    await _service.save_jobs(DATA_DIR)
    return {"removed": job_id}


@app.get("/config")
async def get_config() -> dict:
    return _config


@app.post("/config")
async def update_config(req: ConfigUpdateRequest) -> dict:
    _config.update(req.model_dump(exclude_none=True))
    if _service:
        _service.update_config(_config)
    return _config


# ── Webhook endpoint for Core API event delivery ─────────────────────────────

@app.post("/webhook/events")
async def webhook_events(payload: dict) -> dict:
    event_type = payload.get("type", "")
    data = payload.get("payload", {})

    if _service is None:
        return {"status": "error", "detail": "not running"}

    if event_type == "scheduler.register":
        result = await _service.register_job(data)
        if result:
            await _service.save_jobs(DATA_DIR)
        return {"status": "ok" if result else "error"}

    if event_type == "scheduler.unregister":
        job_id = data.get("job_id")
        if job_id:
            await _service.remove_job(job_id)
            await _service.save_jobs(DATA_DIR)
        return {"status": "ok"}

    if event_type == "scheduler.list_jobs":
        jobs = _service.list_jobs()
        await _publish_event("scheduler.jobs_list", {"jobs": jobs})
        return {"status": "ok"}

    return {"status": "ignored"}


# ── Settings UI ───────────────────────────────────────────────────────────────

@app.get("/settings", response_class=HTMLResponse)
async def settings_page() -> str:
    settings_file = Path(__file__).parent / "settings.html"
    if settings_file.exists():
        return settings_file.read_text()
    return "<html><body><p>settings.html not found</p></body></html>"
