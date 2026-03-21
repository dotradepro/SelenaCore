"""
system_modules/ui_core/pwa.py — PWA manifest + Service Worker endpoint + AP mode QR
"""
from __future__ import annotations

import io
import logging
import socket
import subprocess
from pathlib import Path

import qrcode
from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response

logger = logging.getLogger(__name__)
router = APIRouter(tags=["pwa"])

_STATIC_DIR = Path(__file__).parent / "static"


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
        "lang": "ru",
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


@router.get("/api/ui/ap-qr", tags=["ap-mode"])
async def get_ap_qr_code() -> Response:
    """Generate a QR code for the AP mode Wi-Fi connection URL.

    Returns PNG image of the QR code pointing to the local UI.
    """
    try:
        local_ip = _get_local_ip()
    except Exception:
        local_ip = "192.168.4.1"

    url = f"http://{local_ip}:8080"
    img = qrcode.make(url)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Response(content=buf.getvalue(), media_type="image/png")


def _get_local_ip() -> str:
    """Get the LAN IP address of the device."""
    try:
        # Try connecting to a known address to determine local IP
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return socket.gethostbyname(socket.gethostname())


@router.get("/api/ui/network-info", tags=["ap-mode"])
async def get_network_info() -> JSONResponse:
    """Return current Wi-Fi/network status."""
    try:
        result = subprocess.run(
            ["ip", "route", "show", "default"],
            capture_output=True, text=True, timeout=5
        )
        default_route = result.stdout.strip()
    except Exception:
        default_route = ""

    local_ip = _get_local_ip()
    return JSONResponse({
        "local_ip": local_ip,
        "ui_url": f"http://{local_ip}:8080",
        "core_url": f"http://{local_ip}:7070",
        "default_route": default_route,
    })
