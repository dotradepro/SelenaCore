"""
tests/conftest.py — shared pytest fixtures
"""
from __future__ import annotations

import os

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# Use in-memory SQLite for tests
os.environ.setdefault("CORE_DATA_DIR", "/tmp/selena-test")
os.environ.setdefault("CORE_SECURE_DIR", "/tmp/selena-secure")
os.environ.setdefault("DEV_MODULE_TOKEN", "test-module-token-xyz")

from core.main import create_app
from core.registry.models import Base


@pytest_asyncio.fixture
async def app():
    application = create_app()
    # Override DB to in-memory for tests
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
    from core.eventbus.bus import get_event_bus
    bus = get_event_bus()
    # Don't start the full dispatch loop in tests
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def auth_headers():
    return {"Authorization": "Bearer test-module-token-xyz"}
