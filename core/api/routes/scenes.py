"""
core/api/routes/scenes.py — Scene CRUD + activation

Scenes are named sets of device actions.
Dual-language: name_user (original), name_en (auto-translated for LLM prompt).

Activation runs the scene's action list inline against the device registry and
publishes scene.activate / scene.activated / scene.failed for telemetry.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.api.auth import verify_module_token
from core.api.dependencies import get_db_session
from core.api.helpers import get_entity_patterns, on_entity_changed, translate_to_en
from core.eventbus.bus import get_event_bus
from core.eventbus.types import (
    DEVICE_STATE_CHANGED,
    SCENE_ACTIVATE,
    SCENE_ACTIVATED,
    SCENE_FAILED,
)
from core.registry.models import Scene
from core.registry.service import DeviceNotFoundError, DeviceRegistry

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/scenes", tags=["scenes"])


# ── Pydantic schemas ─────────────────────────────────────────────────────

class SceneCreate(BaseModel):
    name_user: str = Field(..., min_length=1, max_length=255)
    actions: list[dict[str, Any]] = Field(default_factory=list)
    trigger: str = ""
    enabled: bool = True


class SceneUpdate(BaseModel):
    name_user: str | None = Field(None, max_length=255)
    actions: list[dict[str, Any]] | None = None
    trigger: str | None = None
    enabled: bool | None = None


class SceneResponse(BaseModel):
    id: int
    name_user: str
    name_en: str
    actions: list[dict[str, Any]]
    trigger: str
    enabled: bool
    patterns_en: list[str] = []

    @classmethod
    def from_orm(cls, s: Scene, patterns: list[str] | None = None) -> "SceneResponse":
        return cls(
            id=s.id,
            name_user=s.name_user,
            name_en=s.name_en,
            actions=s.get_actions(),
            trigger=s.trigger,
            enabled=s.enabled,
            patterns_en=patterns or [],
        )


class SceneListResponse(BaseModel):
    scenes: list[SceneResponse]


# ── Endpoints ��────────────────────────────────��──────────────────────────

@router.get("", response_model=SceneListResponse)
async def list_scenes(
    enabled_only: bool = False,
    session: AsyncSession = Depends(get_db_session),
    _token: str = Depends(verify_module_token),
) -> SceneListResponse:
    stmt = select(Scene)
    if enabled_only:
        stmt = stmt.where(Scene.enabled == True)
    result = await session.execute(stmt)
    scenes = list(result.scalars().all())
    return SceneListResponse(scenes=[SceneResponse.from_orm(s) for s in scenes])


@router.post("", response_model=SceneResponse, status_code=201)
async def create_scene(
    body: SceneCreate,
    request: Request,
    _token: str = Depends(verify_module_token),
) -> SceneResponse:
    factory = request.app.state.db_session_factory
    name_en = await translate_to_en(body.name_user)

    async with factory() as session:
        async with session.begin():
            scene = Scene(
                name_user=body.name_user,
                name_en=name_en,
                trigger=body.trigger,
                enabled=body.enabled,
            )
            scene.set_actions(body.actions)
            session.add(scene)
        await session.refresh(scene)

    await on_entity_changed("scene", scene.id, "created")
    patterns = await get_entity_patterns(factory, f"scene:{scene.id}")
    return SceneResponse.from_orm(scene, patterns=patterns)


@router.put("/{scene_id}", response_model=SceneResponse)
async def update_scene(
    scene_id: int,
    body: SceneUpdate,
    request: Request,
    _token: str = Depends(verify_module_token),
) -> SceneResponse:
    factory = request.app.state.db_session_factory
    async with factory() as session:
        async with session.begin():
            result = await session.execute(
                select(Scene).where(Scene.id == scene_id)
            )
            scene = result.scalar_one_or_none()
            if scene is None:
                raise HTTPException(status_code=404, detail="Scene not found")

            if body.name_user is not None:
                scene.name_user = body.name_user
                scene.name_en = await translate_to_en(body.name_user)
            if body.actions is not None:
                scene.set_actions(body.actions)
            if body.trigger is not None:
                scene.trigger = body.trigger
            if body.enabled is not None:
                scene.enabled = body.enabled

        await session.refresh(scene)

    await on_entity_changed("scene", scene.id, "updated")
    patterns = await get_entity_patterns(factory, f"scene:{scene.id}")
    return SceneResponse.from_orm(scene, patterns=patterns)


@router.delete("/{scene_id}")
async def delete_scene(
    scene_id: int,
    request: Request,
    _token: str = Depends(verify_module_token),
) -> Response:
    factory = request.app.state.db_session_factory
    async with factory() as session:
        async with session.begin():
            result = await session.execute(
                select(Scene).where(Scene.id == scene_id)
            )
            scene = result.scalar_one_or_none()
            if scene is None:
                raise HTTPException(status_code=404, detail="Scene not found")
            await session.delete(scene)

    await on_entity_changed("scene", scene_id, "deleted")
    return Response(status_code=204)


# ── Activation ────────────────────────────────────────────────────────────


class SceneActivateResponse(BaseModel):
    scene_id: int
    name_user: str
    actions_run: int
    actions_failed: int
    errors: list[str] = []


async def _run_action(
    action: dict[str, Any],
    factory: Any,
) -> None:
    """Execute a single scene action. Mirrors automation_engine action types.

    Bare `{device_id, state}` actions (the original Scene format) are treated
    as device_command for backward compat.
    """
    a_type = action.get("type")
    if a_type is None and "device_id" in action and "state" in action:
        a_type = "device_command"

    if a_type == "device_command":
        device_id = action["device_id"]
        new_state = action.get("state", {})
        async with factory() as session:
            async with session.begin():
                registry = DeviceRegistry(session)
                old_device = await registry.get(device_id)
                if old_device is None:
                    raise DeviceNotFoundError(f"Device {device_id} not found")
                old_state = old_device.get_state()
                await registry.update_state(device_id, new_state)
        await get_event_bus().publish(
            type=DEVICE_STATE_CHANGED,
            source="core.scenes",
            payload={"device_id": device_id, "old_state": old_state, "new_state": new_state},
        )
        return

    if a_type == "delay":
        await asyncio.sleep(float(action.get("seconds", 1)))
        return

    if a_type == "publish_event":
        await get_event_bus().publish(
            type=action.get("event_type", "scene.custom_event"),
            source="core.scenes",
            payload=action.get("payload", {}),
        )
        return

    if a_type == "notify":
        await get_event_bus().publish(
            type="notification.send",
            source="core.scenes",
            payload={
                "message": action.get("message", ""),
                "channel": action.get("channel", "push"),
            },
        )
        return

    raise ValueError(f"Unknown action type: {a_type!r}")


@router.post("/{scene_id}/activate", response_model=SceneActivateResponse)
async def activate_scene(
    scene_id: int,
    request: Request,
    _token: str = Depends(verify_module_token),
) -> SceneActivateResponse:
    factory = request.app.state.db_session_factory
    async with factory() as session:
        result = await session.execute(select(Scene).where(Scene.id == scene_id))
        scene = result.scalar_one_or_none()
        if scene is None:
            raise HTTPException(status_code=404, detail="Scene not found")
        if not scene.enabled:
            raise HTTPException(status_code=409, detail="Scene is disabled")
        actions = scene.get_actions()
        scene_name = scene.name_user

    bus = get_event_bus()
    await bus.publish(
        type=SCENE_ACTIVATE,
        source="core.scenes",
        payload={"scene_id": scene_id, "name_user": scene_name, "action_count": len(actions)},
    )

    errors: list[str] = []
    ran = 0
    for index, action in enumerate(actions):
        try:
            await _run_action(action, factory)
            ran += 1
        except Exception as exc:
            msg = f"action[{index}]: {exc}"
            logger.warning("Scene %s action failed: %s", scene_id, msg)
            errors.append(msg)

    if errors:
        await bus.publish(
            type=SCENE_FAILED,
            source="core.scenes",
            payload={"scene_id": scene_id, "name_user": scene_name, "errors": errors},
        )
    else:
        await bus.publish(
            type=SCENE_ACTIVATED,
            source="core.scenes",
            payload={"scene_id": scene_id, "name_user": scene_name, "actions_run": ran},
        )

    return SceneActivateResponse(
        scene_id=scene_id,
        name_user=scene_name,
        actions_run=ran,
        actions_failed=len(errors),
        errors=errors,
    )
