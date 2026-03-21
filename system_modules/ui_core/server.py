"""
system_modules/ui_core/server.py — FastAPI UI server on port 8080

Serves:
  - Static PWA files from /static/
  - Dashboard, modules, settings pages
  - Onboarding wizard endpoints
  - AP mode + QR code generation
  - /api/ui/* proxy helpers
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from system_modules.ui_core.pwa import router as pwa_router
from system_modules.ui_core.routes.dashboard import router as dashboard_router
from system_modules.ui_core.wizard import router as wizard_router

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


def create_ui_app() -> FastAPI:
    app = FastAPI(
        title="SelenaCore UI",
        description="SmartHome LK — Local Control Panel",
        version="0.3.0-beta",
        docs_url=None,
        redoc_url=None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # API routes
    app.include_router(pwa_router)
    app.include_router(dashboard_router)
    app.include_router(wizard_router)

    # Serve static files (SPA fallback handled by catch-all route in pwa.py)
    if STATIC_DIR.exists():
        app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")

    return app


ui_app = create_ui_app()
