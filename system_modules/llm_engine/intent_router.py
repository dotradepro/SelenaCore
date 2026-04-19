"""
system_modules/llm_engine/intent_router.py — LLM-only Intent Router

Architecture:
  1. Try Module Bus (WebSocket user module intents)              — ~50ms
  2. Local LLM with keyword-filtered catalog                     — 500-2000ms
  3. Cloud LLM fallback (optional)                               — 1-3s
  4. Fallback → ``intent="unknown"``

The legacy Tier-0 regex FastMatcher, PatternGenerator and IntentCache
are all gone. Every request runs a fresh classification so that a
deleted / renamed device or freshly added radio station is reflected
immediately — no stale ``(text → intent)`` mapping can return a pointer
to something that no longer exists.

Every query builds a tight prompt that contains only the intents /
devices / rooms / stations whose names appear in the user's utterance.
That keeps the context short (→ faster, more accurate on small models)
and removes 2000+ lines of regex-generation and cache-promotion code.

All non-English utterances arrive already translated to English by
InputTranslator, so the filter and the prompt work in English only.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from typing import Any

from core.config_writer import get_value

logger = logging.getLogger(__name__)


@dataclass
class IntentResult:
    intent: str
    response: str
    action: dict[str, Any] | None
    source: str          # "system_module" | "module_bus" | "embedding" | "assistant" | "fallback"
    latency_ms: int
    lang: str = "en"
    user_id: str | None = None
    params: dict[str, Any] | None = None
    raw_llm: str | None = None    # raw LLM response before parsing (debug)
    # Clarification request populated by the router when a single-turn
    # answer isn't safe. VoiceCore reads this field and, if set, enters
    # AWAITING_CLARIFICATION mode — speaks the question, keeps mic open
    # for ``timeout_sec``, then routes the next utterance through
    # ``IntentRouter.route_clarification()`` with this dict as context.
    #
    # Schema:
    #   {
    #       "reason": "ambiguous_device" | "missing_param" | "low_margin",
    #       "question_key": str,             # action_phrasing key for TTS
    #       "hint": str | None,              # short hint for the question
    #       "candidates": [                  # when reason=ambiguous_device
    #           {"device_id": str, "name": str, "location": str | None, ...}
    #       ],
    #       "choices": [str, str, ...] | None,  # when reason=low_margin
    #       "pending_intent": str,           # the original intent we want to
    #                                         # retry after the answer
    #       "pending_params": dict,          # original params to merge into
    #       "timeout_sec": float,            # 10.0 default
    #   }
    #
    # None = no clarification needed; VoiceCore proceeds as before.
    clarification: dict[str, Any] | None = None


# Language-agnostic tokenisation. We intentionally do not keep per-
# language stopword lists: a length threshold of 3 characters acts as a
# universal stopword filter (articles, short prepositions and pronouns
# are ≤2 chars in every language we support). ``\w+`` under the UNICODE
# flag matches Latin, Cyrillic, Greek, CJK, Hangul, Arabic, Hebrew and
# every other connected script, so Russian / Ukrainian / German /
# Spanish / Chinese all tokenise correctly with the exact same regex.
_TOKEN_MIN_LEN = 3
_TOKEN_RE = re.compile(r"\w{%d,}" % _TOKEN_MIN_LEN, flags=re.UNICODE)


def _tokenize(text: str) -> list[str]:
    """Return ≥3-char word tokens, NFKC-normalised and lower-cased.

    Language-agnostic: works on any script that has a Unicode ``\\w``
    class, which is every modern alphabet we care about.
    """
    if not text:
        return []
    import unicodedata
    norm = unicodedata.normalize("NFKC", text.lower())
    return _TOKEN_RE.findall(norm)


_COMMAND_VERBS = frozenset({
    "turn", "switch", "set", "lock", "unlock", "open", "close",
    "play", "pause", "stop", "enable", "disable", "start",
    "make", "put", "activate", "deactivate",
})


def _extract_command_segment(text: str) -> str:
    """Extract the command-carrying clause from a long phrase.

    Voice utterances often wrap the actual command in context:

      "I just got home and it is really cold, turn on the AC"
      "Turn off the kettle in the kitchen and it's already boiling"

    Sentence embedding averages ALL tokens, so context noise
    dilutes the intent signal. This helper splits on conjunctions
    and picks the clause that starts with a command verb.

    Short queries (<=8 words) pass through unchanged.

    Selection logic (not "always last"):
      1. Split on conjunctions / commas.
      2. Find the FIRST clause whose first word is a command verb.
      3. If no clause has a command verb → return the first clause
         (commands more often lead than trail in voice).
    """
    words = text.split()
    if len(words) <= 8:
        return text

    segments = re.split(
        r"\b(?:and|but|so|because|since|as|while)\b|[,;]",
        text, flags=re.IGNORECASE,
    )
    segments = [
        s.strip() for s in segments
        if s
        and len(s.strip().split()) >= 2
        and not re.match(
            r"^(?:and|but|so|because|since|as|while)$",
            s.strip(), re.IGNORECASE,
        )
    ]

    if not segments:
        segments = [text]

    # Find the clause with a command verb in the first 4 words.
    # Covers "can you TURN on", "please SET the temperature",
    # "could you LOCK the door" where the verb isn't word #1.
    for seg in segments:
        seg_words = seg.split()
        head = seg_words[:4]
        if any(w.lower().rstrip("'s") in _COMMAND_VERBS for w in head):
            return seg

    # No conjunction-split clause has a verb in its head.
    # Last resort: scan the raw word list for a mid-sentence
    # command verb and take everything from it onward. Catches
    # "I'm cold TURN on the AC for heating" → "turn on the AC..."
    for i, w in enumerate(words):
        if w.lower().rstrip("'s") in _COMMAND_VERBS:
            return " ".join(words[i:])

    return segments[0]


def _parse_catalog_to_candidates(catalog_text: str) -> list[dict[str, str]]:
    """Pull intent rows out of the prompt catalog block.

    The router prints intents like ``  intent.name — description``
    inside an ``Intents:`` block. This helper recovers the original
    (name, description) pairs without re-querying the IntentCompiler,
    so the embedding classifier can re-use the exact filtered list
    that the LLM tier would have seen.
    """
    candidates: list[dict[str, str]] = []
    in_intents = False
    for line in catalog_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("Intents:"):
            in_intents = True
            continue
        if not stripped:
            in_intents = False
            continue
        if in_intents and " — " in stripped:
            name, _, desc = stripped.partition(" — ")
            name = name.strip()
            if name:
                candidates.append({"name": name, "description": desc.strip()})
    return candidates


def _normalize_en(text: str) -> str:
    """NFKC + lower + strip leading EN article + collapse whitespace.

    Applied to *English* fields coming out of Argos (``name_en``,
    ``location_en``) to erase inconsistent single-word outputs like
    ``"the living room"`` vs ``"living room"``. Safe to call on any
    string — non-English input simply loses its trailing spaces.
    """
    if not text:
        return ""
    import unicodedata
    s = unicodedata.normalize("NFKC", text).strip().lower()
    for article in ("the ", "a ", "an "):
        if s.startswith(article):
            s = s[len(article):]
            break
    return " ".join(s.split())


# ── Verb / idiom pattern tables (kept module-level so post-processor
#    and classifier-level heuristics share identical definitions). ──

_ON_VERBS = (
    "turn on", "switch on", "enable", "power on",
    "start", "activate", "run",
    "i want", "put on", "fire up", "kick on",
    "light up", "set up",
)
_OFF_VERBS = (
    "turn off", "switch off", "disable", "power off",
    "stop", "deactivate", "put out", "extinguish",
    "you can turn off", "could you turn off",
    "no need for", "don't need", "kill the",
    "shut down", "shut off", "cut the",
)
_MEDIA_VOLUME_IDIOMS = (
    "turn it up", "turn it down",
    "louder", "quieter", "softer",
)
_ALL_TOKENS = (
    " all ", " everything", " every ",
    " все ", " всі ", " всё ", " всю ", " все.", " всі.", " всё.",
)


def post_process_embedding_intent(result, query_en: str, native_text: str) -> None:
    """Context-aware disambiguation applied AFTER the embedding classifier.

    Mutates `result.intent` in place. Lives at module level so benchmarks
    (which bypass `IntentRouter.route()` and call `classifier.classify()`
    directly) can reuse the same logic without duplicating it.

    Handled cases:
      • Bare on/off imperative on an AC / thermostat → `device.set_mode`
        reclassified to `device.on` / `device.off`.
      • TV entity misrouted to `media.play_*` → power intent.
      • "turn it up / down" volume idioms misrouted to `device.on/off`.
      • Universal quantifier ("all", "все", "everything") on on/off →
        `house.all_on` / `house.all_off`.
    """
    q_low = (query_en or "").lower()

    suffix_on = q_low.rstrip(".!?").endswith(" on")
    suffix_off = q_low.rstrip(".!?").endswith(" off")

    def has_on_verb() -> bool:
        return suffix_on or any(
            q_low.startswith(v) or f" {v}" in q_low for v in _ON_VERBS
        )

    def has_off_verb() -> bool:
        return suffix_off or any(
            q_low.startswith(v) or f" {v}" in q_low for v in _OFF_VERBS
        )

    # 1. Bare-verb reclassification for mode/temp/fan/query intents.
    if result.intent in (
        "device.set_mode", "device.set_temperature",
        "device.set_fan_speed", "device.query_temperature",
    ):
        has_mode_param = bool((result.params or {}).get("value"))
        is_on = has_on_verb()
        is_off = has_off_verb()
        if any(phr in q_low for phr in _MEDIA_VOLUME_IDIOMS):
            is_on = False
            is_off = False
        if not has_mode_param and (is_on or is_off):
            new_intent = "device.off" if is_off else "device.on"
            logger.debug(
                "embedding post-proc: %s → %s (imperative verb, no mode param)",
                result.intent, new_intent,
            )
            result.intent = new_intent

    # 2. TV misrouted to media.play_* → power intent.
    if (
        result.intent.startswith("media.play_")
        and ((result.params or {}).get("entity") == "tv")
    ):
        new_intent = "device.off" if has_off_verb() else "device.on"
        logger.debug(
            "embedding post-proc: %s → %s (TV is a device, not a station)",
            result.intent, new_intent,
        )
        result.intent = new_intent

    # 3. Volume idioms misrouted to device.on/off.
    if result.intent in ("device.on", "device.off"):
        if "turn it up" in q_low or "louder" in q_low:
            result.intent = "media.volume_up"
            logger.debug("post-proc: → media.volume_up (idiom)")
        elif (
            "turn it down" in q_low
            or "quieter" in q_low
            or "softer" in q_low
        ):
            result.intent = "media.volume_down"
            logger.debug("post-proc: → media.volume_down (idiom)")

    # 4. Universal quantifier → mass power intent.
    native_check = f" {(native_text or '').lower()} "
    q_all_check = f" {q_low} "
    has_all = any(
        tok in q_all_check or tok in native_check for tok in _ALL_TOKENS
    )
    if has_all and result.intent in ("device.on", "device.off"):
        new_intent = (
            "house.all_on" if result.intent == "device.on" else "house.all_off"
        )
        logger.debug(
            "embedding post-proc: %s → %s (all/everything quantifier)",
            result.intent, new_intent,
        )
        result.intent = new_intent


class IntentRouter:
    """Intent router: Module Bus → Embedding → Assistant LLM → Fallback."""

    def __init__(self) -> None:
        self._live_log_fn: Any = None  # callback for live monitor logging
        self._embedding = None  # lazy-loaded EmbeddingIntentClassifier

    def set_live_log(self, fn: Any) -> None:
        """Set callback for live monitor: fn(event: str, data: dict)."""
        self._live_log_fn = fn

    def _live_log(self, event: str, data: dict) -> None:
        if self._live_log_fn:
            try:
                self._live_log_fn(event, data)
            except Exception:
                pass

    # ── Main routing ────────────────────────────────────────────────────

    async def route(
        self,
        text: str,
        user_id: str | None = None,
        lang: str = "en",
        *,
        tts_lang: str | None = None,
        native_text: str | None = None,
        trace: bool = False,
    ) -> IntentResult | tuple[IntentResult, list[dict[str, Any]]]:
        """Route user text: Module Bus → Embedding → Assistant LLM → Fallback.

        Tier 0: Module Bus — WebSocket user module intents
        Tier 1: Embedding — sentence-transformers cosine, ~50 ms
        Tier 2: Assistant — freeform LLM reply for unknown intents (no catalog)
        Fallback: deterministic "I did not understand" phrase

        Args:
            text: English form of the utterance (post-Argos).
            lang: STT-detected language code.
            tts_lang: TTS output language. Defaults to ``lang``.
            native_text: The original utterance BEFORE Argos. When set,
                the catalog filter considers tokens from BOTH ``text``
                and ``native_text``. Defaults to ``text`` when there
                was no translation step.

        Returns IntentResult (or (IntentResult, trace_steps) when trace=True).
        """
        if tts_lang is None:
            tts_lang = lang
        if native_text is None:
            native_text = text
        start_ms = int(time.time() * 1000)
        steps: list[dict[str, Any]] = [] if trace else []

        def _elapsed() -> int:
            return int(time.time() * 1000) - start_ms

        # ── Tier 1: Module Bus (WebSocket user module intents) ──
        bus_hit = False
        bus_error = None
        try:
            from core.module_bus import get_module_bus
            bus_result = await get_module_bus().route_intent(
                text, lang, context={"user_id": user_id},
            )
            if bus_result is not None:
                if bus_result.get("handled"):
                    bus_hit = True
                    result = IntentResult(
                        intent=f"module.{bus_result.get('module', '?')}",
                        response=bus_result.get("tts_text", ""),
                        action=bus_result.get("data"),
                        source="module_bus",
                        latency_ms=_elapsed(),
                        lang=lang,
                        user_id=user_id,
                    )
                    if trace:
                        steps.append({
                            "tier": "0", "name": "Module Bus",
                            "status": "hit",
                            "ms": _elapsed(),
                            "detail": bus_result.get("module", "?"),
                        })
                    await self._publish_event(result, raw_text=text, lang=lang)
                    return (result, steps) if trace else result
                # Module unavailable
                reason = bus_result.get("reason", "")
                module_name = bus_result.get("module", "?")
                if reason in ("circuit_open", "timeout", "disconnected"):
                    logger.warning(
                        "Module bus: %s unavailable (reason=%s)", module_name, reason,
                    )
                    bus_error = f"{module_name}: {reason}"
                    result = IntentResult(
                        intent=f"module.{module_name}",
                        response="The module is temporarily unavailable. Please try again later.",
                        action=None,
                        source="module_bus",
                        latency_ms=_elapsed(),
                        lang=lang,
                        user_id=user_id,
                    )
                    if trace:
                        steps.append({
                            "tier": "0", "name": "Module Bus",
                            "status": "error",
                            "ms": _elapsed(),
                            "detail": bus_error,
                        })
                    await self._publish_event(result, raw_text=text, lang=lang)
                    return (result, steps) if trace else result
        except Exception as exc:
            logger.warning("Module bus error: %s", exc)
            bus_error = str(exc)

        if trace and not bus_hit:
            steps.append({
                "tier": "0", "name": "Module Bus",
                "status": "error" if bus_error else "miss",
                "ms": _elapsed(),
                "detail": bus_error,
            })

        # No IntentCache: the router always runs a fresh classification.
        # Devices, rooms and radio stations can change between requests,
        # and a cached (text → intent) pair could return a classification
        # that references a deleted or renamed entity. The keyword-
        # filtered catalog is cheap enough (a few SQL reads + one LLM
        # call) that the ~10 ms cache saving is not worth the risk of
        # stale hits.

        # ── Tier 1: Embedding classifier (fast path) ──
        # Sentence-transformers cosine over per-utterance candidates.
        # Confident hits short-circuit the LLM tier (~60× faster).
        # Low-margin or low-score cases fall through to Local LLM.
        # Runs only when intent.embedding_enabled is true (default).
        emb_result = None
        emb_error = None
        if self._embedding_enabled():
            try:
                emb_result = await self._embedding_classify(
                    text, lang, native_text=native_text,
                )
            except Exception as exc:
                emb_error = str(exc)
                logger.warning("Embedding classifier error: %s", exc)

            if trace:
                steps.append({
                    "tier": "1", "name": "Embedding",
                    "status": "hit" if emb_result else (
                        "error" if emb_error else "fallthrough"
                    ),
                    "ms": _elapsed(),
                    "detail": emb_result.intent if emb_result else emb_error,
                })

        # Accept confident embedding results for KNOWN intents.
        # Embedding "unknown" falls through to the assistant tier so
        # the LLM can give a conversational reply instead of the
        # static "I did not understand" phrase.
        #
        # (Old logic accepted embedding unknown to prevent the LLM
        # classifier from overriding correct unknowns with wrong
        # intents like `presence.who_home`. Now the LLM is a freeform
        # assistant — it won't produce wrong intents, only a spoken
        # reply — so falling through is safe and desirable.)
        if emb_result is not None and emb_result.intent != "unknown":
            emb_result.latency_ms = _elapsed()
            emb_result.lang = lang
            emb_result.user_id = user_id
            emb_result = await self._resolve_entity_ref(emb_result)
            emb_result = await self._disambiguate_device(emb_result, tts_lang)
            await self._publish_event(emb_result, raw_text=text, lang=lang)
            return (emb_result, steps) if trace else emb_result

        # ── Tier 2: Freeform LLM assistant ──
        # Instead of classifying, ask the LLM to respond conversationally.
        # Uses native_text (pre-translation) so the LLM answers in the
        # user's language naturally.
        assistant_result = None
        assistant_error = None
        try:
            assistant_result = await self._ask_as_assistant(text)
        except asyncio.TimeoutError:
            assistant_error = "timeout"
            logger.warning("Assistant LLM timeout for: %s", text[:50])
        except Exception as exc:
            assistant_error = str(exc)
            logger.warning("Assistant LLM error: %s", exc)

        if trace:
            steps.append({
                "tier": "2", "name": "Assistant LLM",
                "status": "hit" if assistant_result else (
                    "error" if assistant_error else "skip"
                ),
                "ms": _elapsed(),
                "detail": (assistant_result.response[:60] if assistant_result
                           else assistant_error),
            })

        if assistant_result is not None:
            assistant_result.latency_ms = _elapsed()
            assistant_result.lang = lang
            assistant_result.user_id = user_id
            await self._publish_event(
                assistant_result, raw_text=text, lang=lang,
            )
            return (assistant_result, steps) if trace else assistant_result

        # ── Fallback: embedding low-confidence, assistant unavailable ──
        # No hardcoded English text — VoiceCore / format_action_context()
        # will turn ``intent="unknown"`` into the deterministic fallback
        # phrase ("I did not understand that command.") and OutputTranslator
        # renders it in the TTS language.
        if trace:
            steps.append({
                "tier": "—", "name": "Fallback",
                "status": "used",
                "ms": _elapsed(),
            })

        result = IntentResult(
            intent="unknown",
            response="",
            action=None,
            source="fallback",
            latency_ms=_elapsed(),
            lang=lang,
            user_id=user_id,
        )
        await self._publish_event(result, raw_text=text, lang=lang)
        return (result, steps) if trace else result

    # ── Clarification fast path ───────────────────────────────────────

    async def route_clarification(
        self,
        text: str,
        pending: dict[str, Any],
        lang: str = "en",
        *,
        tts_lang: str | None = None,
        native_text: str | None = None,
    ) -> IntentResult:
        """Resolve a follow-up utterance against a pending clarification.

        VoiceCore calls this after speaking the clarification question
        and receiving the user's reply. The reply is matched against
        the pending context (candidates / choices / missing-value slot)
        — NOT re-classified from scratch. Returns an IntentResult that
        either re-fires the original intent with merged params, or
        surfaces a clarification-failed state so VoiceCore can speak a
        canned "didn't get that" message.

        Pending schema (see IntentResult.clarification docstring):
          - reason="ambiguous_device" → pending.candidates (list of dicts
            with name, location, device_id). Reply is matched against
            location / name / positional reference.
          - reason="missing_param" → pending has ``param_name`` and
            (optional) ``allowed_values``. Reply is parsed for a numeric
            value or word-form number.
          - reason="low_margin" → pending.choices is [winner, runner_up].
            Reply is matched against either intent name or its
            human phrasing ("the second one", "the first").

        If the match succeeds, returns IntentResult with the original
        pending_intent + merged pending_params, source="clarification".
        On failure returns source="fallback" with intent="unknown" so
        VoiceCore speaks the canned cancel.
        """
        if tts_lang is None:
            tts_lang = lang
        if native_text is None:
            native_text = text

        reason = pending.get("reason", "")
        pending_intent = pending.get("pending_intent") or "unknown"
        pending_params = dict(pending.get("pending_params") or {})

        # Helsinki-translate non-English replies to English for the EN
        # matchers. Native text is kept for bilingual room/name matching.
        try:
            if lang != "en":
                from core.translation.local_translator import get_input_translator
                text_en = get_input_translator().to_english(text, lang)
            else:
                text_en = text
        except Exception:
            text_en = text

        reply_en = (text_en or "").strip().lower()
        reply_native = (native_text or text or "").strip().lower()

        matched_extra: dict[str, Any] = {}
        match_found = False

        if reason == "ambiguous_device":
            matched_extra = self._match_clarification_device(
                reply_en, reply_native, pending.get("candidates") or [],
            )
            match_found = bool(matched_extra)
        elif reason == "missing_param":
            matched_extra = self._match_clarification_value(
                reply_en, reply_native, pending,
            )
            match_found = bool(matched_extra)
        elif reason == "low_margin":
            matched_extra = self._match_clarification_choice(
                reply_en, reply_native, pending,
            )
            match_found = bool(matched_extra)

        if not match_found:
            # Canned cancel — VoiceCore speaks clarify.cancelled and idles.
            return IntentResult(
                intent="unknown",
                response="",
                action=None,
                source="fallback",
                latency_ms=0,
                lang=lang,
                params={"clarification_result": "cancelled"},
            )

        # Handle low_margin choice flip: if user picked the runner-up,
        # route through the full pipeline again with the corrected intent.
        if reason == "low_margin" and matched_extra.get("chosen_intent") != pending_intent:
            pending_intent = matched_extra["chosen_intent"]
            matched_extra = {
                k: v for k, v in matched_extra.items()
                if k != "chosen_intent"
            }

        merged_params = {**pending_params, **matched_extra}

        result = IntentResult(
            intent=pending_intent,
            response="",
            action=None,
            source="clarification",
            latency_ms=0,
            lang=lang,
            params=merged_params,
        )
        # Re-run disambiguation on the merged params so device_ids get
        # filled if the user picked a room now.
        try:
            result = await self._disambiguate_device(result, tts_lang)
        except Exception as exc:
            logger.debug("Disambiguation after clarification failed: %s", exc)

        await self._publish_event(result, raw_text=text, lang=lang)
        return result

    # Fuzzy / positional / numeric matchers for clarification replies.
    # LANG: positional references hardcoded for en/uk — extend
    # _POSITIONAL_MAP to add languages. Everything else (name / room /
    # numeric) works across languages via the existing registry / Argos
    # / numeric-word lookups.

    _POSITIONAL_MAP: dict[str, int] = {
        # EN
        "first": 1, "1st": 1, "one": 1, "the first": 1, "the first one": 1,
        "second": 2, "2nd": 2, "two": 2, "the second": 2, "the second one": 2,
        "third": 3, "3rd": 3, "the third": 3,
        "last": -1, "the last": -1,
        # UK
        "перший": 1, "перша": 1, "перше": 1,
        "другий": 2, "друга": 2, "друге": 2,
        "третій": 3, "третя": 3, "третє": 3,
        "останній": -1, "остання": -1,
    }

    _FUZZY_THRESHOLD = 0.75  # Jaro-Winkler-ish floor. Starting value;
                             # revisit after real-world clarification
                             # logs (see plan §R3).

    @staticmethod
    def _similarity(a: str, b: str) -> float:
        """Cheap string-similarity score in [0..1] using difflib."""
        import difflib
        if not a or not b:
            return 0.0
        return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()

    # Bilingual room-name table for clarification replies. The candidate
    # list carries ``location`` in whatever language the device was
    # registered with (typically UK). When the user answers in EN,
    # translate both sides through this map before comparing.
    # LANG: add more pairs for new languages.
    _ROOM_BILINGUAL: dict[str, str] = {
        # en → uk
        "bedroom":     "спальня",
        "kitchen":     "кухня",
        "living room": "вітальня",
        "living_room": "вітальня",
        "office":      "кабінет",
        "bathroom":    "ванна",
        "hallway":     "коридор",
        "garage":      "гараж",
        # mirror so UK → EN works too (added below)
    }

    def _match_clarification_device(
        self,
        reply_en: str,
        reply_native: str,
        candidates: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Match reply against ambiguous-device candidates."""
        if not candidates:
            return {}

        # 1. Positional reference
        for phrase, idx in self._POSITIONAL_MAP.items():
            if f" {phrase} " in f" {reply_en} " or f" {phrase} " in f" {reply_native} ":
                target = candidates[idx - 1] if idx > 0 else candidates[-1]
                if target:
                    return {
                        "device_id": target["device_id"],
                        "entity": target.get("entity_type"),
                        "location": target.get("location"),
                    }

        # 2. Room match — bilingual + morphology-tolerant. Candidate
        # location is whatever was registered (typically UK native);
        # reply may arrive in EN (Helsinki) or UK with noun cases
        # ("у вітальні" vs registered "вітальня"). Three-pass:
        #   a. Substring exact match against aliases
        #   b. Token-level similarity ≥ 0.7 against any reply token
        reply_combined = f" {reply_en} {reply_native} "
        uk_to_en = {v: k for k, v in self._ROOM_BILINGUAL.items()}
        for c in candidates:
            loc = (c.get("location") or "").lower().strip()
            if not loc:
                continue
            aliases = {loc}
            if loc in uk_to_en:
                aliases.add(uk_to_en[loc])
            if loc in self._ROOM_BILINGUAL:
                aliases.add(self._ROOM_BILINGUAL[loc])
            if any(a in reply_combined for a in aliases):
                return {
                    "device_id": c["device_id"],
                    "entity": c.get("entity_type"),
                    "location": c.get("location"),
                }
            # Morphology-tolerant pass: compare each reply token to
            # each alias via stem similarity. UK locative "вітальні"
            # vs nominative "вітальня" differ by 1-2 trailing chars
            # but share a long common prefix — SequenceMatcher ratio
            # is ~0.9. Threshold 0.70 catches this while still
            # rejecting "ванна" from "вітальні" (~0.3 similarity).
            reply_tokens = (
                reply_en.split() + reply_native.split()
            )
            for tok in reply_tokens:
                tok = tok.strip(".,!?;:").lower()
                if len(tok) < 4:
                    continue
                for alias in aliases:
                    if self._similarity(tok, alias) >= 0.70:
                        return {
                            "device_id": c["device_id"],
                            "entity": c.get("entity_type"),
                            "location": c.get("location"),
                        }

        # 3. Device-name match
        best_score = 0.0
        best_candidate: dict[str, Any] | None = None
        for c in candidates:
            name = (c.get("name") or "").lower()
            if not name:
                continue
            score = max(
                self._similarity(name, reply_en),
                self._similarity(name, reply_native),
            )
            if score > best_score:
                best_score = score
                best_candidate = c
        if best_candidate and best_score >= self._FUZZY_THRESHOLD:
            return {
                "device_id": best_candidate["device_id"],
                "entity": best_candidate.get("entity_type"),
                "location": best_candidate.get("location"),
            }

        return {}

    def _match_clarification_value(
        self,
        reply_en: str,
        reply_native: str,
        pending: dict[str, Any],
    ) -> dict[str, Any]:
        """Match reply against a missing-param slot.

        Looks for a numeric value (digit or word-form) in either the
        translated or native text. For the MVP only numeric values are
        supported — mode / fan-speed clarifications would add a list of
        allowed strings and fuzzy-match them.
        """
        from system_modules.llm_engine.embedding_classifier import (
            _extract_numeric_value,
        )

        num = _extract_numeric_value(reply_en) or _extract_numeric_value(reply_native)
        if num:
            param_name = pending.get("param_name", "value")
            return {param_name: num}

        # Allowed-values fuzzy match (for set_mode etc.)
        allowed = pending.get("allowed_values") or []
        best_score = 0.0
        best_value: str | None = None
        for value in allowed:
            score = max(
                self._similarity(value, reply_en),
                self._similarity(value, reply_native),
            )
            if score > best_score:
                best_score = score
                best_value = value
        if best_value and best_score >= self._FUZZY_THRESHOLD:
            param_name = pending.get("param_name", "value")
            return {param_name: best_value}

        return {}

    def _match_clarification_choice(
        self,
        reply_en: str,
        reply_native: str,
        pending: dict[str, Any],
    ) -> dict[str, Any]:
        """Low-margin: user picks winner, runner-up, or states the
        intent directly."""
        choices = pending.get("choices") or []
        if len(choices) < 2:
            return {}

        # Positional — "the first" / "первый" → choices[0]
        for phrase, idx in self._POSITIONAL_MAP.items():
            if f" {phrase} " in f" {reply_en} " or f" {phrase} " in f" {reply_native} ":
                if 0 < idx <= len(choices):
                    return {"chosen_intent": choices[idx - 1]}

        # Direct intent-name match
        for c in choices:
            # Compare against last segment of the intent name, e.g.
            # "device.set_temperature" → "temperature"; also full name.
            tail = c.split(".")[-1].replace("_", " ")
            if tail.lower() in reply_en or tail.lower() in reply_native:
                return {"chosen_intent": c}

        # Yes/ok/sure — confirm winner
        _AFFIRM = ("yes", "yeah", "sure", "ok", "okay", "так", "да", "звичайно")
        if any(w in reply_en.split() or w in reply_native.split() for w in _AFFIRM):
            return {"chosen_intent": choices[0]}

        return {}

    # ── Embedding classifier (Tier 1, fast path) ─────────────────────

    def warmup_embedding(self) -> None:
        """Force-load the embedding model up front (called from voice-core
        boot). Without this the first user request after a cold start
        eats the ~26 sec model-load latency.
        """
        if not self._embedding_enabled():
            return
        emb = self._ensure_embedding()
        if emb:
            emb.warmup()

    def _ensure_embedding(self):
        """Lazy-load the embedding classifier (~30 MB RAM via ONNX Runtime).
        Voice-core warms it up on boot via ``warmup()``; this is the
        safety net for any other entry point.
        """
        if self._embedding is not None:
            return self._embedding
        try:
            from system_modules.llm_engine.embedding_classifier import (
                EmbeddingIntentClassifier,
            )
            self._embedding = EmbeddingIntentClassifier()
        except (ImportError, FileNotFoundError, OSError) as exc:
            logger.warning(
                "Embedding classifier init failed (%s) — Tier 1 disabled, "
                "falling through to LLM.", exc,
            )
            self._embedding = False  # sentinel: never try again
        return self._embedding

    def _embedding_enabled(self) -> bool:
        """Read the embedding-tier toggle from config (default on)."""
        return bool(get_value("intent", "embedding_enabled", True))

    async def _embedding_classify(
        self,
        text: str,
        lang: str,
        *,
        native_text: str | None = None,
    ) -> IntentResult | None:
        """Run the embedding classifier over the filtered catalog.

        Returns a confident IntentResult or ``None`` if score / margin
        thresholds were not met (caller falls through to LLM tier).
        Confidence policy is configurable via:

          intent.embedding_score_threshold  (default 0.30)
          intent.embedding_margin_threshold (default 0.05)

        score < score_threshold      → return None (fall through)
        margin < margin_threshold    → return None (fall through, top-2 too close)
        otherwise                    → return IntentResult(source="embedding")

        The hallucination guard from the LLM path is unnecessary here:
        the classifier picks ONLY from candidates already inside the
        filtered catalog (which equals the per-utterance ``allowed`` set).
        """
        emb = self._ensure_embedding()
        if not emb:
            return None

        catalog, allowed = await self._build_filtered_catalog(
            text, native_text=native_text,
        )
        candidates = _parse_catalog_to_candidates(catalog)
        if not candidates:
            return None

        # For long phrases (>8 words), extract the command-carrying
        # segment so context noise ("I just got home and it is cold")
        # doesn't dilute the embedding signal. Short phrases pass
        # through unchanged. The catalog filter still sees the FULL
        # text (all tokens contribute to candidate selection).
        query_for_embed = _extract_command_segment(text)
        result = emb.classify(query_for_embed, candidates)

        score_threshold = float(
            get_value("intent", "embedding_score_threshold", 0.30) or 0.30
        )
        margin_threshold = float(
            get_value("intent", "embedding_margin_threshold", 0.05) or 0.05
        )

        if result.score < score_threshold:
            logger.debug(
                "embedding: score %.3f < %.3f → fall through to LLM",
                result.score, score_threshold,
            )
            return None
        if result.margin < margin_threshold:
            logger.debug(
                "embedding: margin %.3f < %.3f (winner=%r vs runner_up=%r) "
                "→ fall through to LLM",
                result.margin, margin_threshold,
                result.intent, result.runner_up,
            )
            return None
        # Final hallucination guard. The classifier picks from `candidates`
        # which came from `allowed`, so this should never trip — kept as a
        # safety net.
        if result.intent != "unknown" and result.intent not in allowed:
            logger.warning(
                "embedding: intent %r not in allowed set %s — falling through",
                result.intent, sorted(allowed),
            )
            return None

        # ── Post-processing: context-aware disambiguation ──
        # Extracted into a module-level function so benchmarks can reuse
        # the same logic without dragging the full `route()` pipeline.
        post_process_embedding_intent(result, query_for_embed, native_text or text)

        # ── Low-margin clarification trigger ──
        # Band chosen empirically from bench margin histogram (see
        # tests/experiments/results/bench_margin_histogram.txt for the analysis). When
        # the winner is only this close to the runner-up, the classifier
        # is on the fence — asking the user is cheaper and more
        # deterministic than silently committing to the wrong intent.
        #
        # The band explicitly skips [0.015, 0.020): that range contains
        # "confident misroutes" caused by Helsinki verb-loss (запали X
        # → noun-only translation) where clarification cannot help
        # because repeating the phrase keeps hitting the same wrong
        # translation. Fix there is upstream in the translator.
        CLARIFY_MARGIN_LOW = 0.003
        CLARIFY_MARGIN_HIGH = 0.015
        pending_clarification: dict[str, Any] | None = None
        if (
            CLARIFY_MARGIN_LOW <= result.margin < CLARIFY_MARGIN_HIGH
            and result.intent not in ("unknown",)
            and result.runner_up
            and result.runner_up not in ("unknown",)
            and result.intent != result.runner_up
        ):
            pending_clarification = {
                "reason": "low_margin",
                "question_key": "clarify.low_confidence",
                "choices": [result.intent, result.runner_up],
                "hint": None,
                "pending_intent": result.intent,
                "pending_params": dict(result.params or {}),
                "timeout_sec": 10.0,
                "margin": result.margin,
            }

        return IntentResult(
            intent=result.intent,
            response="",
            action=None,
            source="embedding",
            latency_ms=0,
            params=result.params,
            clarification=pending_clarification,
            raw_llm=(
                f"score={result.score:.3f} margin={result.margin:.3f} "
                f"runner_up={result.runner_up}({result.runner_up_score:.3f})"
            ),
        )

    # ── Prompt building (per-request word-overlap filter) ─────────────
    # Tested 2026-04-11 on qwen2.5:1.5b + Helsinki, 40-case trace bench:
    #   * Full prompt with 4 examples:                32/40 (baseline)
    #   * Slimmed prompt, no examples, no template:   24/40 (-8 cases)
    #   * Slim prompt + bare format line `{...}`:     29/40 (-3 cases)
    #   * Baseline + namespace hint + AC synonym:     35/40 (+3 cases)
    # Removing the examples drops qwen 1.5b sharply because the 4
    def invalidate_catalog_cache(self) -> None:
        """No-op. Catalog is built per-request from live DB state."""
        return

    async def _build_filtered_catalog(
        self, user_text: str, native_text: str | None = None,
    ) -> tuple[str, set[str]]:
        """Assemble the dynamic prompt section for one utterance.

        The filter tokenises BOTH ``user_text`` (post-Argos English) AND
        ``native_text`` (original pre-translation) with the same Unicode
        tokeniser. Match-set is the union, so an utterance whose verb
        Argos dropped ("вимкни" → "turn") still picks up the right
        intents via the native token "вимкни" hitting the device's
        Ukrainian name/location.
        """
        if native_text is None:
            native_text = user_text
        tokens = set(_tokenize(user_text)) | set(_tokenize(native_text))
        parts: list[str] = []

        # ── Intents via description + verb word overlap ──
        matched_intents: list[tuple[str, str]] = []
        try:
            from system_modules.llm_engine.intent_compiler import get_intent_compiler
            compiled = get_intent_compiler().get_all_intents()
            for ci in compiled:
                desc = (ci.description or "").strip().replace("\n", " ")
                desc_tokens = set(_tokenize(desc))
                verb_tokens = set(
                    _tokenize(ci.intent.replace(".", " ").replace("_", " "))
                )
                if tokens & (desc_tokens | verb_tokens):
                    # 120 char cap leaves room for disambiguating phrases
                    # like "NOT for specific station names" without
                    # truncating them. Legacy descriptions ("Turn a device
                    # on") are still ≤30 chars and unaffected.
                    short = desc if len(desc) <= 120 else desc[:117] + "..."
                    matched_intents.append((ci.intent, short))
        except Exception as exc:
            logger.debug("intent filter failed: %s", exc)

        # Always include "unknown" so the LLM has a safe bail-out.
        # Never include "chat" — freeform falls through to unknown and
        # VoiceCore plays the deterministic fallback phrase.
        matched_intents.append((
            "unknown",
            "Use this when no other intent fits the command",
        ))

        intent_lines = [
            f"  {n} — {d}" if d else f"  {n}" for n, d in matched_intents
        ]
        parts.append("Intents:\n" + "\n".join(intent_lines))

        # ── Devices / radio stations ──
        db_part = await self._load_db_filtered_catalog(tokens)
        if db_part:
            parts.append(db_part)

        built = "\n\n".join(parts)
        allowed = {n for n, _ in matched_intents}
        logger.debug(
            "filtered catalog: %d intents, %d chars (tokens=%s)",
            len(matched_intents), len(built), sorted(tokens),
        )
        logger.debug("filtered catalog allowed=%s", sorted(allowed))
        return built, allowed

    async def _load_db_filtered_catalog(self, tokens: set[str]) -> str:
        """Pull devices / radio stations that match ``tokens``.

        Matching is **bilingual**: each device and station is compared
        against BOTH its native-language fields (``meta.name``,
        ``meta.location``, ``name_user``, ``genre_user``) AND its English
        fields (``meta.name_en``, ``meta.location_en``, ``name_en``,
        ``genre_en``). The utterance tokens are Unicode so they can
        match whichever form appears in the user's actual speech.

        Emitting the catalog then includes both forms so a small LLM
        classifier sees a single row like
        ``вітальня / living room: light "світло" / "light"`` and can
        key off whichever signal is clearer even when Argos mangles
        the input translation.
        """
        if not tokens:
            return ""
        try:
            from core.module_loader.sandbox import get_sandbox
            sf = get_sandbox()._session_factory
            if sf is None:
                return ""

            from sqlalchemy import select
            from core.registry.models import RadioStation, Device
            import json as _json

            parts: list[str] = []

            async with sf() as session:
                # ── Devices (bilingual matching) ──
                devices = list(
                    (await session.execute(select(Device))).scalars().all()
                )
                # matched_rooms: key → list[(etype, name_native, name_en)]
                matched_rooms: dict[str, list[tuple[str, str, str]]] = {}
                for d in devices:
                    try:
                        meta = _json.loads(d.meta) if d.meta else {}
                    except Exception:
                        meta = {}
                    name_native = (meta.get("name") or d.name or "").strip()
                    name_en     = _normalize_en(meta.get("name_en") or "")
                    room_native = (meta.get("location") or d.location or "").strip()
                    room_en     = _normalize_en(meta.get("location_en") or "")
                    etype       = (d.entity_type or "device").strip()

                    haystack: set[str] = set()
                    haystack |= set(_tokenize(name_native))
                    haystack |= set(_tokenize(name_en))
                    haystack |= set(_tokenize(room_native))
                    haystack |= set(_tokenize(room_en))
                    haystack |= set(_tokenize(etype.replace("_", " ")))

                    if tokens & haystack:
                        # Pick the best room label: prefer joined form.
                        if room_native and room_en and room_native.lower() != room_en:
                            room_key = f"{room_native} / {room_en}"
                        else:
                            room_key = room_en or room_native or "unassigned"
                        matched_rooms.setdefault(room_key, []).append(
                            (etype, name_native, name_en)
                        )

                # Emit ENGLISH-only forms into the prompt. Bilingual
                # MATCHING happens above, but the prompt must show a
                # single canonical form so small LLMs do not copy joined
                # "native / english" strings verbatim into params.
                if matched_rooms:
                    lines: list[str] = []
                    for room_key, entries in sorted(matched_rooms.items()):
                        # ``room_key`` may be "native / english" internally
                        # for uniqueness; strip back to english-only for
                        # display.
                        room_display = room_key.split(" / ")[-1] or "unassigned"
                        rendered_entries = [
                            f'{etype} "{ne or nn}"'
                            for etype, nn, ne in entries
                        ]
                        lines.append(f"  {room_display}: " + ", ".join(rendered_entries))
                    parts.append("Matching devices:\n" + "\n".join(lines))

                # ── Radio stations (bilingual on name / genre) ──
                stmt = select(RadioStation).where(
                    RadioStation.enabled == True  # noqa: E712
                )
                stations = list((await session.execute(stmt)).scalars().all())
                matched_stations: list[str] = []
                for s in stations:
                    name_native = (getattr(s, "name_user", "") or "").strip()
                    name_en     = (s.name_en or "").strip()
                    genre_native= (getattr(s, "genre_user", "") or "").strip()
                    genre_en    = (s.genre_en or "").strip()

                    hay: set[str] = set()
                    hay |= set(_tokenize(name_native))
                    hay |= set(_tokenize(name_en))
                    hay |= set(_tokenize(genre_native))
                    hay |= set(_tokenize(genre_en))

                    if tokens & hay:
                        label = name_en or name_native
                        genre_label = genre_en or genre_native
                        matched_stations.append(
                            f"{label} ({genre_label})" if genre_label else label
                        )
                if matched_stations:
                    parts.append(
                        "Matching radio stations: " + ", ".join(matched_stations[:15])
                    )

            return "\n\n".join(parts)

        except Exception as exc:
            logger.debug("DB filtered catalog load failed: %s", exc)
            return ""

    # ── Freeform LLM assistant (replaces classification tiers) ────────

    async def _ask_as_assistant(self, text: str) -> IntentResult | None:
        """Call the LLM as a freeform assistant for unrecognised commands.

        Uses the user-configured system prompt from PromptStore — the same
        prompt visible in the voice module settings UI.

        Returns an IntentResult with ``source="assistant"`` and the LLM's
        natural-language reply in ``response``, or ``None`` on failure
        (timeout, empty response, disabled) so the caller falls through
        to the deterministic fallback.
        """
        if not bool(get_value("intent", "llm_assistant_enabled", True)):
            return None

        from core.llm import llm_call

        reply = await llm_call(
            text,
            prompt_key="chat",
            temperature=0.7,
            max_tokens=100,
            timeout=float(get_value("llm", "timeout_sec", 30)),
            json_mode=False,
            num_ctx=2048,
        )

        if not reply:
            return None

        return IntentResult(
            intent="unknown",
            response=reply,
            action=None,
            source="assistant",
            latency_ms=0,
            raw_llm=reply,
        )

    def refresh_system_prompt(self) -> None:
        """Invalidate the cached intent catalog.

        Called from ``core.api.helpers.on_entity_changed`` whenever a
        registry row (device/radio/scene) changes, so the next
        embedding classify call rebuilds the catalog from fresh DB state.
        """
        self.invalidate_catalog_cache()

    # ── Resolve entity_ref for named entities ────────────────────────────

    async def _resolve_entity_ref(self, result: IntentResult) -> IntentResult:
        """Resolve entity_ref from DB for intents that reference named entities.

        When LLM or cache returns e.g. media.play_radio_name with station_name="Люкс ФМ",
        look up RadioStation by name (name_user or name_en) and inject entity_ref.
        Same for scenes and devices.
        """
        params = result.params or {}
        if params.get("entity_ref"):
            return result  # already resolved

        try:
            from core.module_loader.sandbox import get_sandbox
            sf = get_sandbox()._session_factory
            if sf is None:
                return result

            from sqlalchemy import select, func

            intent = result.intent

            if intent == "media.play_radio_name":
                name = params.get("station_name", "")
                if not name:
                    return result
                from core.registry.models import RadioStation
                async with sf() as session:
                    # Try exact match on name_user or name_en (case-insensitive)
                    name_lower = name.lower()
                    stmt = select(RadioStation).where(
                        RadioStation.enabled == True
                    )
                    rows = list((await session.execute(stmt)).scalars().all())
                    match = None
                    for row in rows:
                        if (row.name_user and row.name_user.lower() == name_lower) or \
                           (row.name_en and row.name_en.lower() == name_lower):
                            match = row
                            break
                    # Fallback: substring match
                    if not match:
                        for row in rows:
                            if (row.name_user and name_lower in row.name_user.lower()) or \
                               (row.name_en and name_lower in row.name_en.lower()):
                                match = row
                                break
                    if match:
                        result.params = {**params, "entity_ref": f"radio_station:{match.id}"}

            elif intent == "automation.run_scene":
                name = params.get("scene_name", params.get("entity", ""))
                if not name:
                    return result
                from core.registry.models import Scene
                async with sf() as session:
                    name_lower = name.lower()
                    stmt = select(Scene).where(Scene.enabled == True)
                    rows = list((await session.execute(stmt)).scalars().all())
                    for row in rows:
                        if (row.name_user and row.name_user.lower() == name_lower) or \
                           (row.name_en and row.name_en.lower() == name_lower):
                            result.params = {**params, "entity_ref": f"scene:{row.id}"}
                            break

        except Exception as exc:
            logger.debug("Entity ref resolution failed: %s", exc)

        return result

    # ── Device disambiguation ─────────────────────────────────────────

    async def _disambiguate_device(
        self, result: IntentResult, tts_lang: str,
    ) -> IntentResult:
        """Resolve a voice intent's target device(s) by type + location.

        Queries DeviceRegistry with ``entity_type + location`` (exact
        match on both columns) and injects the winner(s) into params.

        - 1 match          → inject ``device_id`` (single-device path)
        - N match + room   → inject ``device_ids=[…]`` (group action;
                             module fans the command out to each one)
        - N match + NO room → inject ``ambiguous=True`` so the module
                             speaks a "specify a room" prompt instead of
                             silently acting on a random device
        - 0 match          → pass through (module speaks "not found")

        There is no ``"disambiguation"`` sentinel intent anymore — voice
        users can't interactively answer "which one?", so the group path
        is always preferred when a room is given. Name-matching stays in
        device-control's own resolver for the composite-name case.
        """
        params = result.params or {}
        entity = params.get("entity")
        location = params.get("location")

        # If the classifier extracted no entity word but the intent has
        # a declared ``entity_types`` constraint (e.g. device.set_temperature
        # allows air_conditioner / thermostat / radiator), use that list
        # to resolve. Typical case: "set the temperature to 22 in the
        # bedroom" — no literal device name, but intent + location are
        # enough.
        allowed_entity_types: list[str] = []
        if not entity:
            try:
                from system_modules.llm_engine.intent_compiler import get_intent_compiler
                defn = get_intent_compiler().get_definition(result.intent)
                if defn and defn.entity_types:
                    allowed_entity_types = list(defn.entity_types)
            except Exception:
                pass
            if not allowed_entity_types:
                return result

        try:
            from core.module_loader.sandbox import get_sandbox
            sandbox = get_sandbox()
            session_factory = sandbox._session_factory
            if session_factory is None:
                return result

            from core.registry.service import DeviceRegistry

            async with session_factory() as session:
                registry = DeviceRegistry(session)
                if entity:
                    devices = await registry.query(
                        entity_type=entity, location=location,
                    )
                else:
                    # Entity inferred from intent's entity_types constraint.
                    # Query once per allowed type and merge.
                    devices = []
                    seen: set[str] = set()
                    for et in allowed_entity_types:
                        for d in await registry.query(
                            entity_type=et, location=location,
                        ):
                            if d.device_id not in seen:
                                seen.add(d.device_id)
                                devices.append(d)

            # When entity was inferred (not spoken), write it back to
            # params so downstream modules + logging can see what the
            # resolver actually picked.
            inferred_entity = None
            if not entity and devices:
                # Pick the most common entity_type among matches.
                etypes = [d.entity_type for d in devices if d.entity_type]
                if etypes:
                    inferred_entity = max(set(etypes), key=etypes.count)

            if len(devices) == 1:
                result.params = {
                    **(result.params or {}),
                    "device_id": devices[0].device_id,
                    **({"entity": inferred_entity} if inferred_entity else {}),
                }
            elif len(devices) > 1 and location:
                # Group action: hand every match to the module.
                result.params = {
                    **(result.params or {}),
                    "device_ids": [d.device_id for d in devices],
                    **({"entity": inferred_entity} if inferred_entity else {}),
                    "candidates": [
                        {"device_id": d.device_id, "name": d.name, "location": d.location}
                        for d in devices[:10]
                    ],
                }
            elif len(devices) > 1:
                # Multiple matches, no room — ask the user which room.
                # VoiceCore reads result.clarification, speaks the
                # prompt, keeps the mic open and routes the reply
                # through route_clarification().
                candidates = [
                    {
                        "device_id": d.device_id,
                        "name": d.name,
                        "location": d.location,
                        "entity_type": d.entity_type,
                    }
                    for d in devices[:10]
                ]
                rooms = sorted({
                    c["location"] for c in candidates if c.get("location")
                })
                result.clarification = {
                    "reason": "ambiguous_device",
                    "question_key": "clarify.which_room",
                    "hint": (result.params or {}).get("entity") or "device",
                    "rooms": rooms,
                    "candidates": candidates,
                    "pending_intent": result.intent,
                    "pending_params": dict(result.params or {}),
                    "timeout_sec": 10.0,
                }
                # Legacy mirror — existing device-control fallback code
                # still inspects params["ambiguous"] until it's migrated
                # to read clarification directly. Keep both signals for
                # one release.
                result.params = {
                    **(result.params or {}),
                    "ambiguous": True,
                    "candidates": candidates,
                }
        except Exception as exc:
            logger.debug("Disambiguation failed: %s", exc)

        return result

    # ── Event publishing ───────────────────────────────────────────────

    @staticmethod
    def _normalize_params(params: dict[str, Any] | None) -> dict[str, Any]:
        """Pass-through for historical callers.

        The old implementation mapped Cyrillic genre/mode values back to
        their English form so downstream modules could assume English.
        With InputTranslator handling the whole utterance before it ever
        reaches the router, every captured value is already English, so
        no normalization is needed.
        """
        return dict(params) if params else {}

    async def _publish_event(self, result: IntentResult, raw_text: str = "", lang: str = "en") -> None:
        try:
            from core.eventbus.bus import get_event_bus
            from core.eventbus.types import VOICE_INTENT
            normalized_params = self._normalize_params(result.params)
            await get_event_bus().publish(
                type=VOICE_INTENT,
                source="core.intent_router",
                payload={
                    "intent": result.intent,
                    "response": result.response,
                    "action": result.action,
                    "params": normalized_params,
                    "source": result.source,
                    "user_id": result.user_id,
                    "latency_ms": result.latency_ms,
                    "raw_text": raw_text,
                    "lang": lang,
                },
            )
        except Exception as e:
            logger.debug("Intent event publish failed: %s", e)


_router: IntentRouter | None = None


def get_intent_router() -> IntentRouter:
    global _router
    if _router is None:
        _router = IntentRouter()
    return _router
