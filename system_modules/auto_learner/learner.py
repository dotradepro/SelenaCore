"""
system_modules/auto_learner/learner.py — Intent learning engine.

Subscribes to voice.intent events via EventBus.
When LLM successfully classifies an intent, stores the (raw_text, intent) pair
in JSONL for SmartMatcher to learn from.

Features:
- Deduplication: normalized text → _seen_keys set (persists across restarts)
- use_count tracking: repeated phrases increment counter instead of duplicating
- Nightly cleanup: removes entries older than 30 days with use_count <= 1
- Auto-confirmation: entries with use_count >= 3 are marked confirmed
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class Learner:
    """Intent learning from LLM results."""

    def __init__(self, data_dir: str | None = None) -> None:
        if data_dir is None:
            data_dir = os.path.join(
                os.environ.get("CORE_DATA_DIR", "/var/lib/selena"),
                "smart_matcher",
            )
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._learned_path = self._data_dir / "learned.jsonl"

        # Deduplication: normalized text → line index
        self._seen_keys: set[str] = set()
        self._load_seen_keys()

        # Known intents (set by setup)
        self._known_intents: set[str] = set()

    def set_known_intents(self, intents: list[str]) -> None:
        """Set the list of valid intent names for validation."""
        self._known_intents = set(intents)

    def on_voice_intent(self, payload: dict[str, Any]) -> bool:
        """Process a voice.intent event payload. Returns True if learned.

        Only learns from LLM results where:
        - source == "llm"
        - intent is a known registered intent (not "llm.response", not "unknown")
        - raw_text is non-empty
        """
        source = payload.get("source", "")
        intent = payload.get("intent", "")
        raw_text = payload.get("raw_text", "")

        # Only learn from LLM classifications
        if source != "llm":
            return False

        # Skip non-specific intents
        if intent in ("llm.response", "unknown", ""):
            return False

        # Skip if no raw text
        if not raw_text:
            return False

        # Validate intent against registered intents
        if self._known_intents and intent not in self._known_intents:
            logger.debug("AutoLearner: ignoring unknown intent %r", intent)
            return False

        # Normalize for deduplication
        key = raw_text.lower().strip()

        if key in self._seen_keys:
            # Already seen — increment use_count
            self._increment_use_count(key)
            return False

        # New example
        self._seen_keys.add(key)
        entry = {
            "key": key,
            "text": raw_text.strip(),
            "intent": intent,
            "params": payload.get("params", {}),
            "noun_class": payload.get("noun_class", ""),
            "verb": payload.get("verb", ""),
            "module": payload.get("module", ""),
            "source": "llm",
            "use_count": 1,
            "confirmed": False,
            "created_at": datetime.now().isoformat(),
        }
        self._append_jsonl(entry)
        logger.info(
            "AutoLearner: learned %r → %s",
            raw_text[:50], intent,
        )
        return True

    def nightly_cleanup(self) -> dict[str, int]:
        """Remove stale entries, auto-confirm frequent ones.

        Returns: {"confirmed": N, "deleted": N}
        """
        if not self._learned_path.exists():
            return {"confirmed": 0, "deleted": 0}

        cutoff = (datetime.now() - timedelta(days=30)).isoformat()
        lines = self._learned_path.read_text(encoding="utf-8").splitlines()
        kept: list[str] = []
        confirmed = 0
        deleted = 0

        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            created = entry.get("created_at", "")
            use_count = int(entry.get("use_count", 1))

            # Delete old entries with low usage
            if created < cutoff and use_count <= 1:
                key = entry.get("key", "")
                self._seen_keys.discard(key)
                deleted += 1
                continue

            # Auto-confirm high-usage entries
            if not entry.get("confirmed") and use_count >= 3:
                entry["confirmed"] = True
                confirmed += 1

            kept.append(json.dumps(entry, ensure_ascii=False))

        self._learned_path.write_text(
            "\n".join(kept) + ("\n" if kept else ""),
            encoding="utf-8",
        )

        if confirmed or deleted:
            logger.info(
                "AutoLearner nightly: confirmed=%d, deleted=%d, remaining=%d",
                confirmed, deleted, len(kept),
            )
        return {"confirmed": confirmed, "deleted": deleted}

    def get_stats(self) -> dict[str, Any]:
        """Return learner statistics."""
        if not self._learned_path.exists():
            return {"total": 0, "confirmed": 0, "unconfirmed": 0}

        total = 0
        confirmed_count = 0
        for line in self._learned_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
                total += 1
                if entry.get("confirmed"):
                    confirmed_count += 1
            except json.JSONDecodeError:
                continue

        return {
            "total": total,
            "confirmed": confirmed_count,
            "unconfirmed": total - confirmed_count,
        }

    def get_all_entries(self) -> list[dict[str, Any]]:
        """Return all learned entries for UI display."""
        if not self._learned_path.exists():
            return []

        entries: list[dict[str, Any]] = []
        for line in self._learned_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        # Most recent first
        entries.reverse()
        return entries

    def delete_entry(self, key: str) -> None:
        """Delete a learned entry by normalized key."""
        if not self._learned_path.exists():
            return

        lines = self._learned_path.read_text(encoding="utf-8").splitlines()
        kept = [
            line for line in lines
            if line.strip() and json.loads(line).get("key") != key
        ]
        self._learned_path.write_text(
            "\n".join(kept) + ("\n" if kept else ""),
            encoding="utf-8",
        )
        self._seen_keys.discard(key)

    # ── Internal ─────────────────────────────────────────────────────────

    def _load_seen_keys(self) -> None:
        """Restore _seen_keys from JSONL on startup."""
        if not self._learned_path.exists():
            return

        count = 0
        for line in self._learned_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                key = entry.get("key", "")
                if key:
                    self._seen_keys.add(key)
                    count += 1
            except (json.JSONDecodeError, KeyError):
                continue

        if count:
            logger.info("AutoLearner: restored %d seen keys from JSONL", count)

    def _append_jsonl(self, entry: dict[str, Any]) -> None:
        """Append a single entry to the JSONL file."""
        with open(self._learned_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _increment_use_count(self, key: str) -> None:
        """Increment use_count for an existing entry in JSONL."""
        if not self._learned_path.exists():
            return

        lines = self._learned_path.read_text(encoding="utf-8").splitlines()
        updated = False
        result: list[str] = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                if entry.get("key") == key and not updated:
                    entry["use_count"] = int(entry.get("use_count", 1)) + 1
                    updated = True
                result.append(json.dumps(entry, ensure_ascii=False))
            except json.JSONDecodeError:
                result.append(line)

        if updated:
            self._learned_path.write_text(
                "\n".join(result) + "\n",
                encoding="utf-8",
            )
