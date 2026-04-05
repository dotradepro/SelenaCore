"""
core/api/routes/modules.py — Module Loader API endpoints + SSE status stream
"""
from __future__ import annotations

import asyncio
import json
import logging
import tempfile
from pathlib import Path
from typing import Any, AsyncGenerator

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from core.api.auth import verify_module_token
from core.eventbus.bus import get_event_bus
from core.eventbus.types import MODULE_INSTALLED, MODULE_REMOVED, MODULE_STARTED, MODULE_STOPPED
from core.module_loader.sandbox import ModuleInfo, ModuleStatus, get_sandbox
from core.module_loader.validator import validate_zip

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/modules", tags=["modules"])

# SSE status queues per module name
_status_queues: dict[str, asyncio.Queue[dict]] = {}


def _emit_status(name: str, status: str, message: str) -> None:
    """Send status update to all SSE subscribers for this module."""
    q = _status_queues.get(name)
    if q:
        q.put_nowait({"status": status, "message": message})


class ModuleResponse(BaseModel):
    name: str
    version: str
    type: str
    status: str
    runtime_mode: str
    port: int  # Deprecated: always 0 — modules use WebSocket bus
    installed_at: float
    ui: dict | None = None   # from manifest.json "ui" section (widget, settings, icon)


class ModuleListResponse(BaseModel):
    modules: list[ModuleResponse]


def _to_response(info: ModuleInfo) -> ModuleResponse:
    return ModuleResponse(
        name=info.name,
        version=info.version,
        type=info.type,
        status=info.status.value,
        runtime_mode=info.runtime_mode,
        port=info.port,
        installed_at=info.installed_at,
        ui=info.manifest.get("ui") if info.manifest else None,
    )


@router.get("", response_model=ModuleListResponse)
async def list_modules(
    _token: str = Depends(verify_module_token),
) -> ModuleListResponse:
    sandbox = get_sandbox()
    return ModuleListResponse(modules=[_to_response(m) for m in sandbox.list_modules()])


@router.post("/install", status_code=201)
async def install_module(
    module: UploadFile = File(..., description="Module ZIP archive"),
    _token: str = Depends(verify_module_token),
) -> dict[str, Any]:
    # Save upload to temp file
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        content = await module.read()
        tmp.write(content)
        tmp_path = Path(tmp.name)

    try:
        # Validate
        result = validate_zip(tmp_path)
        if not result.valid:
            raise HTTPException(
                status_code=422,
                detail={"errors": result.errors},
            )

        manifest = result.manifest
        name = manifest["name"]

        # Init SSE queue
        _status_queues[name] = asyncio.Queue()
        _emit_status(name, ModuleStatus.VALIDATING, "Manifest validated, installing...")

        # Install and start
        sandbox = get_sandbox()
        asyncio.create_task(_install_and_notify(sandbox, tmp_path, manifest, name))

        return {
            "name": name,
            "status": ModuleStatus.VALIDATING,
            "message": "Module uploaded, validation in progress",
        }
    except HTTPException:
        tmp_path.unlink(missing_ok=True)
        raise
    except Exception as e:
        tmp_path.unlink(missing_ok=True)
        logger.error("Module install error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


async def _install_and_notify(sandbox, zip_path: Path, manifest: dict, name: str) -> None:
    try:
        info = await sandbox.install(zip_path, manifest)
        _emit_status(name, ModuleStatus.READY, "Validation passed, starting...")
        _emit_status(name, info.status, "Module started (bus-connected)")
        bus = get_event_bus()
        await bus.publish(
            type=MODULE_INSTALLED,
            source="core.module_loader",
            payload={"name": name, "port": info.port},
        )
    except Exception as e:
        _emit_status(name, ModuleStatus.ERROR, str(e))
    finally:
        zip_path.unlink(missing_ok=True)


@router.get("/{name}/status/stream")
async def module_status_stream(
    name: str,
    _token: str = Depends(verify_module_token),
) -> StreamingResponse:
    if name not in _status_queues:
        _status_queues[name] = asyncio.Queue()

    async def event_generator() -> AsyncGenerator[str, None]:
        q = _status_queues[name]
        try:
            while True:
                try:
                    update = await asyncio.wait_for(q.get(), timeout=30.0)
                    yield f"data: {json.dumps(update)}\n\n"
                    if update.get("status") in (
                        ModuleStatus.RUNNING,
                        ModuleStatus.ERROR,
                        ModuleStatus.STOPPED,
                    ):
                        break
                except asyncio.TimeoutError:
                    yield "data: {\"heartbeat\": true}\n\n"
        finally:
            _status_queues.pop(name, None)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/{name}/stop")
async def stop_module(
    name: str,
    _token: str = Depends(verify_module_token),
) -> dict[str, str]:
    sandbox = get_sandbox()
    info = sandbox.get_module(name)
    if info is None:
        raise HTTPException(status_code=404, detail="Module not found")
    if info.type == "SYSTEM":
        raise HTTPException(status_code=403, detail="Cannot stop SYSTEM modules")

    await sandbox.stop(name)
    bus = get_event_bus()
    await bus.publish(
        type=MODULE_STOPPED, source="core.module_loader", payload={"name": name}
    )
    return {"name": name, "status": ModuleStatus.STOPPED}


@router.post("/{name}/start")
async def start_module(
    name: str,
    _token: str = Depends(verify_module_token),
) -> dict[str, str]:
    sandbox = get_sandbox()
    info = sandbox.get_module(name)
    if info is None:
        raise HTTPException(status_code=404, detail="Module not found")

    await sandbox.start(name)
    bus = get_event_bus()
    await bus.publish(
        type=MODULE_STARTED, source="core.module_loader", payload={"name": name}
    )
    return {"name": name, "status": ModuleStatus.RUNNING}


@router.delete("/{name}")
async def remove_module(
    name: str,
    _token: str = Depends(verify_module_token),
) -> Response:
    sandbox = get_sandbox()
    info = sandbox.get_module(name)
    if info is None:
        raise HTTPException(status_code=404, detail="Module not found")
    if info.type == "SYSTEM":
        raise HTTPException(status_code=403, detail="Cannot remove SYSTEM modules")

    await sandbox.remove(name)
    bus = get_event_bus()
    await bus.publish(
        type=MODULE_REMOVED, source="core.module_loader", payload={"name": name}
    )
    return Response(status_code=204)
