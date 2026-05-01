"""satellite-manager — ESP32 satellite speaker management.

Lifecycle:
  - BLE scan + Wi-Fi provisioning of new satellites
  - Persistent WebSocket transport carrying binary-framed audio/control
  - Relay of satellite mic PCM into voice-core via `satellite.*` events
  - Relay of TTS PCM back to satellites keyed by session_id
  - Device registry CRUD + UI widget + settings wizard
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from core.module_loader.system_module import SystemModule

from .audio_bridge import AudioBridge
from .ble_provisioner import BLEProvisioner
from .ota_manager import OTAManager
from .satellite_registry import SatelliteRegistry
from .ws_hub import SatelliteWSHub

if TYPE_CHECKING:
    from fastapi import APIRouter

logger = logging.getLogger(__name__)

HEARTBEAT_TICK_S = 60.0
HEARTBEAT_TIMEOUT_S = 90.0


class SatelliteManagerModule(SystemModule):
    name = "satellite-manager"

    def __init__(self) -> None:
        super().__init__()
        self._ble = BLEProvisioner()
        self._ota = OTAManager()
        self._registry: SatelliteRegistry | None = None
        self._audio_bridge: AudioBridge | None = None
        self._ws_hub: SatelliteWSHub | None = None
        self._heartbeat_task: asyncio.Task | None = None
        self._hub_secret: str | None = None

    async def start(self) -> None:
        assert self._session_factory is not None, "setup() must run before start()"
        assert self._bus is not None, "setup() must run before start()"

        from .auth import get_or_create_secret

        self._hub_secret = get_or_create_secret()
        self._registry = SatelliteRegistry(self._session_factory)
        self._audio_bridge = AudioBridge(bus=self._bus, source=self.name)
        self._ws_hub = SatelliteWSHub(
            registry=self._registry,
            audio_bridge=self._audio_bridge,
            hub_secret=self._hub_secret,
        )

        self.subscribe(
            ["satellite.tts_chunk", "satellite.tts_end", "satellite.state_change"],
            self._on_voice_core_event,
        )

        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        logger.info("satellite-manager started")

    async def stop(self) -> None:
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except (asyncio.CancelledError, Exception):
                pass
            self._heartbeat_task = None
        if self._ws_hub:
            await self._ws_hub.close_all()
        self._cleanup_subscriptions()
        logger.info("satellite-manager stopped")

    def get_router(self) -> "APIRouter":
        from fastapi import APIRouter
        router = APIRouter()

        # BLE
        router.add_api_route("/ble/scan", self._api_ble_scan, methods=["POST"])
        router.add_api_route("/ble/provision", self._api_ble_provision, methods=["POST"])

        # Satellites CRUD
        router.add_api_route("/satellites", self._api_list, methods=["GET"])
        router.add_api_route("/satellites/{device_id}", self._api_update, methods=["PATCH"])
        router.add_api_route("/satellites/{device_id}", self._api_delete, methods=["DELETE"])

        # Test
        router.add_api_route(
            "/satellites/{device_id}/test/mic", self._api_test_mic, methods=["POST"],
        )
        router.add_api_route(
            "/satellites/{device_id}/test/speaker", self._api_test_speaker, methods=["POST"],
        )

        # OTA (stub)
        router.add_api_route("/ota/upload", self._api_ota_upload, methods=["POST"])
        router.add_api_route("/ota/latest", self._api_ota_latest, methods=["GET"])

        # Dashboard V2 metric template — count of online satellites
        router.add_api_route("/widget/data/state", self._widget_state, methods=["GET"])

        # WebSocket for ESP32
        assert self._ws_hub is not None
        router.add_api_websocket_route("/ws", self._ws_hub.handle_connection)

        self._register_html_routes(router, __file__)
        self._register_health_endpoint(router)
        return router

    # ── EventBus handler ─────────────────────────────────────────

    async def _on_voice_core_event(self, event: Any) -> None:
        """Relay TTS / state events from voice-core back to the correct satellite."""
        if self._ws_hub is None:
            return
        session_id = event.payload.get("session_id")
        if not session_id:
            return

        if event.type == "satellite.tts_chunk":
            await self._ws_hub.send_tts_chunk(
                session_id,
                event.payload.get("pcm_data", b""),
                sample_rate=event.payload.get("sample_rate"),
            )
        elif event.type == "satellite.tts_end":
            await self._ws_hub.send_tts_end(
                session_id,
                keep_session_open=bool(event.payload.get("keep_session_open", False)),
            )
        elif event.type == "satellite.state_change":
            state = event.payload.get("state")
            if state:
                await self._ws_hub.send_state(session_id, state)

    # ── Heartbeat ────────────────────────────────────────────────

    async def _heartbeat_loop(self) -> None:
        """Mark satellites offline when their heartbeat ages past the threshold."""
        try:
            while True:
                await asyncio.sleep(HEARTBEAT_TICK_S)
                if self._ws_hub is None or self._registry is None:
                    continue
                stale = self._ws_hub.get_stale_sessions(timeout_s=HEARTBEAT_TIMEOUT_S)
                for session in stale:
                    await self._ws_hub.drop_session(session.device_id)
                    await self._registry.set_online(session.device_id, False)
                    await self.publish("satellite.offline", {
                        "device_id": session.device_id,
                        "location": session.location,
                    })
                    logger.info(
                        "Satellite offline (stale): %s (location=%s)",
                        session.device_id, session.location,
                    )
        except asyncio.CancelledError:
            raise

    # ── REST API handlers ────────────────────────────────────────
    #
    # Kept thin — the heavy lifting lives in BLEProvisioner / SatelliteWSHub /
    # SatelliteRegistry / OTAManager. These handlers just adapt HTTP in/out.

    async def _api_ble_scan(self, timeout: float = 10.0) -> dict:
        satellites = await self._ble.scan(timeout=timeout)
        return {"satellites": satellites}

    async def _api_ble_provision(self, body: dict) -> dict:
        """Provision a satellite via BLE → Wi-Fi → WebSocket.

        Body keys: mac (required), wifi_ssid, wifi_pass, name, location.
        """
        from fastapi import HTTPException

        mac = body.get("mac")
        if not mac:
            raise HTTPException(400, "mac required")

        from .satellite_registry import device_id_for_mac
        device_id = device_id_for_mac(mac)

        assert self._hub_secret is not None
        from .auth import issue_token
        token = issue_token(device_id, self._hub_secret)

        hub_url = body.get("hub_url") or self._guess_hub_ws_url()
        if not hub_url:
            raise HTTPException(
                400,
                "Cannot determine hub LAN IP. Set network.lan_ip in config or "
                "pass hub_url in the request body.",
            )

        ip = await self._ble.provision(
            mac=mac,
            wifi_ssid=body.get("wifi_ssid", ""),
            wifi_pass=body.get("wifi_pass", ""),
            hub_url=hub_url,
            device_token=token,
        )
        if not ip:
            return {"status": "failed", "error": "Wi-Fi provisioning timeout"}

        assert self._registry is not None
        result = await self._registry.register(
            mac=mac,
            firmware=body.get("firmware", "unknown"),
            hardware=body.get("hardware", "esp32_audio_kit"),
            capabilities=body.get("capabilities", ["mic_stereo", "speaker_stereo", "buttons_6"]),
            ip=ip,
        )

        updates = {}
        if body.get("name"):
            updates["name"] = body["name"]
        if body.get("location"):
            updates["location"] = body["location"]
        if updates:
            await self._registry.update(result["device_id"], **updates)

        await self.publish("satellite.registered", {
            "device_id": result["device_id"],
            "location": updates.get("location", result["location"]),
        })

        return {"status": "ok", "device_id": result["device_id"], "ip": ip}

    async def _api_list(self) -> dict:
        assert self._registry is not None
        from core.registry.service import DeviceRegistry
        assert self._session_factory is not None

        satellites = await self._registry.list_all()
        async with self._session_factory() as session:
            reg = DeviceRegistry(session)
            locations = await reg.get_locations()
        return {"satellites": satellites, "locations": locations}

    async def _widget_state(self) -> dict:
        """Dashboard V2 metric — online / total satellites."""
        if self._registry is None:
            return {
                "label": "Satellites",
                "label_key": "widgets.satelliteManager.label",
                "value": "—",
                "tone": "neutral",
                "icon": "satellite",
            }
        sats = await self._registry.list_all()
        online = sum(1 for s in sats if (s.get("state") or {}).get("online"))
        total = len(sats)
        if total == 0:
            return {
                "label": "Satellites",
                "label_key": "widgets.satelliteManager.label",
                "value": "0",
                "trend": {
                    "direction": "flat",
                    "magnitude": "none",
                    "period": "registered",
                    "period_key": "widgets.satelliteManager.periodRegistered",
                },
                "tone": "neutral",
                "icon": "satellite",
            }
        offline = total - online
        tone = "ok" if offline == 0 else "warn"
        trend = None
        if offline > 0:
            trend = {
                "direction": "down",
                "magnitude": f"-{offline}",
                "period": "offline",
                "period_key": "widgets.satelliteManager.periodOffline",
            }
        return {
            "label": "Satellites",
            "label_key": "widgets.satelliteManager.label",
            "value": str(online),
            "unit": f"of {total}",
            "trend": trend,
            "tone": tone,
            "icon": "satellite",
        }

    async def _api_update(self, device_id: str, body: dict) -> dict:
        from fastapi import HTTPException
        assert self._registry is not None

        ok = await self._registry.update(device_id, **body)
        if not ok:
            raise HTTPException(404, "Satellite not found")

        # Push config update to ESP32 if connected
        if self._ws_hub is not None:
            await self._ws_hub.push_config(device_id, body)

        return {"status": "ok"}

    async def _api_delete(self, device_id: str) -> dict:
        from fastapi import HTTPException
        assert self._registry is not None

        if self._ws_hub is not None:
            await self._ws_hub.drop_session(device_id)

        ok = await self._registry.delete(device_id)
        if not ok:
            raise HTTPException(404, "Satellite not found")
        return {"status": "deleted"}

    async def _api_test_mic(self, device_id: str, timeout: float = 15.0) -> dict:
        """Arm a mic-test intercept, wait for the user to say the wake word,
        capture the resulting audio burst and report diagnostics.

        The user is expected to speak the wake phrase within `timeout`
        seconds after calling this endpoint. Returns samples, duration_ms,
        RMS energy, and current RSSI — enough to diagnose "is the mic
        working and is the satellite on a stable Wi-Fi link".
        """
        from fastapi import HTTPException
        if self._ws_hub is None or not self._ws_hub.is_online(device_id):
            raise HTTPException(404, "Satellite offline")
        # Clamp to a sane range so a runaway UI can't hold a session hostage.
        timeout = max(3.0, min(timeout, 30.0))
        return await self._ws_hub.arm_mic_test(device_id, timeout_s=timeout)

    async def _api_test_speaker(self, device_id: str, body: dict | None = None) -> dict:
        from fastapi import HTTPException
        if self._ws_hub is None or not self._ws_hub.is_online(device_id):
            raise HTTPException(404, "Satellite offline")
        text = (body or {}).get("text", "Тестова фраза")
        await self.publish("voice.speak", {"text": text, "target_device": device_id})
        return {"status": "ok"}

    async def _api_ota_upload(self, body: dict) -> dict:
        return await self._ota.upload(body)

    async def _api_ota_latest(self) -> dict:
        return self._ota.latest()

    # ── Helpers ──────────────────────────────────────────────────

    def _guess_hub_ws_url(self) -> str | None:
        """Best-effort LAN IP of the hub for first-run BLE provisioning.

        Tries, in order:
          1. core.yaml -> network.lan_ip (user-configured override)
          2. UDP socket trick against 8.8.8.8 (works if DNS is reachable)
          3. Iterate interfaces via ``ip -4 addr`` and pick the first
             non-loopback, non-docker IPv4 with a /prefix.

        Returns None if we couldn't find a usable LAN IP — the caller MUST
        fail the provisioning rather than shipping 127.0.0.1 to the ESP32.
        """
        import socket
        # 1. Configured override wins
        try:
            from core.config_writer import read_config
            cfg = read_config()
            configured = (cfg.get("network", {}) or {}).get("lan_ip")
            if configured:
                return f"ws://{configured}:80/api/ui/modules/satellite-manager/ws"
        except Exception:
            pass

        # 2. Ask the kernel which IP it would use to reach the outside world
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 53))
            ip = s.getsockname()[0]
            s.close()
            if ip and not ip.startswith("127."):
                return f"ws://{ip}:80/api/ui/modules/satellite-manager/ws"
        except Exception:
            pass

        # 3. Offline network: walk interfaces, skip loopback/docker bridges
        try:
            import subprocess
            out = subprocess.run(
                ["ip", "-4", "-o", "addr", "show"],
                capture_output=True, text=True, timeout=3,
            )
            for line in out.stdout.splitlines():
                parts = line.split()
                if len(parts) < 4:
                    continue
                iface, addr = parts[1], parts[3].split("/")[0]
                if iface == "lo" or iface.startswith(("docker", "br-", "veth")):
                    continue
                if addr.startswith("127."):
                    continue
                return f"ws://{addr}:80/api/ui/modules/satellite-manager/ws"
        except Exception:
            pass

        return None
