"""
core/api/routes/devices.py — Device Registry CRUD endpoints
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from core.api.auth import verify_module_token
from core.eventbus.bus import get_event_bus
from core.eventbus.types import DEVICE_REGISTERED, DEVICE_REMOVED, DEVICE_STATE_CHANGED
from core.registry.models import Device
from core.registry.service import DeviceNotFoundError, DeviceRegistry

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/devices", tags=["devices"])

VALID_DEVICE_TYPES = {"sensor", "actuator", "controller", "virtual"}


# --- Pydantic schemas ---

class DeviceCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    type: str = Field(..., pattern=r"^(sensor|actuator|controller|virtual)$")
    protocol: str = Field(..., min_length=1, max_length=50)
    capabilities: list[str] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)


class StateUpdate(BaseModel):
    state: dict[str, Any]


class DeviceResponse(BaseModel):
    device_id: str
    name: str
    type: str
    protocol: str
    state: dict[str, Any]
    capabilities: list[str]
    last_seen: float | None
    module_id: str | None
    meta: dict[str, Any]

    @classmethod
    def from_orm(cls, device: Device) -> "DeviceResponse":
        return cls(
            device_id=device.device_id,
            name=device.name,
            type=device.type,
            protocol=device.protocol,
            state=device.get_state(),
            capabilities=device.get_capabilities(),
            last_seen=device.last_seen.timestamp() if device.last_seen else None,
            module_id=device.module_id,
            meta=device.get_meta(),
        )


class DeviceListResponse(BaseModel):
    devices: list[DeviceResponse]


# --- Dependency helpers ---

async def get_db_session(request) -> AsyncSession:
    """Get database session from app state."""
    from sqlalchemy.ext.asyncio import async_sessionmaker
    factory: async_sessionmaker = request.app.state.db_session_factory
    async with factory() as session:
        yield session


async def get_registry(
    request,
    session: AsyncSession = Depends(get_db_session),
) -> DeviceRegistry:
    return DeviceRegistry(session)


# --- Endpoints ---

@router.get("", response_model=DeviceListResponse)
async def list_devices(
    registry: DeviceRegistry = Depends(get_registry),
    _token: str = Depends(verify_module_token),
) -> DeviceListResponse:
    devices = await registry.get_all()
    return DeviceListResponse(devices=[DeviceResponse.from_orm(d) for d in devices])


@router.post("", response_model=DeviceResponse, status_code=201)
async def create_device(
    body: DeviceCreate,
    request,
    _token: str = Depends(verify_module_token),
) -> DeviceResponse:
    from sqlalchemy.ext.asyncio import async_sessionmaker
    factory: async_sessionmaker = request.app.state.db_session_factory
    async with factory() as session:
        async with session.begin():
            registry = DeviceRegistry(session)
            device = await registry.create(
                name=body.name,
                type=body.type,
                protocol=body.protocol,
                capabilities=body.capabilities,
                meta=body.meta,
            )
        bus = get_event_bus()
        await bus.publish(
            type=DEVICE_REGISTERED,
            source="core.registry",
            payload={"device_id": device.device_id, "name": device.name},
        )
        return DeviceResponse.from_orm(device)


@router.get("/{device_id}", response_model=DeviceResponse)
async def get_device(
    device_id: str,
    registry: DeviceRegistry = Depends(get_registry),
    _token: str = Depends(verify_module_token),
) -> DeviceResponse:
    device = await registry.get(device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")
    return DeviceResponse.from_orm(device)


@router.patch("/{device_id}/state", response_model=DeviceResponse)
async def update_device_state(
    device_id: str,
    body: StateUpdate,
    request,
    _token: str = Depends(verify_module_token),
) -> DeviceResponse:
    from sqlalchemy.ext.asyncio import async_sessionmaker
    factory: async_sessionmaker = request.app.state.db_session_factory
    async with factory() as session:
        async with session.begin():
            registry = DeviceRegistry(session)
            try:
                old_device = await registry.get(device_id)
                if old_device is None:
                    raise HTTPException(status_code=404, detail="Device not found")
                old_state = old_device.get_state()
                device = await registry.update_state(device_id, body.state)
            except DeviceNotFoundError:
                raise HTTPException(status_code=404, detail="Device not found")
        bus = get_event_bus()
        await bus.publish(
            type=DEVICE_STATE_CHANGED,
            source="core.registry",
            payload={
                "device_id": device_id,
                "old_state": old_state,
                "new_state": body.state,
            },
        )
        return DeviceResponse.from_orm(device)


@router.delete("/{device_id}")
async def delete_device(
    device_id: str,
    request,
    _token: str = Depends(verify_module_token),
) -> Response:
    from sqlalchemy.ext.asyncio import async_sessionmaker
    factory: async_sessionmaker = request.app.state.db_session_factory
    async with factory() as session:
        async with session.begin():
            registry = DeviceRegistry(session)
            try:
                await registry.delete(device_id)
            except DeviceNotFoundError:
                raise HTTPException(status_code=404, detail="Device not found")
    bus = get_event_bus()
    await bus.publish(
        type=DEVICE_REMOVED,
        source="core.registry",
        payload={"device_id": device_id},
    )
    return Response(status_code=204)
