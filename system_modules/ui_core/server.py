"""
system_modules/ui_core/server.py — FastAPI UI server on port 80

Serves:
  - Static PWA files from /static/
  - Dashboard, modules, settings pages
  - Onboarding wizard endpoints
  - AP mode + QR code generation
  - /api/ui/* proxy helpers
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

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


class CoreApiProxyMiddleware:
    """Reverse-proxy /api/* to Core API on port 7070.

    Pure ASGI middleware (no BaseHTTPMiddleware) so SSE responses are never
    buffered — we write directly to the ASGI 'send' callable.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http" and scope.get("path", "").startswith("/api/"):
            if scope["path"] == "/api/ui/stream":
                await self._proxy_stream(scope, receive, send)
            else:
                await self._proxy(scope, receive, send)
            return
        await self.app(scope, receive, send)

    @staticmethod
    def _get_client_ip(scope) -> str:
        """Extract real client IP from ASGI scope."""
        client = scope.get("client")
        return client[0] if client else ""

    @staticmethod
    def _get_header(scope, name: bytes) -> str:
        """Get a single header value from ASGI scope."""
        for hname, hval in scope.get("headers", []):
            if hname.lower() == name.lower():
                return hval.decode("latin-1")
        return ""

    async def _proxy_stream(self, scope, receive, send) -> None:
        """Zero-copy SSE proxy — writes chunks straight to ASGI send.

        Retries connection up to 3 times with 2s delay to handle transient
        failures during container startup.
        """
        path = scope["path"]
        query = scope.get("query_string", b"").decode("latin-1")
        url = f"{CORE_API_BASE}{path}" + (f"?{query}" if query else "")

        fwd_headers = {
            name.decode("latin-1").lower(): value.decode("latin-1")
            for name, value in scope.get("headers", [])
            if name.lower() not in (b"host", b"transfer-encoding", b"content-length")
        }
        # Inject real client IP and forwarding headers so backend sees them
        client_ip = self._get_client_ip(scope)
        if client_ip:
            fwd_headers["x-forwarded-for"] = client_ip
            fwd_headers["x-real-ip"] = client_ip
        orig_host = self._get_header(scope, b"host")
        if orig_host:
            fwd_headers["x-forwarded-host"] = orig_host
        fwd_headers["x-forwarded-proto"] = "https" if scope.get("scheme") == "https" else "http"

        sent_start = False
        max_retries = 3
        for attempt in range(1, max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(None)) as client:
                    async with client.stream("GET", url, headers=fwd_headers) as resp:
                        await send({
                            "type": "http.response.start",
                            "status": resp.status_code,
                            "headers": [
                                (b"content-type", b"text/event-stream; charset=utf-8"),
                                (b"cache-control", b"no-cache"),
                                (b"x-accel-buffering", b"no"),
                                (b"connection", b"keep-alive"),
                            ],
                        })
                        sent_start = True
                        async for chunk in resp.aiter_raw():
                            await send({
                                "type": "http.response.body",
                                "body": chunk,
                                "more_body": True,
                            })
                break  # stream ended normally
            except asyncio.CancelledError:
                break
            except Exception as exc:
                if attempt < max_retries and not sent_start:
                    logger.debug("SSE proxy attempt %d/%d failed: %s, retrying...", attempt, max_retries, exc)
                    await asyncio.sleep(2)
                else:
                    logger.error("SSE proxy error: %s", exc)
                    break

        # Send final response if nothing was sent during retries
        if not sent_start:
            try:
                await send({
                    "type": "http.response.start",
                    "status": 502,
                    "headers": [(b"content-type", b"application/json")],
                })
            except Exception:
                pass
        try:
            await send({"type": "http.response.body", "body": b"", "more_body": False})
        except Exception:
                    pass

    async def _proxy(self, scope, receive, send) -> None:
        """Buffered proxy for regular API requests."""
        path = scope["path"]
        query = scope.get("query_string", b"").decode("latin-1")
        url = f"{CORE_API_BASE}{path}" + (f"?{query}" if query else "")
        method = scope.get("method", "GET")

        body_parts: list[bytes] = []
        while True:
            message = await receive()
            if chunk := message.get("body", b""):
                body_parts.append(chunk)
            if not message.get("more_body", False):
                break
        body = b"".join(body_parts)

        fwd_headers = {
            name.decode("latin-1").lower(): value.decode("latin-1")
            for name, value in scope.get("headers", [])
            if name.lower() not in (b"host", b"transfer-encoding")
        }
        # Inject real client IP and forwarding headers so backend sees them
        client_ip = self._get_client_ip(scope)
        if client_ip:
            fwd_headers["x-forwarded-for"] = client_ip
            fwd_headers["x-real-ip"] = client_ip
        orig_host = self._get_header(scope, b"host")
        if orig_host:
            fwd_headers["x-forwarded-host"] = orig_host
        fwd_headers["x-forwarded-proto"] = "https" if scope.get("scheme") == "https" else "http"

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.request(
                    method=method,
                    url=url,
                    headers=fwd_headers,
                    content=body if body else None,
                )
            excluded = {"transfer-encoding", "content-encoding", "content-length"}
            resp_headers = [
                (k.lower().encode("latin-1"), v.encode("latin-1"))
                for k, v in resp.headers.multi_items()
                if k.lower() not in excluded
            ]
            await send({"type": "http.response.start", "status": resp.status_code, "headers": resp_headers})
            await send({"type": "http.response.body", "body": resp.content, "more_body": False})
        except httpx.ConnectError:
            await send({
                "type": "http.response.start",
                "status": 502,
                "headers": [(b"content-type", b"application/json")],
            })
            await send({
                "type": "http.response.body",
                "body": b'{"detail":"Core API unavailable"}',
                "more_body": False,
            })


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

    # Serve static files
    if STATIC_DIR.exists():
        # Mount known asset sub-directories for efficient serving
        _assets = STATIC_DIR / "assets"
        if _assets.is_dir():
            app.mount("/assets", StaticFiles(directory=str(_assets)), name="assets")
        _icons = STATIC_DIR / "icons"
        if _icons.is_dir():
            app.mount("/icons", StaticFiles(directory=str(_icons)), name="icons")

        # SPA catch-all: any non-API, non-asset path returns index.html
        # so that React Router handles client-side routes on page refresh.
        _index_html = STATIC_DIR / "index.html"
        _static_resolved = STATIC_DIR.resolve()

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa_fallback(full_path: str) -> Response:
            # If the path matches an actual static file, serve it
            candidate = (STATIC_DIR / full_path).resolve()
            if candidate.is_file() and str(candidate).startswith(str(_static_resolved)):
                return Response(
                    content=candidate.read_bytes(),
                    media_type=_guess_media_type(candidate.name),
                )
            # Otherwise serve index.html for SPA routing
            return Response(
                content=_index_html.read_bytes(),
                media_type="text/html",
            )

    return app


def _guess_media_type(filename: str) -> str:
    """Return a MIME type for common static file extensions."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return {
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
    }.get(ext, "application/octet-stream")


ui_app = create_ui_app()
