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

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse

from system_modules.ui_core.pwa import router as pwa_router
from system_modules.ui_core.routes.dashboard import router as dashboard_router
from system_modules.ui_core.wizard import router as wizard_router

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
CORE_API_BASE = os.getenv("CORE_API_BASE", "http://127.0.0.1:7070")


class CharsetMiddleware(BaseHTTPMiddleware):
    """Ensure JS/CSS assets include charset=utf-8 in Content-Type."""

    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[override]
        response: Response = await call_next(request)
        ct = response.headers.get("content-type", "")
        if ct.startswith(("application/javascript", "text/javascript", "text/css")) and "charset" not in ct:
            response.headers["content-type"] = ct + "; charset=utf-8"
        return response


class CoreApiProxyMiddleware(BaseHTTPMiddleware):
    """Reverse-proxy /api/* requests to Core API on port 7070."""

    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[override]
        if request.url.path.startswith("/api/"):
            return await self._proxy(request)
        return await call_next(request)

    async def _proxy(self, request: Request) -> Response:
        url = f"{CORE_API_BASE}{request.url.path}"
        if request.url.query:
            url = f"{url}?{request.url.query}"

        body = await request.body()
        headers = {
            k: v for k, v in request.headers.items()
            if k.lower() not in ("host", "transfer-encoding")
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.request(
                    method=request.method,
                    url=url,
                    headers=headers,
                    content=body if body else None,
                )
            excluded = {"transfer-encoding", "content-encoding", "content-length"}
            resp_headers = {
                k: v for k, v in resp.headers.items()
                if k.lower() not in excluded
            }
            return Response(
                content=resp.content,
                status_code=resp.status_code,
                headers=resp_headers,
            )
        except httpx.ConnectError:
            return Response(
                content='{"detail":"Core API unavailable"}',
                status_code=502,
                media_type="application/json",
            )


def create_ui_app() -> FastAPI:
    app = FastAPI(
        title="SelenaCore UI",
        description="SmartHome LK — Local Control Panel",
        version="0.3.0-beta",
        docs_url=None,
        redoc_url=None,
    )

    app.add_middleware(CharsetMiddleware)
    app.add_middleware(CoreApiProxyMiddleware)

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
