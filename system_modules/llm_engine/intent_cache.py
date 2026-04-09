"""
system_modules/llm_engine/intent_cache.py — Simple SQLite intent cache.

Replaces SmartMatcher + AutoLearner with a straightforward cache:
  - LLM successfully classified → save (text, lang, intent, params, response)
  - Next identical request → return from cache (skip LLM)
  - Frequent phrases (5+ hits) → suggest admin to add to vocabulary YAML

No vectors, no embeddings, no ML. Just normalized text lookup.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class IntentCache:
    """Simple SQLite cache: normalized text+lang → intent."""

    def __init__(self, db_path: str | None = None) -> None:
        if db_path is None:
            data_dir = os.environ.get("CORE_DATA_DIR", "/var/lib/selena")
            db_path = os.path.join(data_dir, "intent_cache.db")
        self._db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS intent_cache (
                    key TEXT PRIMARY KEY,
                    text TEXT NOT NULL,
                    lang TEXT NOT NULL DEFAULT 'en',
                    intent TEXT NOT NULL,
                    params TEXT NOT NULL DEFAULT '{}',
                    response TEXT NOT NULL DEFAULT '',
                    hit_count INTEGER NOT NULL DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_hit_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_intent_cache_hits
                ON intent_cache(hit_count DESC)
            """)

    @staticmethod
    def _make_key(text: str, lang: str) -> str:
        return f"{lang}:{text.lower().strip()}"

    async def get(self, text: str, lang: str) -> dict[str, Any] | None:
        """Look up cached intent for text+lang. Returns dict or None."""
        key = self._make_key(text, lang)
        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT intent, params, response FROM intent_cache WHERE key = ?",
                    (key,),
                ).fetchone()
                if row:
                    # Update hit count
                    conn.execute(
                        "UPDATE intent_cache SET hit_count = hit_count + 1, last_hit_at = CURRENT_TIMESTAMP WHERE key = ?",
                        (key,),
                    )
                    return {
                        "intent": row["intent"],
                        "params": json.loads(row["params"]),
                        "response": row["response"],
                    }
        except Exception as e:
            logger.debug("IntentCache get error: %s", e)
        return None

    async def put(
        self, text: str, lang: str, intent: str,
        params: dict | None = None, response: str = "",
    ) -> None:
        """Cache a successful LLM classification."""
        key = self._make_key(text, lang)
        params_json = json.dumps(params or {}, ensure_ascii=False)
        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute("""
                    INSERT INTO intent_cache (key, text, lang, intent, params, response)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        intent = excluded.intent,
                        params = excluded.params,
                        response = excluded.response,
                        hit_count = hit_count + 1,
                        last_hit_at = CURRENT_TIMESTAMP
                """, (key, text.strip(), lang, intent, params_json, response))
        except Exception as e:
            logger.debug("IntentCache put error: %s", e)

    async def get_frequent(self, min_count: int = 5) -> list[dict[str, Any]]:
        """Get frequently cached phrases — candidates for vocabulary YAML."""
        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT text, lang, intent, params, hit_count FROM intent_cache "
                    "WHERE hit_count >= ? ORDER BY hit_count DESC LIMIT 50",
                    (min_count,),
                ).fetchall()
                return [
                    {
                        "text": r["text"],
                        "lang": r["lang"],
                        "intent": r["intent"],
                        "params": json.loads(r["params"]),
                        "hit_count": r["hit_count"],
                    }
                    for r in rows
                ]
        except Exception as e:
            logger.debug("IntentCache get_frequent error: %s", e)
            return []

    async def promote_frequent_to_patterns(
        self, *, min_hits: int = 5, session_factory=None,
    ) -> int:
        """Convert hot cache entries into auto_learned IntentPattern rows.

        Each promoted row uses ``source='auto_learned'`` and
        ``entity_ref='cache:promoted'`` so it can be cleanly evicted on
        the next promotion run without touching ``auto_entity`` rows
        owned by PatternGenerator. Subsequent voice utterances of the
        same phrase hit FastMatcher (~0 ms) instead of paying the local
        LLM round-trip.

        Returns the number of new pattern rows inserted.
        """
        if session_factory is None:
            return 0

        rows = await self.get_frequent(min_count=min_hits)
        if not rows:
            return 0

        from sqlalchemy import select, delete
        from core.registry.models import IntentDefinition, IntentPattern
        import re as _re

        promoted = 0
        async with session_factory() as session:
            async with session.begin():
                # Wipe previous auto_learned rows so we never accumulate
                # stale promotions when a phrase falls out of the hot set.
                await session.execute(
                    delete(IntentPattern).where(
                        IntentPattern.source == "auto_learned",
                        IntentPattern.entity_ref == "cache:promoted",
                    )
                )
                for row in rows:
                    intent_name = row["intent"]
                    text = (row["text"] or "").strip().lower()
                    lang = row["lang"]
                    if not text or intent_name in ("unknown", "llm.response"):
                        continue

                    idef = (await session.execute(
                        select(IntentDefinition).where(
                            IntentDefinition.intent == intent_name
                        )
                    )).scalar_one_or_none()
                    if idef is None:
                        continue

                    # Anchor the literal exactly so we don't accidentally
                    # match longer phrases. The trailing ``\??`` absorbs
                    # the optional question mark Whisper sometimes adds.
                    pattern = rf"^{_re.escape(text)}\??$"

                    session.add(IntentPattern(
                        intent_id=idef.id,
                        lang=lang,
                        pattern=pattern,
                        source="auto_learned",
                        entity_ref="cache:promoted",
                    ))
                    promoted += 1
        return promoted

    async def clear(self) -> int:
        """Clear all cached entries. Returns number deleted."""
        try:
            with sqlite3.connect(self._db_path) as conn:
                cursor = conn.execute("DELETE FROM intent_cache")
                return cursor.rowcount
        except Exception as e:
            logger.debug("IntentCache clear error: %s", e)
            return 0

    @property
    def count(self) -> int:
        try:
            with sqlite3.connect(self._db_path) as conn:
                row = conn.execute("SELECT COUNT(*) FROM intent_cache").fetchone()
                return row[0] if row else 0
        except Exception:
            return 0


# ── Singleton ────────────────────────────────────────────────────────────

_cache: IntentCache | None = None


def get_intent_cache() -> IntentCache:
    global _cache
    if _cache is None:
        _cache = IntentCache()
    return _cache
