"""
core/api/dependencies.py — Shared FastAPI dependencies for route handlers.
"""
from __future__ import annotations

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession


async def get_db_session(request: Request) -> AsyncSession:
    """Yield a database session from the app-level session factory."""
    factory = request.app.state.db_session_factory
    async with factory() as session:
        yield session
