"""
core/api/routes/scenes.py — Scene CRUD

Scenes are named sets of device actions.
Dual-language: name_user (original), name_en (auto-translated for LLM prompt).
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.api.auth import verify_module_token
from core.registry.models import Scene

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

    @classmethod
    def from_orm(cls, s: Scene) -> "SceneResponse":
        return cls(
            id=s.id,
            name_user=s.name_user,
            name_en=s.name_en,
            actions=s.get_actions(),
            trigger=s.trigger,
            enabled=s.enabled,
        )


class SceneListResponse(BaseModel):
    scenes: list[SceneResponse]


# ── Dependency ───────────────────────────────────────────────────────────

async def get_db_session(request: Request) -> AsyncSession:
    factory = request.app.state.db_session_factory
    async with factory() as session:
        yield session


# ── Auto-translate helper ────────────────────────────────────────────────

async def _translate_to_en(text: str) -> str:
    """Translate text to English via LLM. Returns original on failure."""
    if not text:
        return ""
    if all(ord(c) < 128 for c in text):
        return text
    try:
        from system_modules.llm_engine.ollama_client import get_ollama_client
        client = get_ollama_client()
        if not await client.is_available():
            return text
        raw = await client.generate(
            prompt=f"Translate to English (single phrase, no quotes): {text}",
            system="Reply with ONLY the translated text, nothing else.",
            temperature=0.0,
        )
        return raw.strip().strip('"').strip("'") if raw else text
    except Exception:
        return text


# ── Endpoints ────────────────────────────────────────────────────────────

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
    name_en = await _translate_to_en(body.name_user)

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

    await _on_entity_changed("scene", scene.id, "created")
    return SceneResponse.from_orm(scene)


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
                scene.name_en = await _translate_to_en(body.name_user)
            if body.actions is not None:
                scene.set_actions(body.actions)
            if body.trigger is not None:
                scene.trigger = body.trigger
            if body.enabled is not None:
                scene.enabled = body.enabled

        await session.refresh(scene)

    await _on_entity_changed("scene", scene.id, "updated")
    return SceneResponse.from_orm(scene)


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

    await _on_entity_changed("scene", scene_id, "deleted")
    return Response(status_code=204)


async def _on_entity_changed(entity_type: str, entity_id: int, action: str) -> None:
    """Generate/delete patterns + invalidate caches after entity data change."""
    try:
        from system_modules.llm_engine.pattern_generator import get_pattern_generator
        gen = get_pattern_generator()
        if action == "deleted":
            await gen.delete_for_entity(entity_type, entity_id)
        else:
            await gen.generate_for_entity(entity_type, entity_id)
    except Exception as exc:
        logger.debug("Pattern generation failed: %s", exc)

    try:
        from system_modules.llm_engine.intent_compiler import get_intent_compiler
        await get_intent_compiler().full_reload()
    except Exception:
        pass

    try:
        from system_modules.llm_engine.intent_router import get_intent_router
        get_intent_router().refresh_system_prompt()
    except Exception:
        pass

    try:
        from core.eventbus.bus import get_event_bus
        from core.eventbus.types import REGISTRY_ENTITY_CHANGED
        await get_event_bus().publish(
            type=REGISTRY_ENTITY_CHANGED,
            source="core.api",
            payload={"entity_type": entity_type, "entity_id": entity_id, "action": action},
        )
    except Exception:
        pass
