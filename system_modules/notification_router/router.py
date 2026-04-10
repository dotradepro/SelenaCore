"""
system_modules/notification_router/router.py — NotificationRouter business logic

Channels supported (configured by caller):
  tts       — POST text to TTS module endpoint
  telegram  — POST message via Telegram Bot API
  push      — POST Web Push via notify_push module
  webhook   — POST JSON payload to arbitrary HTTP endpoint

Routing rules:
  Each rule has: priority (int), event_types (list), channel, filter (optional)
  Rules are sorted by priority (lower = higher priority).
  A notification can be routed to multiple channels if multiple rules match.

Public API:
  send(message, level, tags)  — route a notification manually
  handle_event(event_type, payload)  — process an incoming event  
  add_channel(name, config)   — register a delivery channel
  add_rule(rule)              — add a routing rule
  get_status()                — summary stats
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

VALID_CHANNELS = {"tts", "telegram", "push", "webhook"}
VALID_LEVELS = {"info", "warning", "critical"}

# Max notifications kept in history
HISTORY_MAX = 200

_CONFIG_FILE = Path(os.environ.get("NOTIFY_CONFIG_FILE", "/var/lib/selena/notification_router.json"))


class NotificationRouter:
    def __init__(self, publish_event_cb: Any) -> None:
        self._publish = publish_event_cb
        self._channels: dict[str, dict] = {}   # name → config
        self._rules: list[dict] = []           # sorted by priority
        self._history: deque = deque(maxlen=HISTORY_MAX)
        self._stats: dict[str, int] = {}       # channel → sent count
        self._load_config()

    # ── Persistence ──────────────────────────────────────────────────────────

    def _load_config(self) -> None:
        if _CONFIG_FILE.exists():
            try:
                data = json.loads(_CONFIG_FILE.read_text())
                for name, config in data.get("channels", {}).items():
                    if name in VALID_CHANNELS:
                        self._channels[name] = config
                        self._stats.setdefault(name, 0)
                for rule in data.get("rules", []):
                    self._rules.append(rule)
                self._rules.sort(key=lambda r: r.get("priority", 100))
                logger.info("Loaded %d channels, %d rules from %s",
                            len(self._channels), len(self._rules), _CONFIG_FILE)
            except Exception as exc:
                logger.warning("Failed to load notification config: %s", exc)

    def _save_config(self) -> None:
        try:
            _CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "channels": self._channels,
                "rules": self._rules,
            }
            _CONFIG_FILE.write_text(json.dumps(data, indent=2))
        except Exception as exc:
            logger.warning("Failed to save notification config: %s", exc)

    # ── Channel management ───────────────────────────────────────────────────

    def add_channel(self, name: str, config: dict) -> None:
        if name not in VALID_CHANNELS:
            raise ValueError(f"Unknown channel: {name}. Valid: {sorted(VALID_CHANNELS)}")
        self._channels[name] = dict(config)
        if name not in self._stats:
            self._stats[name] = 0
        self._save_config()

    def remove_channel(self, name: str) -> bool:
        if name in self._channels:
            del self._channels[name]
            self._save_config()
            return True
        return False

    def get_channels(self) -> dict[str, dict]:
        return {k: {**v, "stats": self._stats.get(k, 0)} for k, v in self._channels.items()}

    # ── Rule management ──────────────────────────────────────────────────────

    def add_rule(self, rule: dict) -> str:
        """Add a routing rule. Returns rule_id."""
        import uuid
        rule_id = rule.get("rule_id") or str(uuid.uuid4())
        entry = {
            "rule_id": rule_id,
            "priority": int(rule.get("priority", 100)),
            "event_types": rule.get("event_types", []),  # [] = match all
            "channel": rule["channel"],
            "level": rule.get("level"),       # None = match all levels
            "tags": rule.get("tags", []),     # [] = match all
            "enabled": rule.get("enabled", True),
        }
        # Remove existing rule with same id
        self._rules = [r for r in self._rules if r["rule_id"] != rule_id]
        self._rules.append(entry)
        self._rules.sort(key=lambda r: r["priority"])
        self._save_config()
        return rule_id

    def remove_rule(self, rule_id: str) -> bool:
        before = len(self._rules)
        self._rules = [r for r in self._rules if r["rule_id"] != rule_id]
        removed = len(self._rules) < before
        if removed:
            self._save_config()
        return removed

    def get_rules(self) -> list[dict]:
        return list(self._rules)

    # ── Send notification ────────────────────────────────────────────────────

    async def send(
        self,
        message: str,
        level: str = "info",
        tags: list[str] | None = None,
        event_type: str | None = None,
    ) -> list[str]:
        """Route a notification. Returns list of channels that were attempted."""
        if level not in VALID_LEVELS:
            level = "info"
        tags = tags or []
        attempted: list[str] = []

        entry = {
            "message": message,
            "level": level,
            "tags": tags,
            "event_type": event_type,
            "ts": datetime.now(tz=timezone.utc).isoformat(),
            "channels": [],
        }

        matched_channels: set[str] = set()
        for rule in self._rules:
            if not rule["enabled"]:
                continue
            if rule["channel"] not in self._channels:
                continue
            # Match event_type
            if rule["event_types"] and event_type not in rule["event_types"]:
                continue
            # Match level
            if rule["level"] and rule["level"] != level:
                continue
            # Match tags
            if rule["tags"] and not any(t in tags for t in rule["tags"]):
                continue
            matched_channels.add(rule["channel"])

        for ch_name in matched_channels:
            config = self._channels[ch_name]
            success = await self._deliver(ch_name, config, message, level, entry)
            if success:
                self._stats[ch_name] = self._stats.get(ch_name, 0) + 1
            attempted.append(ch_name)
            entry["channels"].append(ch_name)

        self._history.append(entry)

        # Publish to EventBus → SyncBridge → frontend WebSocket
        try:
            await self._publish("notification.sent", entry)
        except Exception:
            pass

        return attempted

    async def handle_event(self, event_type: str, payload: dict) -> list[str]:
        """Handle an incoming event and route if matching rules exist."""
        message = payload.get("message") or f"Event: {event_type}"
        level = payload.get("level", "info")
        tags = payload.get("tags", [])
        return await self.send(message, level=level, tags=tags, event_type=event_type)

    # ── Delivery ──────────────────────────────────────────────────────────────

    async def _deliver(
        self, channel: str, config: dict, message: str, level: str, entry: dict
    ) -> bool:
        try:
            if channel == "tts":
                return await self._deliver_tts(config, message)
            elif channel == "telegram":
                return await self._deliver_telegram(config, message)
            elif channel == "push":
                return await self._deliver_push(config, message, level)
            elif channel == "webhook":
                return await self._deliver_webhook(config, message, level, entry)
            return False
        except Exception as exc:
            logger.error("Delivery failed for channel %s: %s", channel, exc)
            return False

    async def _deliver_tts(self, config: dict, message: str) -> bool:
        url = config.get("tts_url", "http://localhost/api/tts/say")
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json={"text": message})
            return resp.status_code < 300

    async def _deliver_telegram(self, config: dict, message: str) -> bool:
        token = config.get("bot_token", "")
        chat_id = config.get("chat_id", "")
        if not token or not chat_id:
            logger.warning("Telegram channel missing bot_token or chat_id")
            return False
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json={"chat_id": chat_id, "text": message})
            return resp.status_code == 200

    _LEVEL_EMOJI = {"info": "ℹ️", "warning": "⚠️", "critical": "🚨"}

    async def _deliver_push(self, config: dict, message: str, level: str) -> bool:
        push_url = config.get(
            "push_url",
            "http://localhost/api/ui/modules/presence-detection/push/send",
        )
        user_id = config.get("user_id")  # None = send to all
        emoji = self._LEVEL_EMOJI.get(level, "ℹ️")
        payload: dict = {
            "title": f"{emoji} Selena — {level.upper()}",
            "body": message,
            "data": {"level": level, "tag": f"selena-{level}"},
        }
        if user_id:
            payload["user_id"] = user_id
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(push_url, json=payload)
            return resp.status_code < 300

    async def _deliver_webhook(self, config: dict, message: str, level: str, entry: dict) -> bool:
        url = config.get("url", "")
        if not url:
            return False
        payload = {
            "message": message,
            "level": level,
            "ts": entry.get("ts"),
            "event_type": entry.get("event_type"),
            "tags": entry.get("tags", []),
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload)
            return resp.status_code < 300

    # ── Status / History ──────────────────────────────────────────────────────

    def get_history(self, limit: int = 50) -> list[dict]:
        items = list(self._history)
        return items[-limit:]

    def get_status(self) -> dict[str, Any]:
        return {
            "channels": len(self._channels),
            "rules": len(self._rules),
            "total_sent": sum(self._stats.values()),
            "stats_by_channel": dict(self._stats),
        }
