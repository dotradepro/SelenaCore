"""
core/main.py — точка входа FastAPI приложения SelenaCore
"""
from __future__ import annotations

import asyncio
import logging
import logging.config
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

import yaml
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.api.middleware import RateLimitMiddleware, RequestIdMiddleware, setup_cors
from core.api.routes import devices, events, integrity, intents, modules, setup, system, ui
from core.config import get_settings
from core.cloud_sync.sync import get_cloud_sync
from core.eventbus.bus import get_event_bus
from core.eventbus.types import CORE_STARTUP, CORE_SHUTDOWN
from core.registry.models import Base

logger = logging.getLogger(__name__)


def _setup_logging() -> None:
    log_config_path = Path("/opt/selena-core/config/logging.yaml")
    if log_config_path.exists():
        with log_config_path.open() as f:
            config = yaml.safe_load(f)
        try:
            logging.config.dictConfig(config)
            return
        except Exception as e:
            pass  # Fall back to basic config
    # Fallback basic config
    settings = get_settings()
    level = getattr(logging, settings.core_log_level.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan — startup and shutdown logic."""
    _setup_logging()
    logger.info("SelenaCore starting up...")

    settings = get_settings()

    # Setup database
    engine = create_async_engine(settings.db_url, echo=settings.debug)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    app.state.db_session_factory = session_factory
    app.state.db_engine = engine

    # Start event bus
    bus = get_event_bus()
    await bus.start()

    # Publish startup event
    await bus.publish(
        type=CORE_STARTUP,
        source="core",
        payload={"version": "0.3.0-beta"},
    )

    # Start CloudSync
    cloud_sync = get_cloud_sync()
    await cloud_sync.start()

    # Auto-discover modules from local directory
    from core.module_loader.loader import get_plugin_manager
    manager = get_plugin_manager()
    modules_dir = Path("/opt/selena-core/modules")
    manager.scan_local_modules(modules_dir)

    logger.info("SelenaCore ready on port %s", settings.core_port)

    yield  # App is running

    # Shutdown
    logger.info("SelenaCore shutting down...")
    await cloud_sync.stop()
    await bus.publish(
        type=CORE_SHUTDOWN,
        source="core",
        payload={},
    )
    await bus.stop()
    await engine.dispose()
    logger.info("SelenaCore shutdown complete")


def create_app() -> FastAPI:
    app = FastAPI(
        title="SelenaCore API",
        description="SmartHome LK — Local Device Core",
        version="0.3.0-beta",
        docs_url="/docs" if os.environ.get("DEBUG", "false").lower() == "true" else None,
        redoc_url=None,
        lifespan=lifespan,
    )

    # Middlewares (order matters — applied in reverse)
    app.add_middleware(RateLimitMiddleware, limit=120, window_sec=60)
    app.add_middleware(RequestIdMiddleware)
    setup_cors(app)

    # API routes
    api_prefix = "/api/v1"
    app.include_router(system.router, prefix=api_prefix)
    app.include_router(devices.router, prefix=api_prefix)
    app.include_router(events.router, prefix=api_prefix)
    app.include_router(integrity.router, prefix=api_prefix)
    app.include_router(modules.router, prefix=api_prefix)
    app.include_router(intents.router, prefix=api_prefix)

    # UI routes (no auth — localhost only, protected by iptables)
    app.include_router(ui.router, prefix="/api/ui")
    app.include_router(setup.router, prefix="/api/ui")

    return app


app = create_app()
