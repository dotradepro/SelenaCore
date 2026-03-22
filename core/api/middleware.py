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

        ip = request.client.host if request.client else "unknown"
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
