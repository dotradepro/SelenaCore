"""
core/api/routes/pwa.py — PWA manifest, Service Worker, network info

Moved from system_modules/ui_core/pwa.py to eliminate the UI proxy layer.
"""
from __future__ import annotations

import io
import logging
import os
import socket
import subprocess
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

logger = logging.getLogger(__name__)
router = APIRouter(tags=["pwa"])

_STATIC_DIR = Path("/opt/selena-core/system_modules/ui_core/static")


@router.get("/manifest.json", include_in_schema=False)
async def pwa_manifest() -> JSONResponse:
    """PWA Web App Manifest."""
    hostname = socket.gethostname()
    return JSONResponse({
        "name": f"SelenaCore — {hostname}",
        "short_name": "Selena",
        "description": "SmartHome LK Local Control Panel",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#0f172a",
        "theme_color": "#6366f1",
        "icons": [
            {"src": "/icons/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/icons/icon-512.png", "sizes": "512x512", "type": "image/png"},
        ],
        "categories": ["utilities"],
        "lang": "en",
    })


@router.get("/sw.js", include_in_schema=False)
async def service_worker() -> Response:
    """Minimal Service Worker for offline support."""
    sw_path = _STATIC_DIR / "sw.js"
    if sw_path.exists():
        content = sw_path.read_text()
    else:
        content = _default_sw()
    return Response(
        content=content,
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/"},
    )


def _default_sw() -> str:
    return """\
const CACHE = 'selena-v1';
const OFFLINE = '/offline.html';
const PRECACHE = ['/', '/offline.html', '/manifest.json'];

self.addEventListener('install', e =>
  e.waitUntil(
    caches.open(CACHE).then(c => c.addAll(PRECACHE)).then(() => self.skipWaiting())
  )
);

self.addEventListener('activate', e =>
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  )
);

self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET') return;
  e.respondWith(
    fetch(e.request)
      .then(r => {
        const clone = r.clone();
        caches.open(CACHE).then(c => c.put(e.request, clone));
        return r;
      })
      .catch(() => caches.match(e.request).then(r => r || caches.match(OFFLINE)))
  );
});
"""


def _get_local_ip() -> str:
    """Get the LAN IP address of the device."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return socket.gethostbyname(socket.gethostname())


def _get_ui_url(request: Request) -> str:
    """Build the UI URL from the incoming request Host header or env override."""
    host_env = os.environ.get("HOST_IP", "").strip()
    if host_env:
        return f"http://{host_env}"
    host_header = request.headers.get("host", "").strip()
    if host_header:
        host_no_port = host_header.split(":")[0]
        return f"http://{host_no_port}"
    return f"http://{_get_local_ip()}"


@router.get("/api/ui/network-info", tags=["ap-mode"])
async def get_network_info() -> JSONResponse:
    """Return current Wi-Fi/network status."""
    try:
        result = subprocess.run(
            ["ip", "route", "show", "default"],
            capture_output=True, text=True, timeout=5,
        )
        default_route = result.stdout.strip()
    except Exception:
        default_route = ""

    local_ip = _get_local_ip()
    return JSONResponse({
        "local_ip": local_ip,
        "ui_url": f"http://{local_ip}",
        "default_route": default_route,
    })
