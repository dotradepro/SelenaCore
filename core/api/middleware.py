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
    """Simple in-memory rate limiter: 60 requests/minute per IP."""

    LIMIT = 60
    WINDOW_SEC = 60

    def __init__(self, app, limit: int = 60, window_sec: int = 60) -> None:
        super().__init__(app)
        self.limit = limit
        self.window_sec = window_sec
        # { ip: [(timestamp, count)] }
        self._buckets: dict[str, list[float]] = defaultdict(list)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        ip = request.client.host if request.client else "unknown"
        now = time.monotonic()
        window_start = now - self.window_sec

        # Clean old entries
        self._buckets[ip] = [t for t in self._buckets[ip] if t > window_start]

        if len(self._buckets[ip]) >= self.limit:
            logger.warning("Rate limit exceeded for IP %s", ip)
            return Response(
                content='{"detail":"Rate limit exceeded. Try again later."}',
                status_code=429,
                media_type="application/json",
            )

        self._buckets[ip].append(now)
        return await call_next(request)


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
