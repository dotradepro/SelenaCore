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
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response as FastAPIResponse
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


def _migrate_enabled_column(connection) -> None:
    """Add enabled column to devices table if missing."""
    import sqlalchemy as sa
    inspector = sa.inspect(connection)
    columns = {c["name"] for c in inspector.get_columns("devices")}
    if "enabled" not in columns:
        connection.execute(sa.text(
            "ALTER TABLE devices ADD COLUMN enabled BOOLEAN DEFAULT 1 NOT NULL"
        ))
        logger.info("Migration: added enabled column to devices")


def _migrate_intent_entity_types(connection) -> None:
    """Add entity_types JSON column to intent_definitions (no-hardcode refactor)."""
    import sqlalchemy as sa
    inspector = sa.inspect(connection)
    if "intent_definitions" not in inspector.get_table_names():
        return
    columns = {c["name"] for c in inspector.get_columns("intent_definitions")}
    if "entity_types" not in columns:
        connection.execute(sa.text(
            "ALTER TABLE intent_definitions ADD COLUMN entity_types TEXT"
        ))
        logger.info("Migration: added entity_types column to intent_definitions")


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


def _setup_snapshot_providers(session_factory, plugin_manager, sandbox) -> None:
    """Configure SyncManager snapshot providers for enriched hello messages."""
    from core.api.sync_manager import get_sync_manager
    from core.api.sync_bridge import _sanitize_payload
    from core.registry.service import DeviceRegistry

    async def _devices_snapshot() -> list[dict]:
        try:
            async with session_factory() as session:
                registry = DeviceRegistry(session)
                devices = await registry.get_all()
                return [
                    {
                        "device_id": d.device_id,
                        "name": d.name,
                        "type": d.type,
                        "protocol": d.protocol,
                        "state": _sanitize_payload(d.state) if isinstance(d.state, dict) else {},
                        "capabilities": d.capabilities if isinstance(d.capabilities, list) else [],
                        "last_seen": d.last_seen,
                        "module_id": d.module_id,
                        "meta": _sanitize_payload(d.meta) if isinstance(d.meta, dict) else {},
                        "entity_type": getattr(d, "entity_type", None),
                        "location": getattr(d, "location", None),
                    }
                    for d in devices
                ]
        except Exception as exc:
            logger.debug("devices_snapshot failed: %s", exc)
            return []

    def _modules_snapshot() -> list[dict]:
        try:
            return [
                {
                    "name": m.name,
                    "version": m.version,
                    "type": m.type,
                    "status": m.status.value,
                    "runtime_mode": m.runtime_mode,
                    "port": m.port,
                    "installed_at": m.installed_at,
                    "ui": m.manifest.get("ui"),
                }
                for m in plugin_manager.list_modules()
            ]
        except Exception:
            return []

    async def _system_snapshot() -> dict:
        from core.api.routes.ui import _read_hw_metrics
        from core.api.routes.system import get_system_mode
        from core.version import VERSION
        loop = asyncio.get_event_loop()
        hw = await loop.run_in_executor(None, _read_hw_metrics)
        return {
            "cpu_temp": hw.get("cpu_temp", 0),
            "cpu_load": hw.get("cpu_load", [0, 0, 0]),
            "cpu_count": hw.get("cpu_count", 1),
            "ram_used_mb": hw.get("ram_used_mb", 0),
            "ram_total_mb": hw.get("ram_total_mb", 0),
            "swap_used_mb": hw.get("swap_used_mb", 0),
            "swap_total_mb": hw.get("swap_total_mb", 0),
            "disk_used_gb": hw.get("disk_used_gb", 0),
            "disk_total_gb": hw.get("disk_total_gb", 0),
            "uptime": hw.get("uptime", 0),
            "mode": get_system_mode(),
            "version": VERSION,
        }

    def _voice_snapshot() -> dict:
        try:
            instance = sandbox.get_in_process_module("voice-core")
            if instance and hasattr(instance, "_privacy_mode"):
                return {
                    "state": "idle",
                    "privacy_mode": bool(instance._privacy_mode),
                }
        except Exception:
            pass
        return {"state": "idle", "privacy_mode": False}

    get_sync_manager().set_snapshot_providers(
        devices_fn=None,  # devices delivered via initial fetchDevices() + WS events
        modules_fn=_modules_snapshot,
        system_fn=_system_snapshot,
        voice_fn=_voice_snapshot,
    )
    logger.info("SyncManager snapshot providers configured")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan — startup and shutdown logic."""
    _setup_logging()
    logger.info("SelenaCore starting up...")

    # One-shot config migration: llm.ollama_url → voice.providers.ollama.url.
    # Runs before settings/yaml cache reads so downstream code sees the
    # canonical key. Failure is logged but non-fatal — the container keeps
    # booting on the legacy fallback read inside OllamaClient.
    try:
        from core.config import migrate_ollama_url_key
        migrate_ollama_url_key()
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("ollama_url migration skipped: %s", exc)

    settings = get_settings()

    # Setup database
    engine = create_async_engine(settings.db_url, echo=settings.debug)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Migration: add keywords columns if missing (v2.0 upgrade)
        await conn.run_sync(_migrate_keywords_columns)
        # Migration: add entity_type + location columns (Phase 6)
        await conn.run_sync(_migrate_entity_location_columns)
        # Migration: add enabled column for inactive cloud-only Tuya devices
        await conn.run_sync(_migrate_enabled_column)
        # Migration: add entity_types JSON column on intent_definitions
        await conn.run_sync(_migrate_intent_entity_types)
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

    # Start SyncBridge: forwards EventBus events to SyncManager (WebSocket)
    from core.api.sync_bridge import get_sync_bridge
    from core.api.sync_manager import get_sync_manager
    sync_bridge = get_sync_bridge()
    sync_bridge.start(bus, get_sync_manager())

    # Sync in-memory wizard state with the persisted core.yaml so that a
    # container restart after wizard completion doesn't resurrect a
    # half-empty _wizard_state that would mislead /wizard/requirements.
    try:
        from core.api.routes.ui import _init_wizard_state
        _init_wizard_state()
    except Exception as exc:
        logger.warning("Could not initialise wizard state from config: %s", exc)

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

    # Initialize audio mixer (generates ALSA dmix config for concurrent playback)
    try:
        from core.audio_mixer import get_mixer
        mixer = get_mixer()
        mixer.initialize()
    except Exception as exc:
        logger.warning("Audio mixer init failed: %s", exc)

    # Initialize prompt store (seed from en.json, cache from DB)
    try:
        from core.prompt_store import get_prompt_store
        ps = get_prompt_store()
        ps.set_session_factory(session_factory)
        await ps.initialize()
    except Exception as exc:
        logger.warning("Prompt store init failed: %s", exc)

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

    # Register SPA catch-all LAST — after all module routers are mounted.
    # This ensures /api/ui/modules/{name}/* routes are matched before the
    # catch-all /{full_path:path} which returns index.html.
    _register_spa_fallback(app)

    # Set snapshot providers for SyncManager hello message enrichment
    _setup_snapshot_providers(session_factory, manager, sandbox)

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
    # Stop SyncBridge (cancel pending throttle timers)
    sync_bridge.stop()
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

    # Shared assets for widget iframes (no auth)
    from core.api.routes import shared_assets
    app.include_router(shared_assets.router, prefix="/api")

    # UI routes (no auth — localhost only, protected by iptables)
    from core.api.routes import voice_engines, vosk as vosk_routes
    app.include_router(ui.router, prefix="/api/ui")
    app.include_router(setup.router, prefix="/api/ui")
    app.include_router(voice_engines.router, prefix="/api/ui")
    app.include_router(vosk_routes.router, prefix="/api/ui")

    # PWA routes (manifest.json, sw.js, network-info) — served directly
    from core.api.routes import pwa as pwa_routes
    app.include_router(pwa_routes.router)

    # ── Static file serving (SPA) — replaces the UI proxy server ────────
    _mount_static_files(app)

    return app


def _mount_static_files(app: FastAPI) -> None:
    """Mount React SPA static files and SPA catch-all fallback.

    MUST be called last — the catch-all route matches everything
    that wasn't handled by API routes above.
    """
    from starlette.staticfiles import StaticFiles
    from starlette.types import Receive, Scope, Send

    static_dir = Path("/opt/selena-core/system_modules/ui_core/static")
    if not static_dir.exists():
        logger.warning("Static directory not found: %s — SPA will not be served", static_dir)
        return

    class NoCacheStaticFiles(StaticFiles):
        """StaticFiles that always sends Cache-Control: no-cache."""

        async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
            async def send_with_no_cache(message: dict) -> None:
                if message["type"] == "http.response.start":
                    headers = list(message.get("headers", []))
                    headers.append((b"cache-control", b"no-cache, no-store, must-revalidate"))
                    message["headers"] = headers
                await send(message)
            await super().__call__(scope, receive, send_with_no_cache)

    # Mount known asset sub-directories
    assets_dir = static_dir / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", NoCacheStaticFiles(directory=str(assets_dir)), name="assets")
    icons_dir = static_dir / "icons"
    if icons_dir.is_dir():
        app.mount("/icons", NoCacheStaticFiles(directory=str(icons_dir)), name="icons")

    # SPA catch-all: any non-API, non-asset path returns index.html
    # so that React Router handles client-side routes on page refresh.
    index_html = static_dir / "index.html"
    static_resolved = static_dir.resolve()

    _MIME_TYPES = {
        "js": "application/javascript; charset=utf-8",
        "css": "text/css; charset=utf-8",
        "html": "text/html; charset=utf-8",
        "json": "application/json",
        "png": "image/png",
        "svg": "image/svg+xml",
        "ico": "image/x-icon",
        "woff2": "font/woff2",
        "woff": "font/woff",
        "ttf": "font/ttf",
        "webp": "image/webp",
        "webmanifest": "application/manifest+json",
    }

    # SPA catch-all is NOT registered here — it MUST be registered AFTER
    # module routers are dynamically added during lifespan startup.
    # See: _register_spa_fallback() called at end of lifespan().


_SPA_MIME_TYPES = {
    "js": "application/javascript; charset=utf-8",
    "css": "text/css; charset=utf-8",
    "html": "text/html; charset=utf-8",
    "json": "application/json",
    "png": "image/png",
    "svg": "image/svg+xml",
    "ico": "image/x-icon",
    "woff2": "font/woff2",
    "woff": "font/woff",
    "ttf": "font/ttf",
    "webp": "image/webp",
    "webmanifest": "application/manifest+json",
}

_STATIC_DIR = Path("/opt/selena-core/system_modules/ui_core/static")


def _register_spa_fallback(app: FastAPI) -> None:
    """Register SPA catch-all route. MUST be called after all module routers."""
    static_dir = _STATIC_DIR
    if not static_dir.exists():
        return
    index_html = static_dir / "index.html"
    static_resolved = static_dir.resolve()

    @app.get("/{full_path:path}", include_in_schema=False, response_class=FastAPIResponse)
    async def spa_fallback(full_path: str):
        # Serve real static file if it exists
        candidate = (static_dir / full_path).resolve()
        if candidate.is_file() and str(candidate).startswith(str(static_resolved)):
            ext = candidate.name.rsplit(".", 1)[-1].lower() if "." in candidate.name else ""
            media_type = _SPA_MIME_TYPES.get(ext, "application/octet-stream")
            return FastAPIResponse(
                content=candidate.read_bytes(),
                media_type=media_type,
                headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
            )
        # SPA routing — serve index.html
        if index_html.is_file():
            return FastAPIResponse(
                content=index_html.read_bytes(),
                media_type="text/html",
                headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
            )
        return FastAPIResponse(content=b"Not Found", status_code=404)


app = create_app()
