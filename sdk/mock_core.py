"""
sdk/mock_core.py — Mock Core API server for local module development

Starts a FastAPI server that mimics the SelenaCore Core API endpoints
so modules can be developed and tested without a real SelenaCore instance.

Start: python -m sdk.mock_core   (listens on :7070)
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any

from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.responses import JSONResponse

app = FastAPI(title="SelenaCore Mock API", version="1.0.0-mock")

# In-memory store
_devices: dict[str, dict] = {}
_events: list[dict] = []
_modules: dict[str, dict] = {
    "ui_core": {"name": "ui_core", "type": "SYSTEM", "status": "RUNNING"},
    "voice_core": {"name": "voice_core", "type": "SYSTEM", "status": "RUNNING"},
}


def _check_auth(authorization: str | None) -> None:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid auth token")


# ---- Health ----

@app.get("/api/v1/health")
def health():
    return {"status": "ok", "mock": True, "timestamp": time.time()}


# ---- Devices ----

@app.get("/api/v1/devices")
def list_devices(authorization: str | None = Header(None)):
    _check_auth(authorization)
    return {"devices": list(_devices.values()), "total": len(_devices)}


@app.post("/api/v1/devices")
async def create_device(request: Request, authorization: str | None = Header(None)):
    _check_auth(authorization)
    data = await request.json()
    device_id = str(uuid.uuid4())[:8]
    device = {"id": device_id, "created_at": time.time(), **data}
    _devices[device_id] = device
    return device


@app.get("/api/v1/devices/{device_id}")
def get_device(device_id: str, authorization: str | None = Header(None)):
    _check_auth(authorization)
    dev = _devices.get(device_id)
    if not dev:
        raise HTTPException(status_code=404, detail="Device not found")
    return dev


@app.patch("/api/v1/devices/{device_id}")
async def update_device(device_id: str, request: Request, authorization: str | None = Header(None)):
    _check_auth(authorization)
    if device_id not in _devices:
        raise HTTPException(status_code=404, detail="Device not found")
    data = await request.json()
    _devices[device_id].update(data)
    return _devices[device_id]


# ---- Events ----

@app.post("/api/v1/events/publish")
async def publish_event(request: Request, authorization: str | None = Header(None)):
    _check_auth(authorization)
    data = await request.json()
    event = {"id": str(uuid.uuid4()), "timestamp": time.time(), **data}
    _events.append(event)
    print(f"[MockCore] Event: {data.get('event_type')} → {json.dumps(data.get('payload', {}))[:80]}")
    return {"id": event["id"], "status": "published"}


@app.get("/api/v1/events")
def list_events(authorization: str | None = Header(None), limit: int = 50):
    _check_auth(authorization)
    return {"events": _events[-limit:]}


# ---- Modules ----

@app.get("/api/v1/modules")
def list_modules(authorization: str | None = Header(None)):
    _check_auth(authorization)
    return {"modules": list(_modules.values())}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=7070)
