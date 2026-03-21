"""
system_modules/notify_push/push.py — Web Push VAPID notification delivery

Sends browser push notifications using VAPID authentication (RFC 8292).
Uses the py-vapid + pywebpush libraries.

Endpoints are stored in /var/lib/selena/push_subscriptions.json
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SUBS_FILE = Path(os.environ.get("PUSH_SUBS_FILE", "/var/lib/selena/push_subscriptions.json"))
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "/secure/vapid_private.pem")
VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY", "")
VAPID_CLAIMS_SUB = os.environ.get("VAPID_CLAIMS_SUB", "mailto:admin@selena.local")


@dataclass
class PushSubscription:
    endpoint: str
    keys: dict[str, str]  # {"auth": "...", "p256dh": "..."}
    user_id: str = ""


class PushNotifier:
    def __init__(self) -> None:
        self._subs: list[PushSubscription] = []
        self._load_subs()

    def _load_subs(self) -> None:
        if SUBS_FILE.exists():
            try:
                data = json.loads(SUBS_FILE.read_text())
                self._subs = [PushSubscription(**s) for s in data]
            except Exception as exc:
                logger.warning("Failed to load push subscriptions: %s", exc)

    def _save_subs(self) -> None:
        SUBS_FILE.parent.mkdir(parents=True, exist_ok=True)
        SUBS_FILE.write_text(json.dumps([asdict(s) for s in self._subs], indent=2))

    def subscribe(self, subscription: PushSubscription) -> None:
        """Register a new push subscription (from browser)."""
        # Replace if same endpoint already registered
        self._subs = [s for s in self._subs if s.endpoint != subscription.endpoint]
        self._subs.append(subscription)
        self._save_subs()
        logger.info("Push subscription registered for user %s", subscription.user_id)

    def unsubscribe(self, endpoint: str) -> None:
        """Remove a push subscription."""
        self._subs = [s for s in self._subs if s.endpoint != endpoint]
        self._save_subs()

    def get_subscriptions(self, user_id: str | None = None) -> list[PushSubscription]:
        if user_id:
            return [s for s in self._subs if s.user_id == user_id]
        return list(self._subs)

    async def send(
        self,
        title: str,
        body: str,
        icon: str = "/icons/icon-192.png",
        user_id: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> int:
        """Send a push notification. Returns count of successful deliveries."""
        try:
            from pywebpush import webpush, WebPushException  # type: ignore
        except ImportError:
            logger.warning("pywebpush not installed. Run: pip install pywebpush")
            return 0

        payload = json.dumps({
            "title": title,
            "body": body,
            "icon": icon,
            "data": data or {},
        })

        subs = self.get_subscriptions(user_id)
        success = 0
        failed_endpoints = []

        for sub in subs:
            try:
                webpush(
                    subscription_info={"endpoint": sub.endpoint, "keys": sub.keys},
                    data=payload,
                    vapid_private_key=VAPID_PRIVATE_KEY,
                    vapid_claims={"sub": VAPID_CLAIMS_SUB},
                )
                success += 1
            except Exception as exc:
                logger.warning("Push delivery failed to %s: %s", sub.endpoint[:40], exc)
                # Remove expired/invalid subscriptions (HTTP 410)
                if "410" in str(exc) or "404" in str(exc):
                    failed_endpoints.append(sub.endpoint)

        for ep in failed_endpoints:
            self.unsubscribe(ep)

        logger.info("Push sent: %d/%d delivered", success, len(subs))
        return success


_notifier: PushNotifier | None = None


def get_push_notifier() -> PushNotifier:
    global _notifier
    if _notifier is None:
        _notifier = PushNotifier()
    return _notifier
