"""
core/api/routes/module_data.py — Generic widget data/action proxy for the
template engine (dashboard recraft Phase 2).

Routes:
  GET  /api/v1/modules/{name}/data/{key}     → returns widget JSON payload
  POST /api/v1/modules/{name}/action/{key}   → dispatches a write action

Resolution:
  * Look up the module's manifest (via :class:`Sandbox`) for the
    ``ui.widget.data_endpoints[key].path`` or ``ui.widget.actions[key].path``
    string.
  * Forward an in-process HTTP request to ``/api/ui/modules/{name}{path}``
    using ``httpx.ASGITransport`` so we hit the same FastAPI app without
    going over the network.
  * Apply a per-(name, key) TTL cache for ``GET`` responses.
  * On timeout or upstream error, fall back to a cached value if it is
    less than ``STALE_GRACE_S`` seconds old (stale-while-revalidate).
"""
from __future__ import annotations

import logging
import time
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request

from core.module_loader.sandbox import get_sandbox

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/modules", tags=["widget-data"])

# Hard upstream timeout — see docs/dashboard-recraft.md §5 risks.
UPSTREAM_TIMEOUT_S = 0.8
# How long a cached value remains servable as a fallback after the upstream
# fails. Must be >= the longest cache_ttl_s declared in any manifest.
STALE_GRACE_S = 60.0


# ── In-memory cache ────────────────────────────────────────────────────────

class _CacheEntry:
    __slots__ = ("data", "ts", "ttl")

    def __init__(self, data: Any, ts: float, ttl: float) -> None:
        self.data = data
        self.ts = ts
        self.ttl = ttl

    def fresh(self, now: float) -> bool:
        return now - self.ts < self.ttl

    def stale_alive(self, now: float) -> bool:
        return now - self.ts < STALE_GRACE_S


_cache: dict[tuple[str, str], _CacheEntry] = {}


def _cache_get(name: str, key: str) -> _CacheEntry | None:
    return _cache.get((name, key))


def _cache_put(name: str, key: str, data: Any, ttl: float) -> None:
    _cache[(name, key)] = _CacheEntry(data, time.monotonic(), ttl)


def _cache_invalidate(name: str) -> None:
    """Drop every cached entry belonging to a module — called after an
    action so the next data fetch reflects the write."""
    for cache_key in [k for k in _cache if k[0] == name]:
        _cache.pop(cache_key, None)


# ── Manifest lookup ────────────────────────────────────────────────────────

def _widget_endpoint(name: str, key: str, kind: str) -> tuple[str, float]:
    """Return (path, cache_ttl_s) for ``data_endpoints`` or ``actions`` entry.

    Raises HTTPException if the module isn't loaded, has no widget block, or
    the key isn't declared.
    """
    info = get_sandbox().get_module(name)
    if info is None:
        raise HTTPException(404, f"Module {name!r} not found")
    manifest = info.manifest or {}
    widget = (manifest.get("ui") or {}).get("widget") or {}
    if widget.get("kind") != "template":
        raise HTTPException(
            409,
            f"Module {name!r} is not a template widget (kind={widget.get('kind', 'custom')!r})",
        )
    section_name = "data_endpoints" if kind == "data" else "actions"
    section = widget.get(section_name) or {}
    entry = section.get(key)
    if not entry:
        raise HTTPException(404, f"No {section_name}[{key!r}] declared in manifest")
    path = entry.get("path")
    if not path:
        raise HTTPException(500, f"Manifest {section_name}[{key!r}] has no path")
    ttl = float(entry.get("cache_ttl_s", 5.0)) if kind == "data" else 0.0
    return path, ttl


# ── Routes ────────────────────────────────────────────────────────────────

@router.get("/{name}/data/{key}")
async def get_widget_data(name: str, key: str, request: Request) -> Any:
    path, ttl = _widget_endpoint(name, key, "data")
    now = time.monotonic()
    cached = _cache_get(name, key)
    if cached and cached.fresh(now):
        return cached.data

    upstream_path = f"/api/ui/modules/{name}{path}"
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=request.app),
            base_url="http://core.local",
            timeout=UPSTREAM_TIMEOUT_S,
        ) as client:
            resp = await client.get(upstream_path)
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        # Stale-while-revalidate: serve last good payload if still alive.
        if cached and cached.stale_alive(now):
            logger.warning(
                "module_data: %s/%s upstream failed (%s); serving stale",
                name, key, exc,
            )
            return cached.data
        logger.warning("module_data: %s/%s upstream failed: %s", name, key, exc)
        raise HTTPException(502, f"Upstream {name}/{path} failed: {exc}")

    _cache_put(name, key, data, ttl)
    return data


@router.post("/{name}/action/{key}")
async def post_widget_action(name: str, key: str, request: Request) -> Any:
    path, _ = _widget_endpoint(name, key, "action")
    try:
        body: Any = await request.json() if await request.body() else None
    except ValueError:
        body = None

    upstream_path = f"/api/ui/modules/{name}{path}"
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=request.app),
            base_url="http://core.local",
            timeout=UPSTREAM_TIMEOUT_S * 2,  # actions can be slower than reads
        ) as client:
            resp = await client.post(upstream_path, json=body)
    except httpx.HTTPError as exc:
        logger.warning("module_data: %s action %s failed: %s", name, key, exc)
        raise HTTPException(502, f"Upstream {name}{path} failed: {exc}")

    if resp.status_code >= 400:
        # Surface module's error verbatim so the dashboard can show it.
        try:
            detail = resp.json()
        except ValueError:
            detail = resp.text
        raise HTTPException(resp.status_code, detail)

    _cache_invalidate(name)
    try:
        return resp.json()
    except ValueError:
        return {"status": "ok"}
