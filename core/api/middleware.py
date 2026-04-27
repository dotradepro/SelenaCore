"""
core/api/middleware.py — CORS, X-Request-Id, rate limiting
"""
from __future__ import annotations

import contextvars
import logging
import time
import uuid
from collections import defaultdict
from typing import Callable

from fastapi import Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)

# Context variable for request ID propagation
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default=""
)


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Assigns X-Request-Id to every request and propagates it via contextvars."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        request_id = request.headers.get("X-Request-Id") or str(uuid.uuid4())
        request_id_var.set(request_id)
        response = await call_next(request)
        response.headers["X-Request-Id"] = request_id
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Simple in-memory rate limiter.

    Rules:
    - Localhost (127.x) and LAN (192.168.x, 10.x, 172.16-31.x): 600 req/min
    - External IPs: 120 req/min
    - SSE stream endpoint and static files: always exempt (no counting)
    """

    # Paths that are never rate-limited (SSE keeps a long open connection,
    # static assets can produce many parallel requests on page load).
    EXEMPT_PREFIXES = (
        "/api/ui/stream",
        "/static/",
        "/assets/",
        "/favicon",
    )

    LIMIT_LOCAL = 600
    LIMIT_EXTERNAL = 120
    WINDOW_SEC = 60

    def __init__(self, app, limit: int = 300, window_sec: int = 60) -> None:
        super().__init__(app)
        # kept for backward-compat but ignored in favour of per-class limits
        self.limit = limit
        self.window_sec = window_sec
        self._buckets: dict[str, list[float]] = defaultdict(list)

    @staticmethod
    def _is_local(ip: str) -> bool:
        return (
            ip.startswith("127.")
            or ip.startswith("192.168.")
            or ip.startswith("10.")
            or ip == "::1"
            or (ip.startswith("172.") and _is_rfc1918_172(ip))
        )

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path

        # Exempt SSE and static assets entirely
        for prefix in self.EXEMPT_PREFIXES:
            if path.startswith(prefix):
                return await call_next(request)

        ip = (request.client.host if request.client else None) or "unknown"
        limit = self.LIMIT_LOCAL if self._is_local(ip) else self.LIMIT_EXTERNAL
        now = time.monotonic()
        window_start = now - self.WINDOW_SEC

        # Clean old entries
        self._buckets[ip] = [t for t in self._buckets[ip] if t > window_start]

        if len(self._buckets[ip]) >= limit:
            logger.warning("Rate limit exceeded for IP %s (path=%s)", ip, path)
            return Response(
                content='{"detail":"Rate limit exceeded. Try again later."}',
                status_code=429,
                media_type="application/json",
            )

        self._buckets[ip].append(now)
        return await call_next(request)


def _is_rfc1918_172(ip: str) -> bool:
    """Return True for 172.16.0.0/12 addresses."""
    try:
        parts = ip.split(".")
        if len(parts) != 4:
            return False
        second = int(parts[1])
        return 16 <= second <= 31
    except (ValueError, IndexError):
        return False


def get_request_id() -> str:
    return request_id_var.get("")


def setup_cors(app) -> None:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # Restricted by iptables at network level
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


# ── No-cache ASGI middleware ─────────────────────────────────────────────
# Starlette's BaseHTTPMiddleware (used elsewhere here for X-Request-Id and
# rate limiting) drops a handful of headers — including Cache-Control —
# from regular ``Response`` returns when wrapping the ASGI stream. Adding
# the header at the raw ASGI layer side-steps the loss and guarantees it
# reaches the wire for the two paths that absolutely must never be cached.

class NoCacheForPaths:
    """Append ``Cache-Control: no-store`` to specific paths' responses.

    Implemented as a raw ASGI middleware so it operates on the ``send``
    message before uvicorn writes the response, after every Starlette
    middleware has had its turn. Targets ``/sw.js`` and ``/manifest.json``
    — these are the entry points browsers cache aggressively under PWA
    rules and which we need to retire reliably across kiosk fleets.
    """

    NO_CACHE_PATHS = frozenset({"/sw.js", "/manifest.json"})

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http" or scope.get("path") not in self.NO_CACHE_PATHS:
            await self.app(scope, receive, send)
            return

        async def send_with_no_cache(message):
            if message["type"] == "http.response.start":
                # Replace any existing cache-control header rather than
                # appending so a downstream Starlette middleware can't
                # accidentally end up with two conflicting values.
                headers = [
                    (k, v) for k, v in message.get("headers", [])
                    if k.lower() != b"cache-control" and k.lower() != b"pragma" and k.lower() != b"expires"
                ]
                headers.append((b"cache-control", b"no-store, no-cache, must-revalidate, max-age=0"))
                headers.append((b"pragma", b"no-cache"))
                headers.append((b"expires", b"0"))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_no_cache)
