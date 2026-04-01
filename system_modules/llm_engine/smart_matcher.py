"""
system_modules/llm_engine/smart_matcher.py — TF-IDF vector similarity for intent matching (Tier 1.7).

Sits between Tier 1.5 (compiled regex) and Tier 2 (Module Bus) in IntentRouter.
Uses scikit-learn TfidfVectorizer + cosine similarity to catch near-miss utterances
that regex patterns miss but are semantically close to known intents.

Features:
- noun_class pre-filtering: only compare against intents of the same semantic class
- Two-threshold scoring: confident (>= 0.60) vs uncertain (0.45-0.60)
- Learnable: add_example() + batched background rebuild every 5 minutes
- Persistent: saves learned examples to JSONL file
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class _IntentEntry:
    """Single entry in the TF-IDF index."""

    intent: str
    module: str
    noun_class: str
    verb: str
    description: str
    source: str  # "compiled" | "seed" | "llm"


class SmartMatcher:
    """TF-IDF + cosine similarity intent matcher with noun_class filtering."""

    THRESHOLD_CONFIDENT = 0.55  # confident match — stop routing
    THRESHOLD_MIN = 0.46  # minimum match — mark as uncertain

    def __init__(self, data_dir: str | None = None) -> None:
        if data_dir is None:
            data_dir = os.path.join(
                os.environ.get("CORE_DATA_DIR", "/var/lib/selena"),
                "smart_matcher",
            )
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)

        self._learned_path = self._data_dir / "learned.jsonl"

        # TF-IDF state
        self._vectorizer: Any = None  # TfidfVectorizer
        self._matrix: Any = None  # sparse matrix
        self._entries: list[_IntentEntry] = []
        self._corpus: list[str] = []

        # noun_class → list of indices in _entries
        self._nc_index: dict[str, list[int]] = {}

        # Dirty flag for batched rebuild
        self._dirty = False
        self._built = False

    # ── Public API ───────────────────────────────────────────────────────

    def build_index(
        self,
        intents: list[Any],
        definitions: list[Any],
    ) -> None:
        """Build TF-IDF index from compiled intents + definitions.

        Args:
            intents: list of SystemIntentEntry from IntentCompiler
            definitions: list of IntentDefinition from IntentCompiler
        """
        from sklearn.feature_extraction.text import TfidfVectorizer

        self._entries = []
        self._corpus = []

        # Build corpus from compiled intents
        defn_map = {d.name: d for d in definitions}
        for entry in intents:
            defn = defn_map.get(entry.intent)
            noun_class = defn.noun_class if defn else "UNKNOWN"
            verb = defn.verb if defn else "UNKNOWN"
            description = entry.description or defn.description if defn else ""

            # Corpus text: combine description + pattern keywords
            keywords = self._extract_keywords_from_patterns(entry.patterns)
            text = f"{description} {keywords}".strip().lower()

            self._entries.append(_IntentEntry(
                intent=entry.intent,
                module=entry.module,
                noun_class=noun_class,
                verb=verb,
                description=description,
                source="compiled",
            ))
            self._corpus.append(text)

        # Load learned examples
        self._load_learned_examples()

        # Build TF-IDF matrix
        if not self._corpus:
            logger.warning("SmartMatcher: empty corpus, index not built")
            return

        self._vectorizer = TfidfVectorizer(
            analyzer="word",
            ngram_range=(1, 2),
            max_features=5000,
            sublinear_tf=True,
        )
        self._matrix = self._vectorizer.fit_transform(self._corpus)
        self._rebuild_nc_index()
        self._built = True

        logger.info(
            "SmartMatcher: index built with %d entries (%d compiled, %d learned)",
            len(self._entries),
            sum(1 for e in self._entries if e.source == "compiled"),
            sum(1 for e in self._entries if e.source != "compiled"),
        )

    def match(
        self,
        text: str,
        struct: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Find best matching intent via cosine similarity.

        Args:
            text: user utterance
            struct: output of extract_structure() — used for noun_class filtering

        Returns:
            {"intent": "...", "score": 0.72, "uncertain": True/False,
             "module": "...", "noun_class": "...", "params": {}}
            or None if score < THRESHOLD_MIN
        """
        if not self._built or self._vectorizer is None:
            return None

        from sklearn.metrics.pairwise import cosine_similarity

        query_vec = self._vectorizer.transform([text.lower().strip()])

        # Determine candidate indices (noun_class filtering)
        candidate_indices = self._get_candidates(struct)

        if not candidate_indices:
            return None

        # Compute similarities only against candidates
        candidate_matrix = self._matrix[candidate_indices]
        similarities = cosine_similarity(query_vec, candidate_matrix)[0]

        best_local_idx = int(np.argmax(similarities))
        best_score = float(similarities[best_local_idx])

        if best_score < self.THRESHOLD_MIN:
            return None

        best_entry = self._entries[candidate_indices[best_local_idx]]

        return {
            "intent": best_entry.intent,
            "module": best_entry.module,
            "noun_class": best_entry.noun_class,
            "score": round(best_score, 3),
            "uncertain": best_score < self.THRESHOLD_CONFIDENT,
            "source": "smart_matcher",
            "params": {},
        }

    def add_example(
        self,
        text: str,
        intent: str,
        metadata: dict[str, Any],
    ) -> None:
        """Add a learned example. Does NOT rebuild index immediately.

        Call rebuild() or wait for _background_rebuild_loop() to pick it up.
        """
        entry = _IntentEntry(
            intent=intent,
            module=metadata.get("module", ""),
            noun_class=metadata.get("noun_class", "UNKNOWN"),
            verb=metadata.get("verb", "UNKNOWN"),
            description=text,
            source=metadata.get("source", "llm"),
        )
        self._entries.append(entry)
        self._corpus.append(text.lower().strip())
        self._dirty = True

    def rebuild(self) -> None:
        """Rebuild TF-IDF index from current entries + corpus."""
        if not self._corpus:
            return

        from sklearn.feature_extraction.text import TfidfVectorizer

        self._vectorizer = TfidfVectorizer(
            analyzer="word",
            ngram_range=(1, 2),
            max_features=5000,
            sublinear_tf=True,
        )
        self._matrix = self._vectorizer.fit_transform(self._corpus)
        self._rebuild_nc_index()
        self._dirty = False
        self._built = True
        logger.info("SmartMatcher: index rebuilt with %d entries", len(self._entries))

    async def background_rebuild_loop(self) -> None:
        """Run every 5 minutes; rebuild index if dirty."""
        while True:
            await asyncio.sleep(300)
            if self._dirty:
                try:
                    self.rebuild()
                except Exception as exc:
                    logger.error("SmartMatcher rebuild error: %s", exc)

    @property
    def entry_count(self) -> int:
        return len(self._entries)

    @property
    def is_built(self) -> bool:
        return self._built

    # ── Internal ─────────────────────────────────────────────────────────

    def _get_candidates(self, struct: dict[str, Any] | None) -> list[int]:
        """Return candidate indices filtered by noun_class if available."""
        if struct and struct.get("noun_class", "UNKNOWN") != "UNKNOWN":
            nc = struct["noun_class"]
            candidates = self._nc_index.get(nc, [])
            if candidates:
                return candidates
        # Fallback: all entries
        return list(range(len(self._entries)))

    def _rebuild_nc_index(self) -> None:
        """Rebuild noun_class → indices mapping."""
        self._nc_index = {}
        for i, entry in enumerate(self._entries):
            self._nc_index.setdefault(entry.noun_class, []).append(i)

    def _load_learned_examples(self) -> None:
        """Load learned examples from JSONL file into corpus."""
        if not self._learned_path.exists():
            return

        count = 0
        for line in self._learned_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                self._entries.append(_IntentEntry(
                    intent=data.get("intent", ""),
                    module=data.get("module", ""),
                    noun_class=data.get("noun_class", "UNKNOWN"),
                    verb=data.get("verb", "UNKNOWN"),
                    description=data.get("text", ""),
                    source=data.get("source", "llm"),
                ))
                self._corpus.append(data.get("text", "").lower().strip())
                count += 1
            except (json.JSONDecodeError, KeyError):
                continue

        if count:
            logger.info("SmartMatcher: loaded %d learned examples from JSONL", count)

    @staticmethod
    def _extract_keywords_from_patterns(
        patterns: dict[str, list[str]],
    ) -> str:
        """Extract human-readable keywords from regex patterns."""
        import re

        words: list[str] = []
        for lang_patterns in patterns.values():
            for pattern in lang_patterns:
                # Remove regex syntax, keep words
                cleaned = re.sub(r"[\\()?+*\[\]{}|^$.]", " ", pattern)
                cleaned = re.sub(r"\b[sdwWbB]\b", "", cleaned)  # remove \s \d etc
                cleaned = re.sub(r"\s+", " ", cleaned).strip()
                for word in cleaned.split():
                    if len(word) >= 2 and word.isalpha():
                        words.append(word.lower())
        return " ".join(set(words))


# ── Singleton ────────────────────────────────────────────────────────────

_matcher: SmartMatcher | None = None


def get_smart_matcher() -> SmartMatcher:
    global _matcher
    if _matcher is None:
        _matcher = SmartMatcher()
    return _matcher
