"""
core/main.py — FastAPI application entry point for SelenaCore
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
from core.api.routes import devices, events, integrity, intents, modules, radio, scenes, secrets, setup, system, ui
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


def _migrate_keywords_columns(connection) -> None:
    """Add keywords_user and keywords_en columns to devices table if missing."""
    import sqlalchemy as sa
    inspector = sa.inspect(connection)
    columns = {c["name"] for c in inspector.get_columns("devices")}
    if "keywords_user" not in columns:
        connection.execute(sa.text("ALTER TABLE devices ADD COLUMN keywords_user TEXT DEFAULT '[]'"))
        logger.info("Migration: added keywords_user column to devices")
    if "keywords_en" not in columns:
        connection.execute(sa.text("ALTER TABLE devices ADD COLUMN keywords_en TEXT DEFAULT '[]'"))
        logger.info("Migration: added keywords_en column to devices")


def _migrate_entity_location_columns(connection) -> None:
    """Add entity_type and location columns to devices table if missing."""
    import sqlalchemy as sa
    inspector = sa.inspect(connection)
    columns = {c["name"] for c in inspector.get_columns("devices")}
    if "entity_type" not in columns:
        connection.execute(sa.text("ALTER TABLE devices ADD COLUMN entity_type VARCHAR(50)"))
        logger.info("Migration: added entity_type column to devices")
    if "location" not in columns:
        connection.execute(sa.text("ALTER TABLE devices ADD COLUMN location VARCHAR(100)"))
        logger.info("Migration: added location column to devices")


async def _preload_module_registry(session_factory) -> None:
    """Load registered_modules from DB into in-memory ModuleRegistry.

    Ensures the LLM prompt has module catalog from first request,
    even before modules finish starting.
    """
    try:
        from sqlalchemy import select
        from core.registry.models import RegisteredModule
        from core.module_registry import ModuleEntry, get_module_registry

        registry = get_module_registry()
        async with session_factory() as session:
            result = await session.execute(
                select(RegisteredModule).where(RegisteredModule.enabled == True)
            )
            modules = list(result.scalars().all())

        for m in modules:
            entry = ModuleEntry(
                name=m.name,
                group=m.group,
                intents=m.get_intents(),
                entities=m.get_entities(),
                description=m.description_en or m.description_user,
                type=m.module_type,
                status="READY",
            )
            registry.register(entry)

        if modules:
            logger.info("Pre-loaded %d modules from DB into ModuleRegistry", len(modules))
    except Exception as exc:
        logger.debug("ModuleRegistry pre-load skipped: %s", exc)


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
        # Migration: add keywords columns if missing (v2.0 upgrade)
        await conn.run_sync(_migrate_keywords_columns)
        # Migration: add entity_type + location columns (Phase 6)
        await conn.run_sync(_migrate_entity_location_columns)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    app.state.db_session_factory = session_factory
    app.state.db_engine = engine

    # Inject session factory into sandbox BEFORE module scanning
    # so that SYSTEM modules have DB access from their start() call.
    from core.module_loader.sandbox import get_sandbox
    sandbox = get_sandbox()
    sandbox.set_session_factory(session_factory)

    # Start event bus
    bus = get_event_bus()
    await bus.start()

    # Publish startup event
    from core.version import VERSION
    await bus.publish(
        type=CORE_STARTUP,
        source="core",
        payload={"version": VERSION},
    )

    # Start CloudSync
    cloud_sync = get_cloud_sync()
    await cloud_sync.start()

    # Pre-load ModuleRegistry from DB (modules from previous run)
    await _preload_module_registry(session_factory)

    # Auto-discover system modules (pre-installed, type=SYSTEM)
    from core.module_loader.loader import get_plugin_manager
    manager = get_plugin_manager()
    system_modules_dir = Path("/opt/selena-core/system_modules")
    await manager.scan_local_modules(system_modules_dir)

    # Mount each in-process system module's router into the core app
    # Routes become available at /api/ui/modules/{name}/*
    from core.module_loader.sandbox import ModuleStatus
    for mod_info in sandbox.list_modules():
        if mod_info.type == "SYSTEM" and mod_info.status == ModuleStatus.RUNNING:
            instance = sandbox.get_in_process_module(mod_info.name)
            if instance:
                router = instance.get_router()
                if router:
                    app.include_router(
                        router,
                        prefix=f"/api/ui/modules/{mod_info.name}",
                        tags=[f"system:{mod_info.name}"],
                    )
                    logger.info("Mounted router for system module '%s'", mod_info.name)

    # Auto-discover user-installed modules
    modules_dir = Path("/opt/selena-core/modules")
    await manager.scan_local_modules(modules_dir)

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
    # Gracefully shut down bus-connected user modules (drain period)
    from core.module_bus import get_module_bus
    await get_module_bus().shutdown_all(drain_ms=5000)
    # Gracefully stop all in-process system modules
    await sandbox.shutdown_in_process_modules()
    await bus.stop()
    await engine.dispose()
    logger.info("SelenaCore shutdown complete")


def create_app() -> FastAPI:
    from core.version import VERSION
    app = FastAPI(
        title="SelenaCore API",
        description="SmartHome LK — Local Device Core",
        version=VERSION,
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
    app.include_router(secrets.router, prefix=api_prefix)
    app.include_router(intents.router, prefix=api_prefix)
    app.include_router(radio.router, prefix=api_prefix)
    app.include_router(scenes.router, prefix=api_prefix)

    # Module Bus — WebSocket endpoint for user modules
    from core.api.routes import bus as bus_routes
    app.include_router(bus_routes.router, prefix=api_prefix)

    # UI routes (no auth — localhost only, protected by iptables)
    from core.api.routes import voice_engines
    app.include_router(ui.router, prefix="/api/ui")
    app.include_router(setup.router, prefix="/api/ui")
    app.include_router(voice_engines.router, prefix="/api/ui")

    return app


app = create_app()
