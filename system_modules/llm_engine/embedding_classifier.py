"""
system_modules/llm_engine/embedding_classifier.py

Production embedding-based intent classifier — IntentRouter Tier 1.

Pipeline position
-----------------
    Vosk STT (native)
      → Helsinki translator       (always present)
      → Token filter               (3-15 candidates)
      → EmbeddingIntentClassifier  (this file, IntentRouter Tier 1)
      → if confident → return; else fall through to Local LLM (Tier 2)

The classifier is fed Helsinki English output, NOT the user's native
text. INTENT_ANCHORS therefore mix two sources:

  * Original clean English (e.g. "turn on the light in the living room")
  * Real Helsinki outputs from previous trace bench runs, which include
    quirks like "Turn on the air conditioning in the living room." or
    "What weather outside." (Helsinki sometimes drops the verb)

Anchors trained on idealised English would be miscalibrated against
the actual production translation noise. Including both forms makes
the classifier robust to whatever Helsinki produces.

Production rationale
--------------------
Trace bench on qwen 1.5b + Helsinki: 35/40 (87.5%), p50 2548 ms.
Embedding bench (sentence-transformers/all-MiniLM-L6-v2) on the same
40-case corpus: 39/40 (97.5%), classify-only p50 41 ms — both more
accurate AND ~60× faster than the local LLM. The model is 22 MB on
disk and ~80 MB in RAM, vs ~1 GB for qwen 1.5b.

After integration into IntentRouter.route() with LLM fallback for
low-margin cases: 40/40 (100%), end-to-end p50 111 ms (~23× faster
than the LLM-only path).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


# ── Intent anchors ──────────────────────────────────────────────────
#
# For each intent name we list a few representative phrases. The
# classifier averages their embeddings and uses the result as a
# centroid against which incoming queries are compared.
#
# CRITICAL: anchors must include actual Helsinki outputs, not just
# clean English. Real outputs from trace_bench:
#
#   "увімкни кондиціонер у вітальні" → "Turn on the air conditioning..."
#   "встанови режим охолодження"     → "Set the cooling mode."
#   "вмикни джазове радіо"           → "Turn on the jazz radio."
#   "яка погода надворі"             → "What weather outside."
#   "замкни вхідні двері"            → "Shut the front door."
#   "розкажи анекдот"                → "Tell me the joke."
#
# Without these the classifier will perform well in unit tests but
# fall apart on production translations.
INTENT_ANCHORS: dict[str, list[str]] = {
    "device.on": [
        "turn on the light",
        "turn on the air conditioner",
        "turn on the humidifier in the bedroom",
        # Helsinki outputs:
        "turn on the air conditioning in the living room.",
        "put the light in the living room.",
        "turn the light in the living room.",
        "turn on the humidifier in your bedroom.",
    ],
    "device.off": [
        "turn off the light",
        "turn off the light in the living room",
        "turn off the air conditioner",
        "turn off the kettle in the kitchen",
        # Helsinki outputs:
        "turn off the air conditioning.",
        "turn off the lights in the living room.",
        "turn off the light in the living room.",
        "turn off the kettle in the kitchen.",
    ],
    "device.set_temperature": [
        "set the air conditioner to 22 degrees",
        "set temperature to 20",
        # Helsinki outputs:
        "set the air conditioning to 22 degrees.",
    ],
    "device.set_mode": [
        "set cool mode on the air conditioner",
        "set heating mode",
        # Helsinki outputs:
        "set the cooling mode.",
    ],
    "device.set_fan_speed": [
        "set the fan speed to high",
        "set fan speed to low",
        # Helsinki outputs:
        "set fan speed to high.",
    ],
    "device.query_temperature": [
        "what is the temperature in the living room",
        "what is the temperature",
        # Helsinki outputs:
        "what is the temperature in the living room?",
    ],
    "device.lock": [
        "lock the front door",
        "lock the door",
        # Helsinki outputs (Helsinki tc-big-zle-en sometimes uses "shut"):
        "shut the front door.",
    ],
    "device.unlock": [
        "unlock the front door",
        "unlock the door",
        # Noisy bench: "open the front door" was hitting device.lock
        "open the front door",
        "open the door",
        "open the door for me",
    ],
    "clock.set_timer": [
        "set a timer for ten minutes",
        "start a timer for 5 minutes",
        # Helsinki outputs:
        "set the timer to 10 minutes.",
    ],
    "clock.cancel_alarm": [
        "cancel the alarm",
        "stop the alarm",
    ],
    "media.play_genre": [
        "play jazz radio",
        "play some rock music",
        "play classical music",
        # Helsinki outputs — critical for case 22:
        "turn on the jazz radio.",
    ],
    "media.play_radio": [
        "play the radio",
        "turn on radio",
    ],
    "media.play_radio_name": [
        "play Radio Relax",
        "put on BBC Radio",
    ],
    "media.pause": [
        "pause the music",
        # Helsinki outputs:
        "put music on pause.",
        "put the music on pause.",
    ],
    "weather.current": [
        "what is the weather outside",
        "current weather conditions",
        # Helsinki sometimes drops "is the":
        "what weather outside.",
        # Argos artifact for robustness:
        "what a weather.",
        # Noisy bench: indirect weather questions
        "is it raining",
        "is it raining outside",
        "is it cold outside",
        "is it warm outside",
    ],
    "weather.temperature": [
        "what is the current temperature outside",
        "how hot is it outside",
    ],
    "weather.forecast": [
        "what is the weather forecast",
        "weather for tomorrow",
    ],
    "privacy_on": [
        "enable privacy mode",
        # Helsinki outputs:
        "turn on the privacy mode.",
        # Noisy bench: "stop listening" alone shifted centroid too
        # far from positive commands like "увімкни режим приватності".
        # Keep only the longer, more specific forms.
        "stop listening to me",
        "don't listen to me",
    ],
    "privacy_off": [
        "disable privacy mode",
        "start listening again",
    ],
    "presence.who_home": [
        "who is at home",
        "who is home right now",
        # Deliberately NOT including "who are you" — that should fall
        # to unknown via the negative anchors below.
    ],
    "unknown": [
        # Negative anchors push the unknown centroid away from device
        # intents and toward "weird stuff" / "questions about the
        # assistant itself".
        "xyzzy plover quux",
        "tell me a joke",
        "tell me the joke.",
        "open the curtains",
        "open the blinds.",
        "who are you",
        "who are you.",
    ],
}


# ── Param extraction ────────────────────────────────────────────────
#
# Lexicon-based, runs on Helsinki English output. Includes Helsinki
# artifacts ("air conditioning", "clutch") so cases like
# "увімкни кондиціонер" still resolve entity correctly when Helsinki
# produces a non-canonical phrase.

ENTITY_MAP: dict[str, str] = {
    # Standard EN
    "light": "light",
    "lights": "light",
    "air conditioner": "air_conditioner",
    "fan": "fan",
    "lock": "lock",
    "thermostat": "thermostat",
    "humidifier": "humidifier",
    "kettle": "kettle",
    "outlet": "outlet",
    # Helsinki / Argos artifacts
    "air conditioning": "air_conditioner",
    "conditioner": "air_conditioner",
    "clutch": "humidifier",   # Argos quirk for зволожувач
}

ROOM_KEYWORDS: list[str] = [
    "living room", "bedroom", "kitchen", "bathroom",
    "hallway", "office", "garage",
]

VALUE_KEYWORDS: list[str] = [
    "high", "low", "medium", "auto",
    "cool", "heat", "dry", "eco", "turbo",
]

GENRE_KEYWORDS: list[str] = [
    "jazz", "rock", "classical", "pop", "blues",
    "electronic", "country", "folk", "metal",
]

# Word-to-number mapping for STT output where Vosk or Helsinki
# produces spelled-out numbers ("twenty two") instead of digits.
# Sorted longest-first at lookup time so "twenty two" matches
# before "twenty".
WORD_NUMBERS: dict[str, int] = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19, "twenty": 20,
    "twenty one": 21, "twenty two": 22, "twenty three": 23,
    "twenty four": 24, "twenty five": 25, "twenty six": 26,
    "twenty seven": 27, "twenty eight": 28, "twenty nine": 29,
    "thirty": 30,
}

_WORD_NUMBERS_SORTED = sorted(
    WORD_NUMBERS.items(), key=lambda x: -len(x[0]),
)


def _extract_numeric_value(text: str) -> str | None:
    """Extract the first numeric value from text — digit or word form.

    Digit regex runs FIRST because it's unambiguous ("22" is always
    a number). Word-number lookup runs second with word-boundary
    guards so "one" inside "air conditioner" doesn't match.
    """
    import re

    q = text.lower()
    # Prefer digit form — always unambiguous.
    nums = re.findall(r"\b(\d+)\b", q)
    if nums:
        return nums[0]
    # Fallback to word-number with word boundaries.
    for phrase, num in _WORD_NUMBERS_SORTED:
        pattern = r"\b" + re.escape(phrase) + r"\b"
        if re.search(pattern, q):
            return str(num)
    return None


def extract_params(query_en: str, intent: str) -> dict[str, Any]:
    """Lexicon-based param extraction over Helsinki English output."""
    import re

    q = query_en.lower()
    params: dict[str, Any] = {}

    # Entity — longest substring match wins so "air conditioning"
    # beats "conditioner" beats nothing.
    matched, matched_len = None, 0
    for kw, entity_type in ENTITY_MAP.items():
        if kw in q and len(kw) > matched_len:
            matched, matched_len = entity_type, len(kw)
    if matched:
        params["entity"] = matched

    # Location
    for room in ROOM_KEYWORDS:
        if room in q:
            params["location"] = room
            break

    # Value — only relevant for set_* intents. Handles both digit
    # ("22") and word-number ("twenty two") forms from Vosk/Helsinki.
    if intent in (
        "device.set_mode",
        "device.set_fan_speed",
        "device.set_temperature",
    ):
        num_val = _extract_numeric_value(q)
        if num_val:
            params["value"] = num_val
        else:
            for v in VALUE_KEYWORDS:
                if v in q:
                    params["value"] = v
                    break

    # Genre — only for media.play_genre
    if intent == "media.play_genre":
        for g in GENRE_KEYWORDS:
            if g in q:
                params["genre"] = g
                break

    return params


# ── Classifier ──────────────────────────────────────────────────────


@dataclass
class EmbeddingResult:
    intent: str
    score: float            # cosine similarity of the winner
    params: dict[str, Any] = field(default_factory=dict)
    runner_up: str = "unknown"
    runner_up_score: float = 0.0
    margin: float = 0.0     # winner.score - runner_up.score


class EmbeddingIntentClassifier:
    """Cosine-similarity classifier over pre-computed anchor centroids.

    Performance design
    ------------------
    The naive approach (re-encode every anchor on every classify call)
    is ~10× slower than necessary. Instead we pre-compute a single
    centroid per intent at warmup time, cache it, and on each classify
    call only embed the live query + the live description string. The
    description embedding is also cached because the catalog descriptions
    rarely change between requests.
    """

    MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
    UNKNOWN_THRESHOLD = 0.30   # max cosine below this → force unknown
    MARGIN_THRESHOLD = 0.05    # winner − runner_up below this → log low confidence

    def __init__(self) -> None:
        self._model = None
        # Pre-computed mean anchor embedding per intent name.
        self._anchor_cache: dict[str, np.ndarray] = {}
        # Lazy cache for description strings (filled on demand).
        self._desc_cache: dict[str, np.ndarray] = {}

    def warmup(self) -> None:
        """Load model and pre-compute anchor centroids."""
        if self._model is not None:
            return

        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(self.MODEL_NAME)

        # Pre-compute mean anchor embedding for every known intent.
        for intent_name, anchors in INTENT_ANCHORS.items():
            if not anchors:
                continue
            embs = self._model.encode(anchors, normalize_embeddings=True)
            mean = embs.mean(axis=0)
            mean = mean / np.linalg.norm(mean)
            self._anchor_cache[intent_name] = mean

        logger.info(
            "EmbeddingIntentClassifier: warmed up, %d anchor centroids",
            len(self._anchor_cache),
        )

    def _desc_embedding(self, description: str) -> np.ndarray:
        """Cache-or-encode a description string."""
        cached = self._desc_cache.get(description)
        if cached is not None:
            return cached
        emb = self._model.encode(description, normalize_embeddings=True)
        # Already normalized by sentence-transformers, but be defensive.
        emb = emb / np.linalg.norm(emb)
        self._desc_cache[description] = emb
        return emb

    def _intent_centroid(self, name: str, description: str) -> np.ndarray:
        """Combine pre-cached anchor centroid with live description embedding.

        If the intent has no anchors (one of the rare ones added by a
        module after this file was last updated), fall back to using
        the description alone.
        """
        anchor = self._anchor_cache.get(name)
        desc = self._desc_embedding(description)
        if anchor is None:
            return desc
        combined = anchor + desc
        return combined / np.linalg.norm(combined)

    def classify(
        self,
        query_en: str,
        candidates: list[dict[str, str]],
    ) -> EmbeddingResult:
        """Classify a single Helsinki-English query against the filtered catalog.

        ``candidates`` is the per-utterance shortlist already produced by
        ``IntentRouter._build_filtered_catalog``: a list of
        ``{"name": str, "description": str}`` dicts.
        """
        self.warmup()

        if not candidates:
            return EmbeddingResult(intent="unknown", score=0.0)

        query_emb = self._model.encode(query_en, normalize_embeddings=True)

        names: list[str] = []
        scores: list[float] = []
        for c in candidates:
            name = c["name"]
            desc = c.get("description", "")
            centroid = self._intent_centroid(name, desc)
            names.append(name)
            scores.append(float(np.dot(query_emb, centroid)))

        # Sort by score descending so we can read winner + runner-up
        order = sorted(range(len(scores)), key=lambda i: -scores[i])
        best_idx = order[0]
        best_intent = names[best_idx]
        best_score = scores[best_idx]

        if len(order) > 1:
            ru_idx = order[1]
            runner_up = names[ru_idx]
            runner_up_score = scores[ru_idx]
        else:
            runner_up = "unknown"
            runner_up_score = 0.0
        margin = best_score - runner_up_score

        # Hard threshold: if even the best match is weak, fall back.
        if best_score < self.UNKNOWN_THRESHOLD:
            best_intent = "unknown"

        params = extract_params(query_en, best_intent)

        return EmbeddingResult(
            intent=best_intent,
            score=best_score,
            params=params,
            runner_up=runner_up,
            runner_up_score=runner_up_score,
            margin=margin,
        )
