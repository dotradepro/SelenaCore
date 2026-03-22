"""
system_modules/presence_detection/module.py — In-process SystemModule wrapper.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel

from core.module_loader.system_module import SystemModule
from system_modules.presence_detection.presence import PresenceDetector, mac_in_arp_table, _read_arp_table

logger = logging.getLogger(__name__)

_MODULE_DIR = Path(__file__).parent


class UserRequest(BaseModel):
    user_id: str
    name: str
    devices: list[dict] = []


class InviteRequest(BaseModel):
    name: str
    base_url: str = ""


class PushSubscribeRequest(BaseModel):
    user_id: str
    endpoint: str
    p256dh: str
    auth: str
    user_agent: str = ""


class PresenceDetectionModule(SystemModule):
    name = "presence-detection"

    def __init__(self) -> None:
        super().__init__()
        self._detector: PresenceDetector | None = None

    async def _on_state_changed(self, event) -> None:
        """Forward device state changes to the detector."""
        pass  # presence detector handles its own scanning

    async def start(self) -> None:
        db_path = os.getenv("PRESENCE_DB_PATH", ":memory:")
        # Use persistent path if CORE_DATA_DIR is set
        if db_path == ":memory:":
            data_dir = os.getenv("CORE_DATA_DIR", "/var/lib/selena")
            db_dir = Path(data_dir)
            if db_dir.exists():
                db_path = str(db_dir / "presence.db")

        self._detector = PresenceDetector(
            publish_event_cb=self.publish,
            scan_interval_sec=int(os.environ.get("PRESENCE_SCAN_INTERVAL", "60")),
            away_threshold_sec=int(os.environ.get("PRESENCE_AWAY_THRESHOLD", "180")),
            db_path=db_path,
        )
        await self._detector.start()
        self.subscribe(["device.state_changed"], self._on_state_changed)
        await self.publish("module.started", {"name": self.name})

    async def stop(self) -> None:
        if self._detector:
            await self._detector.stop()
        self._cleanup_subscriptions()
        await self.publish("module.stopped", {"name": self.name})

    def get_router(self) -> APIRouter:
        router = APIRouter()
        svc = self

        @router.get("/health")
        async def health() -> dict:
            status = svc._detector.get_status() if svc._detector else {}
            return {"status": "ok", "module": svc.name, **status}

        @router.get("/status")
        async def get_status() -> dict:
            if svc._detector is None:
                raise HTTPException(503, "Not running")
            return svc._detector.get_status()

        @router.get("/users")
        async def list_users() -> dict:
            if svc._detector is None:
                raise HTTPException(503, "Not running")
            return {"users": svc._detector.list_users()}

        @router.post("/users", status_code=201)
        async def add_user(req: UserRequest) -> dict:
            if svc._detector is None:
                raise HTTPException(503, "Not running")
            result = svc._detector.add_user({"user_id": req.user_id, "name": req.name, "devices": req.devices})
            return result

        @router.get("/users/{user_id}")
        async def get_user(user_id: str) -> dict:
            if svc._detector is None:
                raise HTTPException(503, "Not running")
            user = svc._detector.get_user(user_id)
            if not user:
                raise HTTPException(404, "User not found")
            return user

        @router.delete("/users/{user_id}", status_code=204, response_class=Response, response_model=None)
        async def remove_user(user_id: str) -> Response:
            if svc._detector is None:
                raise HTTPException(503, "Not running")
            svc._detector.remove_user(user_id)
            return Response(status_code=204)

        @router.post("/scan")
        async def trigger_scan() -> dict:
            if svc._detector is None:
                raise HTTPException(503, "Not running")
            results = await svc._detector.trigger_scan_now()
            return {"status": "scan_triggered", "results": results}

        @router.get("/discover")
        async def discover_network(active: bool = Query(True)) -> JSONResponse:
            """Scan local network — return devices with IP, MAC, hostname, manufacturer."""
            if svc._detector is None:
                raise HTTPException(503, "Not running")
            devices = await svc._detector.discover_network_devices(active=active)
            return JSONResponse({"devices": devices})

        # ── QR Invite flow ──────────────────────────────────────────────

        @router.post("/invite", status_code=201)
        async def create_invite(req: InviteRequest, request: Request) -> JSONResponse:
            """Create an invite link + QR code for a person to register their device."""
            if svc._detector is None:
                raise HTTPException(503, "Not running")
            invite = svc._detector.create_invite(req.name)
            # Build the join URL — prefer browser-provided origin over Host header
            if req.base_url:
                base = req.base_url.rstrip("/")
            else:
                host = request.headers.get("x-forwarded-host") or request.headers.get("host", "localhost")
                scheme = request.headers.get("x-forwarded-proto", "http")
                base = f"{scheme}://{host}"
            join_url = f"{base}/api/ui/modules/presence-detection/join/{invite['token']}"
            # Generate QR SVG
            qr_svg = svc._detector.generate_qr_svg(join_url)
            return JSONResponse({
                **invite,
                "join_url": join_url,
                "qr_svg": qr_svg,
            }, status_code=201)

        @router.get("/invite/{token}")
        async def get_invite_status(token: str) -> JSONResponse:
            if svc._detector is None:
                raise HTTPException(503, "Not running")
            invite = svc._detector.get_invite(token)
            if not invite:
                raise HTTPException(404, "Invite not found")
            return JSONResponse(invite)

        @router.get("/join/{token}", response_class=HTMLResponse)
        async def join_page(token: str, request: Request) -> HTMLResponse:
            """The page that opens when a person scans the QR code."""
            if svc._detector is None:
                raise HTTPException(503, "Not running")
            invite = svc._detector.get_invite(token)
            if not invite:
                return HTMLResponse(_join_error_html("Invite not found", "Запрошення не знайдено"), status_code=404)
            if invite["status"] == "expired":
                return HTMLResponse(_join_error_html("Invite expired", "Запрошення прострочене"), status_code=410)
            if invite["status"] == "completed":
                return HTMLResponse(_join_done_html(invite["name"]), status_code=200)

            # Capture device info from the request
            client_ip = _get_client_ip(request)
            user_agent = request.headers.get("user-agent", "")

            # Look up MAC from ARP table
            mac = ""
            arp = _read_arp_table()
            if client_ip in arp:
                mac = arp[client_ip]

            # Auto-complete the invite
            result = svc._detector.complete_invite(token, client_ip, mac, user_agent)
            if result:
                vapid_key = svc._detector.get_vapid_public_key() or ""
                return HTMLResponse(_join_success_html(
                    name=result["name"],
                    device_name=result["device_name"],
                    ip=result["ip"],
                    user_id=result["user_id"],
                    vapid_public_key=vapid_key,
                ))
            return HTMLResponse(_join_error_html("Registration failed", "Реєстрація не вдалася"), status_code=500)

        @router.get("/users/{user_id}/history")
        async def get_user_history(user_id: str, limit: int = Query(50, ge=1, le=500)) -> JSONResponse:
            if svc._detector is None:
                raise HTTPException(503, "Not running")
            history = svc._detector.get_user_history(user_id, limit=limit)
            return JSONResponse({"user_id": user_id, "history": history})

        @router.get("/invites")
        async def list_invites() -> JSONResponse:
            if svc._detector is None:
                raise HTTPException(503, "Not running")
            return JSONResponse({"invites": svc._detector.list_invites()})

        @router.get("/widget", response_class=HTMLResponse)
        async def widget() -> HTMLResponse:
            f = _MODULE_DIR / "widget.html"
            return HTMLResponse(f.read_text() if f.exists() else "<p>widget.html not found</p>")

        @router.get("/settings", response_class=HTMLResponse)
        async def settings_page() -> HTMLResponse:
            f = _MODULE_DIR / "settings.html"
            return HTMLResponse(f.read_text() if f.exists() else "<p>settings.html not found</p>")

        # ── PWA + Push endpoints ────────────────────────────────────

        @router.get("/pwa.webmanifest")
        async def pwa_manifest() -> Response:
            f = _MODULE_DIR / "pwa.webmanifest"
            content = f.read_text() if f.exists() else "{}"
            return Response(content=content, media_type="application/manifest+json")

        @router.get("/sw.js")
        async def service_worker() -> Response:
            f = _MODULE_DIR / "sw.js"
            content = f.read_text() if f.exists() else "// sw.js not found"
            return Response(
                content=content,
                media_type="application/javascript",
                headers={"Service-Worker-Allowed": "/"},
            )

        @router.get("/push/vapid-public-key")
        async def vapid_public_key() -> JSONResponse:
            if svc._detector is None:
                raise HTTPException(503, "Not running")
            key = svc._detector.get_vapid_public_key()
            return JSONResponse({"public_key": key or ""})

        @router.post("/push-subscribe", status_code=201)
        async def push_subscribe(req: PushSubscribeRequest) -> JSONResponse:
            if svc._detector is None:
                raise HTTPException(503, "Not running")
            svc._detector.save_push_subscription(
                user_id=req.user_id,
                endpoint=req.endpoint,
                p256dh=req.p256dh,
                auth=req.auth,
                user_agent=req.user_agent,
            )
            return JSONResponse({"status": "subscribed"}, status_code=201)

        @router.post("/push-test/{user_id}")
        async def push_test(user_id: str) -> JSONResponse:
            if svc._detector is None:
                raise HTTPException(503, "Not running")
            result = await svc._detector.send_push_to_user(
                user_id=user_id,
                title="Selena Test",
                body=f"Push notification test for {user_id}",
                data={"type": "test"},
            )
            return JSONResponse(result)

        @router.get("/push/subscriptions")
        async def list_push_subscriptions() -> JSONResponse:
            if svc._detector is None:
                raise HTTPException(503, "Not running")
            subs = svc._detector.get_all_push_subscriptions()
            return JSONResponse({"subscriptions": subs})

        return router


def _get_client_ip(request: Request) -> str:
    """Extract the real client IP from request (handles reverse proxy headers)."""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    real_ip = request.headers.get("x-real-ip", "")
    if real_ip:
        return real_ip
    if request.client:
        return request.client.host
    return ""


def json_escape(s: str) -> str:
    """Escape a string for safe embedding as a JS string literal in HTML."""
    import json as _json
    return _json.dumps(s)


def _join_success_html(name: str, device_name: str, ip: str, user_id: str = "", vapid_public_key: str = "") -> str:
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Selena — Welcome</title>
<link rel="manifest" href="./pwa.webmanifest">
<meta name="theme-color" content="#0B0C10">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #0B0C10; color: #EDEEF5; display: flex; justify-content: center; align-items: center; min-height: 100vh; margin: 0; padding: 20px; text-align: center; }}
.card {{ background: #12131A; border-radius: 16px; padding: 32px 24px; max-width: 400px; width: 100%; }}
.icon {{ font-size: 3rem; margin-bottom: 12px; }}
h1 {{ font-size: 1.25rem; margin: 0 0 6px; }}
p {{ color: #888EA8; font-size: 0.88rem; margin: 4px 0; }}
.tag {{ display: inline-block; background: rgba(46,201,138,0.15); color: #2EC98A; padding: 4px 12px; border-radius: 6px; font-size: 0.85rem; margin-top: 10px; }}
.step {{ display: none; }}
.step.active {{ display: block; }}
.btn {{ display: block; width: 100%; border: none; border-radius: 10px; padding: 14px; cursor: pointer; font-size: 1rem; font-weight: 600; margin-top: 16px; }}
.btn-primary {{ background: #4F8CF7; color: #fff; }}
.btn-green {{ background: #2EC98A; color: #fff; }}
.btn-sec {{ background: #20212C; color: #EDEEF5; }}
.btn:disabled {{ opacity: 0.4; cursor: default; }}
.instruction {{ background: #191A22; border-radius: 10px; padding: 16px; margin: 16px 0; text-align: left; font-size: 0.88rem; line-height: 1.6; color: #EDEEF5; }}
.instruction b {{ color: #4F8CF7; }}
.status-msg {{ font-size: 0.82rem; margin-top: 10px; min-height: 20px; }}
.status-msg.ok {{ color: #2EC98A; }}
.status-msg.err {{ color: #E05454; }}
.steps-indicator {{ display: flex; gap: 8px; justify-content: center; margin-bottom: 20px; }}
.steps-indicator .dot {{ width: 8px; height: 8px; border-radius: 50%; background: #20212C; }}
.steps-indicator .dot.active {{ background: #4F8CF7; }}
.steps-indicator .dot.done {{ background: #2EC98A; }}
</style></head><body>
<div class="card">
<div class="steps-indicator"><div class="dot active" id="sd1"></div><div class="dot" id="sd2"></div><div class="dot" id="sd3"></div></div>

<!-- Step 1: Registered -->
<div class="step active" id="step1">
<div class="icon">✅</div>
<h1>{name}</h1>
<p>{device_name} · {ip}</p>
<div class="tag">Device registered</div>
<button class="btn btn-primary" onclick="goStep(2)">Next →</button>
</div>

<!-- Step 2: Add to Home Screen -->
<div class="step" id="step2">
<div class="icon">📱</div>
<h1 id="a2hs-title">Add to Home Screen</h1>
<div class="instruction" id="a2hs-instruction"></div>
<button class="btn btn-primary" onclick="goStep(3)">Done, continue →</button>
<button class="btn btn-sec" onclick="goStep(3)" style="margin-top:8px;font-size:0.82rem">Skip</button>
</div>

<!-- Step 3: Enable push notifications -->
<div class="step" id="step3">
<div class="icon">🔔</div>
<h1 id="push-title">Enable Notifications</h1>
<p id="push-desc">Receive alerts when someone arrives or leaves home.</p>
<button class="btn btn-green" id="push-btn" onclick="enablePush()">Enable Notifications</button>
<div class="status-msg" id="push-status"></div>
</div>

<!-- Final -->
<div class="step" id="step4">
<div class="icon">🎉</div>
<h1>All set!</h1>
<p>You'll receive notifications from your smart home.</p>
<p style="margin-top:16px;font-size:0.8rem;color:#484D66">You can close this page now.<br>Можна закрити цю сторінку.</p>
</div>
</div>

<script>
var USER_ID = {json_escape(user_id)};
var VAPID_KEY = {json_escape(vapid_public_key)};
var BASE = window.location.pathname.replace(/\\/join\\/[^\\/]+$/, '');
var currentStep = 1;

function goStep(n) {{
  for (var i=1; i<=4; i++) {{
    var el = document.getElementById('step'+i);
    if (el) el.classList.toggle('active', i===n);
  }}
  for (var i=1; i<=3; i++) {{
    var d = document.getElementById('sd'+i);
    if (d) {{ d.className = 'dot' + (i<n ? ' done' : '') + (i===n ? ' active' : ''); }}
  }}
  currentStep = n;
}}

/* Step 2: detect platform and show instruction */
(function() {{
  var isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent);
  var el = document.getElementById('a2hs-instruction');
  if (isIOS) {{
    el.innerHTML = '1. Tap the <b>Share</b> button (square with arrow ↑) at the bottom of Safari<br>2. Scroll down and tap <b>"Add to Home Screen"</b><br>3. Tap <b>Add</b>';
    document.getElementById('a2hs-title').textContent = 'Add to Home Screen';
  }} else {{
    el.innerHTML = '1. Tap the <b>⋮ menu</b> (three dots) in the top-right corner<br>2. Tap <b>"Add to Home screen"</b> or <b>"Install app"</b><br>3. Tap <b>Add</b>';
    document.getElementById('a2hs-title').textContent = 'Add to Home Screen';
  }}
}})();

function urlBase64ToUint8Array(base64String) {{
  var padding = '='.repeat((4 - base64String.length % 4) % 4);
  var base64 = (base64String + padding).replace(/\\-/g, '+').replace(/_/g, '/');
  var rawData = window.atob(base64);
  var outputArray = new Uint8Array(rawData.length);
  for (var i = 0; i < rawData.length; ++i) {{
    outputArray[i] = rawData.charCodeAt(i);
  }}
  return outputArray;
}}

async function enablePush() {{
  var statusEl = document.getElementById('push-status');
  var btn = document.getElementById('push-btn');

  if (!('serviceWorker' in navigator) || !('PushManager' in window)) {{
    statusEl.textContent = 'Push notifications are not supported in this browser. Try opening from Home Screen (PWA).';
    statusEl.className = 'status-msg err';
    return;
  }}

  try {{
    btn.disabled = true;
    statusEl.textContent = 'Requesting permission...';
    statusEl.className = 'status-msg';

    var permission = await Notification.requestPermission();
    if (permission !== 'granted') {{
      statusEl.textContent = 'Notification permission denied. You can enable it in browser settings.';
      statusEl.className = 'status-msg err';
      btn.disabled = false;
      return;
    }}

    statusEl.textContent = 'Registering service worker...';
    var reg = await navigator.serviceWorker.register(BASE + '/sw.js', {{ scope: BASE + '/' }});
    await navigator.serviceWorker.ready;

    statusEl.textContent = 'Subscribing to push...';
    var subscription = await reg.pushManager.subscribe({{
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(VAPID_KEY)
    }});

    var subJson = subscription.toJSON();
    statusEl.textContent = 'Saving subscription...';
    var resp = await fetch(BASE + '/push-subscribe', {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify({{
        user_id: USER_ID,
        endpoint: subJson.endpoint,
        p256dh: subJson.keys.p256dh,
        auth: subJson.keys.auth,
        user_agent: navigator.userAgent
      }})
    }});

    if (resp.ok) {{
      goStep(4);
    }} else {{
      statusEl.textContent = 'Failed to save subscription (HTTP ' + resp.status + ')';
      statusEl.className = 'status-msg err';
      btn.disabled = false;
    }}
  }} catch(e) {{
    statusEl.textContent = 'Error: ' + (e.message || e);
    statusEl.className = 'status-msg err';
    btn.disabled = false;
  }}
}}
</script>
</body></html>"""


def _join_done_html(name: str) -> str:
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Selena — Already Registered</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #0B0C10; color: #EDEEF5; display: flex; justify-content: center; align-items: center; min-height: 100vh; margin: 0; padding: 20px; text-align: center; }}
.card {{ background: #12131A; border-radius: 16px; padding: 40px 32px; max-width: 380px; width: 100%; }}
.icon {{ font-size: 3rem; margin-bottom: 16px; }}
h1 {{ font-size: 1.3rem; margin: 0 0 8px; }}
p {{ color: #888EA8; font-size: 0.9rem; }}
</style></head><body>
<div class="card">
<div class="icon">\U0001F44D</div>
<h1>{name}</h1>
<p>This device is already registered.<br>Цей пристрій вже зареєстрований.</p>
</div></body></html>"""


def _join_error_html(msg_en: str, msg_uk: str) -> str:
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Selena — Error</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #0B0C10; color: #EDEEF5; display: flex; justify-content: center; align-items: center; min-height: 100vh; margin: 0; padding: 20px; text-align: center; }}
.card {{ background: #12131A; border-radius: 16px; padding: 40px 32px; max-width: 380px; width: 100%; }}
.icon {{ font-size: 3rem; margin-bottom: 16px; }}
h1 {{ font-size: 1.3rem; margin: 0 0 8px; color: #E05454; }}
p {{ color: #888EA8; font-size: 0.9rem; }}
</style></head><body>
<div class="card">
<div class="icon">❌</div>
<h1>{msg_en}</h1>
<p>{msg_uk}</p>
</div></body></html>"""
