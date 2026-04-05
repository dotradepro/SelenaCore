"""
core/api/routes/radio.py — Radio Station catalog CRUD

Dual-language: name_user (original), name_en (auto-translated).
Used by LLM prompt builder and media-player module.
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
from core.api.dependencies import get_db_session
from core.api.helpers import get_entity_patterns, on_entity_changed, translate_to_en
from core.registry.models import RadioStation

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/radio", tags=["radio"])


# ── Pydantic schemas ─────��─────────────────���─────────────────────────────

class RadioStationCreate(BaseModel):
    name_user: str = Field(..., min_length=1, max_length=255)
    stream_url: str = Field(..., min_length=1)
    genre_user: str = Field("", max_length=100)
    country: str = Field("", max_length=50)
    logo_url: str = ""
    enabled: bool = True
    favourite: bool = False


class RadioStationUpdate(BaseModel):
    name_user: str | None = Field(None, max_length=255)
    stream_url: str | None = None
    genre_user: str | None = Field(None, max_length=100)
    country: str | None = Field(None, max_length=50)
    logo_url: str | None = None
    enabled: bool | None = None
    favourite: bool | None = None


class RadioStationResponse(BaseModel):
    id: int
    name_user: str
    name_en: str
    stream_url: str
    genre_user: str
    genre_en: str
    country: str
    logo_url: str
    enabled: bool
    favourite: bool
    patterns_en: list[str] = []

    @classmethod
    def from_orm(cls, s: RadioStation, patterns: list[str] | None = None) -> "RadioStationResponse":
        return cls(
            id=s.id,
            name_user=s.name_user,
            name_en=s.name_en,
            stream_url=s.stream_url,
            genre_user=s.genre_user,
            genre_en=s.genre_en,
            country=s.country,
            logo_url=s.logo_url,
            enabled=s.enabled,
            favourite=s.favourite,
            patterns_en=patterns or [],
        )


class RadioStationListResponse(BaseModel):
    stations: list[RadioStationResponse]


# ── Endpoints ────────────────────────────────────���───────────────────────

@router.get("", response_model=RadioStationListResponse)
async def list_stations(
    enabled_only: bool = False,
    genre: str | None = None,
    session: AsyncSession = Depends(get_db_session),
    _token: str = Depends(verify_module_token),
) -> RadioStationListResponse:
    stmt = select(RadioStation)
    if enabled_only:
        stmt = stmt.where(RadioStation.enabled == True)
    if genre:
        stmt = stmt.where(
            RadioStation.genre_en.ilike(f"%{genre}%")
            | RadioStation.genre_user.ilike(f"%{genre}%")
        )
    result = await session.execute(stmt)
    stations = list(result.scalars().all())
    return RadioStationListResponse(
        stations=[RadioStationResponse.from_orm(s) for s in stations]
    )


@router.post("", response_model=RadioStationResponse, status_code=201)
async def create_station(
    body: RadioStationCreate,
    request: Request,
    _token: str = Depends(verify_module_token),
) -> RadioStationResponse:
    factory = request.app.state.db_session_factory
    name_en = await translate_to_en(body.name_user)
    genre_en = await translate_to_en(body.genre_user)

    async with factory() as session:
        async with session.begin():
            station = RadioStation(
                name_user=body.name_user,
                name_en=name_en,
                stream_url=body.stream_url,
                genre_user=body.genre_user,
                genre_en=genre_en,
                country=body.country,
                logo_url=body.logo_url,
                enabled=body.enabled,
                favourite=body.favourite,
            )
            session.add(station)
        await session.refresh(station)

    await on_entity_changed("radio_station", station.id, "created")

    patterns = await get_entity_patterns(factory, f"radio_station:{station.id}")
    return RadioStationResponse.from_orm(station, patterns=patterns)


@router.put("/{station_id}", response_model=RadioStationResponse)
async def update_station(
    station_id: int,
    body: RadioStationUpdate,
    request: Request,
    _token: str = Depends(verify_module_token),
) -> RadioStationResponse:
    factory = request.app.state.db_session_factory
    async with factory() as session:
        async with session.begin():
            result = await session.execute(
                select(RadioStation).where(RadioStation.id == station_id)
            )
            station = result.scalar_one_or_none()
            if station is None:
                raise HTTPException(status_code=404, detail="Station not found")

            if body.name_user is not None:
                station.name_user = body.name_user
                station.name_en = await translate_to_en(body.name_user)
            if body.stream_url is not None:
                station.stream_url = body.stream_url
            if body.genre_user is not None:
                station.genre_user = body.genre_user
                station.genre_en = await translate_to_en(body.genre_user)
            if body.country is not None:
                station.country = body.country
            if body.logo_url is not None:
                station.logo_url = body.logo_url
            if body.enabled is not None:
                station.enabled = body.enabled
            if body.favourite is not None:
                station.favourite = body.favourite

        await session.refresh(station)

    await on_entity_changed("radio_station", station.id, "updated")
    patterns = await get_entity_patterns(factory, f"radio_station:{station.id}")
    return RadioStationResponse.from_orm(station, patterns=patterns)


@router.delete("/{station_id}")
async def delete_station(
    station_id: int,
    request: Request,
    _token: str = Depends(verify_module_token),
) -> Response:
    factory = request.app.state.db_session_factory
    async with factory() as session:
        async with session.begin():
            result = await session.execute(
                select(RadioStation).where(RadioStation.id == station_id)
            )
            station = result.scalar_one_or_none()
            if station is None:
                raise HTTPException(status_code=404, detail="Station not found")
            await session.delete(station)

    await on_entity_changed("radio_station", station_id, "deleted")
    return Response(status_code=204)
