"""
benchmarks/conftest.py — shared fixtures for benchmark suite
"""
from __future__ import annotations

import os

os.environ.setdefault("CORE_DATA_DIR", "/tmp/selena-bench")
os.environ.setdefault("CORE_SECURE_DIR", "/tmp/selena-secure")
os.environ.setdefault("DEV_MODULE_TOKEN", "bench-token-xyz")

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.main import create_app
from core.registry.models import Base


@pytest_asyncio.fixture
async def app():
    application = create_app()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    application.state.db_session_factory = async_sessionmaker(
        engine, expire_on_commit=False
    )
    application.state.db_engine = engine
    yield application
    await engine.dispose()


@pytest_asyncio.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://bench") as c:
        yield c


@pytest.fixture
def auth_headers():
    return {"Authorization": "Bearer bench-token-xyz"}


@pytest_asyncio.fixture
async def db_session(app):
    async with app.state.db_session_factory() as session:
        yield session
        await session.rollback()
