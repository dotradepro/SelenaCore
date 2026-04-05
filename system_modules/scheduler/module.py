"""
system_modules/scheduler/module.py — In-process SystemModule wrapper.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from core.module_loader.system_module import SystemModule
from system_modules.scheduler.scheduler import SchedulerService

logger = logging.getLogger(__name__)

SELENA_DATA_DIR = os.environ.get("CORE_DATA_DIR", "/var/lib/selena")


class RegisterJobRequest(BaseModel):
    job_id: str
    trigger: str
    event_type: str
    payload: dict = {}
    owner: str = ""


class ConfigUpdateRequest(BaseModel):
    latitude: float | None = None
    longitude: float | None = None
    timezone: str | None = None


class SchedulerModule(SystemModule):
    name = "scheduler"

    def __init__(self) -> None:
        super().__init__()
        self._service: SchedulerService | None = None
        self._config: dict = {
            "latitude": None,
            "longitude": None,
            "timezone": "UTC",
        }
        self._data_dir = Path(SELENA_DATA_DIR) / "modules" / "scheduler"

    async def _on_scheduler_event(self, event) -> None:
        if not self._service:
            return
        etype = event.type
        payload = event.payload
        if etype == "scheduler.register":
            await self._service.register_job(payload)
            await self._service.save_jobs(self._data_dir)
        elif etype == "scheduler.unregister":
            await self._service.remove_job(payload.get("job_id", ""))
            await self._service.save_jobs(self._data_dir)
        elif etype == "scheduler.list_jobs":
            pass  # informational only

    async def start(self) -> None:
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._service = SchedulerService(
            publish_callback=self.publish,
            config=self._config,
        )
        await self._service.start()
        await self._service.load_jobs(self._data_dir)
        self.subscribe(
            ["scheduler.register", "scheduler.unregister", "scheduler.list_jobs"],
            self._on_scheduler_event,
        )
        await self.publish("module.started", {"name": self.name})

    async def stop(self) -> None:
        if self._service:
            await self._service.save_jobs(self._data_dir)
            await self._service.stop()
        self._cleanup_subscriptions()
        await self.publish("module.stopped", {"name": self.name})

    def get_router(self) -> APIRouter:
        router = APIRouter()
        svc = self

        svc._register_health_endpoint(router)

        @router.get("/jobs")
        async def list_jobs() -> dict:
            if svc._service is None:
                raise HTTPException(503, "Not running")
            return {"jobs": svc._service.list_jobs()}

        @router.post("/jobs", status_code=201)
        async def create_job(req: RegisterJobRequest) -> dict:
            if svc._service is None:
                raise HTTPException(503, "Not running")
            job = await svc._service.register_job(req.model_dump())
            await svc._service.save_jobs(svc._data_dir)
            return job

        @router.delete("/jobs/{job_id}", status_code=200)
        async def remove_job(job_id: str) -> dict:
            if svc._service is None:
                raise HTTPException(503, "Not running")
            await svc._service.remove_job(job_id)
            await svc._service.save_jobs(svc._data_dir)
            return {"removed": job_id}

        @router.get("/config")
        async def get_config() -> dict:
            return svc._config

        @router.post("/config")
        async def update_config(req: ConfigUpdateRequest) -> dict:
            for k, v in req.model_dump(exclude_none=True).items():
                svc._config[k] = v
            return svc._config

        svc._register_html_routes(router, __file__)
        return router
