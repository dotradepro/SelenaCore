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
import json
import logging
import os
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
    source: str          # "fast_matcher" | "system_module" | "module_bus" | "embedding" | "llm" | "cloud" | "cache" | "fallback"
    latency_ms: int
    lang: str = "en"
    user_id: str | None = None
    params: dict[str, Any] | None = None
    raw_llm: str | None = None    # raw LLM response before parsing (debug)


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


class IntentRouter:
    """Intent router: Module Bus → Embedding → Local LLM → Cloud LLM (no cache)."""

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
        """Route user text: Module Bus → Embedding → Local LLM → Cloud LLM.

        Args:
            text: English form of the utterance (post-Argos).
            lang: STT-detected language code.
            tts_lang: TTS output language. Defaults to ``lang``.
            native_text: The original utterance BEFORE Argos. When set,
                the catalog filter and param sanitizer consider tokens
                from BOTH ``text`` and ``native_text``, so an Argos
                misfire on the source verb (e.g. "вимкни" → "turn") no
                longer breaks classification. Defaults to ``text`` when
                there was no translation step.

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

        # Accept any embedding result — including confident "unknown".
        #
        # _embedding_classify already filtered by score + margin
        # thresholds. If it returns a result (not None), the
        # classification is trustworthy. Letting "unknown" fall
        # through to the LLM tier is WORSE: the LLM on Jetson
        # overrides correct unknowns with wrong intents like
        # `presence.who_home` for "who are you". On Pi the LLM
        # timed out → accidental correct fallback. Trusting
        # embedding for unknown gives consistent results on
        # both platforms.
        if emb_result is not None:
            emb_result.latency_ms = _elapsed()
            emb_result.lang = lang
            emb_result.user_id = user_id
            if emb_result.intent != "unknown":
                emb_result = await self._resolve_entity_ref(emb_result)
                emb_result = await self._disambiguate_device(emb_result, tts_lang)
            await self._publish_event(emb_result, raw_text=text, lang=lang)
            return (emb_result, steps) if trace else emb_result

        # ── Tier 2: Local LLM (single call) ──
        llm_result = None
        llm_error = None
        try:
            llm_result = await self._local_llm_classify(
                text, lang, tts_lang=tts_lang, native_text=native_text,
            )
        except asyncio.TimeoutError:
            llm_error = "timeout"
            logger.warning("Local LLM timeout for: %s", text[:50])
        except Exception as exc:
            llm_error = str(exc)
            logger.warning("Local LLM error: %s", exc)

        if trace:
            steps.append({
                "tier": "2", "name": "Local LLM",
                "status": "hit" if llm_result else ("error" if llm_error else "skip"),
                "ms": _elapsed(),
                "detail": llm_result.intent if llm_result else llm_error,
            })

        if llm_result is not None and llm_result.intent != "unknown":
            llm_result.latency_ms = _elapsed()
            llm_result.lang = lang
            llm_result.user_id = user_id
            llm_result = await self._resolve_entity_ref(llm_result)
            llm_result = await self._disambiguate_device(llm_result, tts_lang)
            await self._publish_event(llm_result, raw_text=text, lang=lang)
            return (llm_result, steps) if trace else llm_result

        # ── Tier 3: Cloud LLM (if configured) ──
        cloud_result = None
        cloud_error = None
        cloud_cfg = self._get_cloud_config()
        if cloud_cfg:
            try:
                cloud_result = await self._cloud_llm_classify(
                    text, lang, cloud_cfg, tts_lang=tts_lang, native_text=native_text,
                )
            except asyncio.TimeoutError:
                cloud_error = "timeout"
            except Exception as exc:
                cloud_error = str(exc)
                logger.warning("Cloud LLM error: %s", exc)

            if trace:
                steps.append({
                    "tier": "3", "name": "Cloud LLM",
                    "status": "hit" if cloud_result else ("error" if cloud_error else "skip"),
                    "ms": _elapsed(),
                    "detail": cloud_result.intent if cloud_result else cloud_error,
                })

            if cloud_result is not None and cloud_result.intent != "unknown":
                cloud_result.latency_ms = _elapsed()
                cloud_result.lang = lang
                cloud_result.user_id = user_id
                cloud_result = await self._resolve_entity_ref(cloud_result)
                cloud_result = await self._disambiguate_device(cloud_result, tts_lang)
                await self._publish_event(cloud_result, raw_text=text, lang=lang)
                return (cloud_result, steps) if trace else cloud_result
        elif trace:
            steps.append({
                "tier": "3", "name": "Cloud LLM",
                "status": "skip",
                "ms": _elapsed(),
                "detail": "not configured",
            })

        # ── Fallback: classifier found nothing, LLM timed out, etc. ──
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
        """Lazy-load the embedding classifier (heavy: ~80 MB RAM, 26 s
        first-call cold start). Voice-core warms it up on boot via
        ``warmup()``; this is the safety net for any other entry point.
        """
        if self._embedding is not None:
            return self._embedding
        try:
            from system_modules.llm_engine.embedding_classifier import (
                EmbeddingIntentClassifier,
            )
            self._embedding = EmbeddingIntentClassifier()
        except ImportError as exc:
            logger.warning(
                "Embedding classifier import failed (%s) — Tier 1 disabled, "
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
        # Final hallucination guard mirroring _parse_llm_response. The
        # classifier picks from `candidates` which came from `allowed`,
        # so this should never trip — kept for parity.
        if result.intent != "unknown" and result.intent not in allowed:
            logger.warning(
                "embedding: intent %r not in allowed set %s — falling through",
                result.intent, sorted(allowed),
            )
            return None

        return IntentResult(
            intent=result.intent,
            response="",
            action=None,
            source="embedding",
            latency_ms=0,
            params=result.params,
            raw_llm=(
                f"score={result.score:.3f} margin={result.margin:.3f} "
                f"runner_up={result.runner_up}({result.runner_up_score:.3f})"
            ),
        )

    # ── Local LLM (single call) ────────────────────────────────────────

    async def _local_llm_classify(
        self,
        text: str,
        lang: str,
        *,
        tts_lang: str | None = None,
        native_text: str | None = None,
    ) -> IntentResult | None:
        """Single LLM call via core.llm.llm_call(): returns intent JSON.

        ``text`` is the post-Argos English form, ``native_text`` is the
        original utterance (may equal ``text`` for English speakers).
        Both forms feed the bilingual filter and post-classification
        sanitizer so Argos glitches cannot drop information.
        """
        from core.llm import llm_call

        native_text = native_text or text
        catalog, allowed = await self._build_filtered_catalog(
            text, native_text=native_text,
        )

        # Text is already English — send directly, no language wrapping.
        # 8k context gives headroom for the enriched catalog (intent
        # descriptions, per-device names, radio station list).
        # max_tokens=256: the JSON response is small (~100 tokens); capping
        # tight cuts generation time dramatically on 3B models (qwen2.5,
        # phi3:mini) without truncating real answers.
        raw = await llm_call(
            text,
            prompt_key="intent",
            extra_context=catalog,
            temperature=0.1,
            max_tokens=256,
            timeout=30.0,
            num_ctx=8192,
        )

        if not raw:
            return None

        # Sanitizer checks BOTH the Argos-translated form AND the
        # original utterance, so values spoken natively survive even if
        # the English form dropped them.
        sanity_text = f"{text}\n{native_text}" if native_text != text else text
        return self._parse_llm_response(
            raw, source="llm", utter_text=sanity_text, allowed_intents=allowed,
        )

    # ── Cloud LLM ──────────────────────────────────────────────────────

    def _get_cloud_config(self) -> dict | None:
        """Check whether a cloud LLM provider is configured.

        Returns a truthy dict if cloud is available, None otherwise.
        llm_call() handles the actual provider dispatch internally.
        """
        try:
            from core.config_writer import read_config
            config = read_config()
            ai_cfg = config.get("ai", {}).get("conversation", {})

            # Check new ai.conversation.cloud config
            cloud_cfg = ai_cfg.get("cloud", {})
            url = cloud_cfg.get("url", "")
            key = cloud_cfg.get("key") or os.environ.get("GROQ_API_KEY", "")
            model = cloud_cfg.get("model", "")
            if url and key and model:
                return {"url": url, "key": key, "model": model}

            # Fallback: check legacy voice.providers for cloud
            voice_cfg = config.get("voice", {})
            provider = voice_cfg.get("llm_provider", "")
            if provider not in ("ollama", ""):
                providers_cfg = voice_cfg.get("providers", {})
                p_cfg = providers_cfg.get(provider, {})
                api_key = p_cfg.get("api_key", "")
                p_model = p_cfg.get("model", "")
                if api_key and p_model:
                    return {"provider": provider, "key": api_key, "model": p_model}
        except Exception:
            pass
        return None

    async def _cloud_llm_classify(
        self,
        text: str,
        lang: str,
        cloud_cfg: dict,
        *,
        tts_lang: str | None = None,
        native_text: str | None = None,
    ) -> IntentResult | None:
        """Cloud LLM classification via core.llm.llm_call()."""
        from core.llm import llm_call

        native_text = native_text or text
        catalog, allowed = await self._build_filtered_catalog(
            text, native_text=native_text,
        )

        raw = await llm_call(
            text,
            prompt_key="intent",
            extra_context=catalog,
            temperature=0.1,
            max_tokens=256,
            timeout=15.0,
            num_ctx=8192,
        )

        if not raw:
            return None

        sanity_text = f"{text}\n{native_text}" if native_text != text else text
        return self._parse_llm_response(
            raw, source="cloud", utter_text=sanity_text, allowed_intents=allowed,
        )

    # ── Prompt building (per-request word-overlap filter) ─────────────

    # Static instructions appended to the user-editable identity. Describes
    # the JSON contract and the allowed params. Kept tight on purpose —
    # small models are better classifiers than multi-shot imitators.
    #
    # IMPORTANT: do NOT remove the Examples block.
    # Tested 2026-04-11 on qwen2.5:1.5b + Helsinki, 40-case trace bench:
    #   * Full prompt with 4 examples:                32/40 (baseline)
    #   * Slimmed prompt, no examples, no template:   24/40 (-8 cases)
    #   * Slim prompt + bare format line `{...}`:     29/40 (-3 cases)
    #   * Baseline + namespace hint + AC synonym:     35/40 (+3 cases)
    # Removing the examples drops qwen 1.5b sharply because the 4
    # examples act as a structural anchor for form imitation —
    # without them small models flatten params, omit the intent
    # field, or hallucinate Home-Assistant-style dotted entity IDs
    # like `light.office`, `lock.front_door`. The namespace hint and
    # entity synonyms below are supplements, not replacements.
    # If you want to tune this header, run run_trace_bench.py before
    # AND after every change to qwen 1.5b + Helsinki and check the
    # raw responses for these failure modes.
    _SCHEMA_HEADER = (
        "Reply with ONE JSON object, no prose, no markdown, no code fences:\n"
        '{"intent":"<namespace.action>","params":{"entity":"...","location":"..."}}\n\n'
        "Rules:\n"
        "- Pick the intent name EXACTLY from the Intents list below.\n"
        "- Use \"unknown\" if nothing in the list fits the command.\n"
        "- Do NOT invent intent names. Do NOT add a \"response\" field.\n"
        "- Namespaces: device.* = hardware, clock.* = timers/alarms, media.* = playback.\n"
        "- Include a param ONLY if the user literally said it. If the user\n"
        "  did not name a device/station/number, OMIT that key. Never copy\n"
        "  placeholder values from the schema above.\n"
        "- An empty params object {} is fine. Prefer omitting fields over\n"
        "  guessing them. NEVER fabricate entity, location, station, genre,\n"
        "  or value just because the schema lists those fields.\n\n"
        "Examples (output shape only — pick intents from your list):\n"
        '  "pause the music"      -> {"intent":"media.pause"}\n'
        '  "enable privacy mode"  -> {"intent":"privacy_on"}\n'
        '  "turn on the kitchen light" -> {"intent":"device.on","params":{"entity":"light","location":"kitchen"}}\n'
        '  "set fan speed to high"     -> {"intent":"device.set_fan_speed","params":{"value":"high"}}\n\n'
        "Params:\n"
        "- entity: device TYPE when the user said one "
        "(light, outlet, air_conditioner/\"air conditioning\", lock, thermostat, fan, humidifier, ...)\n"
        "- location: room name from the catalog when the user said a room\n"
        "- value: the exact word or number the user said for a setting "
        "(e.g. 22, high, low, cool, auto)\n"
        "- name_en: exact catalog device name ONLY when the user said that exact name\n"
        "- station / genre: ONLY for media intents when the user named a station or genre\n"
    )

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
        parts: list[str] = [self._SCHEMA_HEADER]

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

    # ── Response parsing ───────────────────────────────────────────────

    # Fields the LLM likes to hallucinate into when a schema slot exists
    # but the user said nothing about it. They go through the stricter
    # substring check below. Intentionally generic — no language-specific
    # rules, no number-word lists. If the value literally appears in the
    # original utterance (NFKC-normalised, case-insensitive), we keep
    # it; otherwise it's a fabrication.
    _SANITIZE_KEYS = ("name_en", "station", "genre", "value")

    # Literal placeholder strings models tend to copy from the schema
    # example block when they have nothing real to put in a slot. Always
    # dropped from non-_SANITIZE_KEYS keys (entity, location). For
    # _SANITIZE_KEYS keys we still allow them through if they literally
    # appear in the utterance — protects pathological-but-valid inputs
    # like "set mode to none".
    _PLACEHOLDER_VALUES = frozenset({
        "...", "…", "null", "none", "n/a",
        "<value>", "<entity>", "<location>",
    })

    @staticmethod
    def _in_utterance(value: Any, utter: str) -> bool:
        """True if ``value`` appears as a substring in ``utter``.

        NFKC + case-fold on both sides. Language-agnostic: works for
        any script the user's Vosk/Argos pair produces.
        """
        if value in (None, "", "null"):
            return False
        import unicodedata
        v = unicodedata.normalize("NFKC", str(value)).strip().lower()
        if not v:
            return False
        u = unicodedata.normalize("NFKC", utter or "").lower()
        return v in u

    def _sanitize_params(
        self, params: dict[str, Any], utter_text: str,
    ) -> dict[str, Any]:
        """Drop params that cannot be justified from the utterance.

        Universal rule: ``entity`` and ``location`` pass through (the
        LLM often canonicalises a room or device type and that is
        allowed). Everything else in ``_SANITIZE_KEYS`` must either
        (a) appear literally as a substring in the original utterance,
        or (b) for ``value`` specifically, be a number whose digits
        appear in the utterance.
        """
        clean: dict[str, Any] = {}
        digit_only_utter = bool(re.search(r"\d", utter_text or ""))
        for key, raw_val in params.items():
            if raw_val in (None, "", "null"):
                continue
            val_str = str(raw_val).strip()
            if not val_str or val_str.lower() == "null":
                continue

            if val_str.lower() in self._PLACEHOLDER_VALUES:
                if key in self._SANITIZE_KEYS and self._in_utterance(
                    val_str, utter_text,
                ):
                    pass  # user literally said this word — keep it
                else:
                    logger.debug(
                        "dropped placeholder %s=%r (utter=%r)",
                        key, raw_val, utter_text,
                    )
                    continue

            if key in self._SANITIZE_KEYS:
                if key == "value":
                    # Numeric values need at least one digit in the source
                    # (any language) OR a spelled-out form that literally
                    # appears in the utterance.
                    is_digit_value = bool(re.search(r"\d", val_str))
                    if is_digit_value and digit_only_utter:
                        pass  # keep
                    elif self._in_utterance(val_str, utter_text):
                        pass  # keep
                    else:
                        logger.debug(
                            "dropped fabricated value=%r (utter=%r)",
                            raw_val, utter_text,
                        )
                        continue
                else:
                    if not self._in_utterance(val_str, utter_text):
                        logger.debug(
                            "dropped fabricated %s=%r (utter=%r)",
                            key, raw_val, utter_text,
                        )
                        continue
            clean[key] = raw_val
        return clean

    def _parse_llm_response(
        self,
        raw: str,
        source: str = "llm",
        utter_text: str = "",
        allowed_intents: set[str] | None = None,
    ) -> IntentResult | None:
        """Parse the classifier JSON into an IntentResult.

        The contract is strict: the LLM must return a JSON object with
        ``intent`` + optional ``params``. Any ``response`` key it tries to
        sneak in is silently dropped — the spoken reply is composed later
        by :func:`format_action_context`. Parse errors / empty output
        degrade to ``intent="unknown"`` so the caller always gets a
        well-formed :class:`IntentResult`.
        """
        raw_debug = raw
        cleaned = (raw or "").strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)

        start_idx = cleaned.find("{")
        if start_idx == -1:
            return IntentResult(
                intent="unknown", response="", action=None,
                source=source, latency_ms=0, raw_llm=raw_debug,
            )

        # Extract the FIRST balanced JSON object from start_idx. Some cloud
        # providers (Gemini observed) emit a valid object followed by
        # garbage like '\n":"living room"}}\n"}}'. The old approach of
        # rfind('}') swept up all that junk and crashed json.loads. A
        # depth-counting walk that respects string literals + escapes
        # gives us exactly the first complete object.
        end_idx = -1
        depth = 0
        in_str = False
        esc = False
        for i in range(start_idx, len(cleaned)):
            ch = cleaned[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end_idx = i
                    break
        if end_idx == -1:
            return IntentResult(
                intent="unknown", response="", action=None,
                source=source, latency_ms=0, raw_llm=raw_debug,
            )

        try:
            data = json.loads(cleaned[start_idx:end_idx + 1])
        except json.JSONDecodeError:
            return IntentResult(
                intent="unknown", response="", action=None,
                source=source, latency_ms=0, raw_llm=raw_debug,
            )

        intent_name = (data.get("intent") or "").strip()
        params = data.get("params") or {}

        # Legacy top-level entity/location shim: some models still emit
        # these at the root instead of under params. Fold them in silently.
        entity = data.get("entity")
        location = data.get("location")
        if entity and entity != "null":
            params.setdefault("entity", entity)
        if location and location != "null":
            params.setdefault("location", location)

        # "chat" is a leftover from older prompts; collapse it to unknown
        # so the deterministic fallback speech fires.
        if not intent_name or intent_name == "chat":
            intent_name = "unknown"

        # Strict per-catalog hallucination guard. The LLM saw a filtered
        # catalog of intents relevant to this utterance; anything outside
        # that set is a fabrication. Drop both the intent name AND any
        # params it carried — unknown with leftover params is worse than
        # nothing for downstream consumers.
        if (
            allowed_intents is not None
            and intent_name != "unknown"
            and intent_name not in allowed_intents
        ):
            logger.debug(
                "LLM hallucinated intent %r not in catalog (%d allowed) → unknown",
                intent_name, len(allowed_intents),
            )
            intent_name = "unknown"
            params = {}

        # Sanity-check params against the original utterance so the LLM
        # can't invent device names, stations, genres, or numeric values.
        # Substring-based — language-agnostic.
        params = self._sanitize_params(params, utter_text)

        return IntentResult(
            intent=intent_name, response="", action=None,
            source=source, latency_ms=0, params=params,
            raw_llm=raw_debug,
        )

    def refresh_system_prompt(self) -> None:
        """Invalidate the cached intent catalog.

        Called from ``core.api.helpers.on_entity_changed`` whenever a
        registry row (device/radio/scene) changes, so the next LLM call
        rebuilds the catalog from fresh DB state.
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
        """If intent targets a device entity with >1 match, ask user to clarify.

        Uses entity_type + location from params to query DeviceRegistry.
        If exactly 1 device matches — injects device_id into params.
        If >1 match — replaces response with a clarification question.
        If 0 match — leaves result unchanged (module will handle).
        """
        params = result.params or {}
        entity = params.get("entity")
        location = params.get("location")

        # Only disambiguate device-related intents with entity info
        if not entity:
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
                devices = await registry.query(
                    entity_type=entity,
                    location=location,
                )

            if len(devices) == 1:
                # Single match — inject device_id
                result.params = {**(result.params or {}), "device_id": devices[0].device_id}
            elif len(devices) > 1:
                # Multiple matches — ask for clarification
                device_names = ", ".join(d.name for d in devices[:5])
                result.intent = "disambiguation"
                result.response = f"Which one did you mean: {device_names}?"
                result.action = None
                result.params = {
                    **(result.params or {}),
                    "candidates": [
                        {"device_id": d.device_id, "name": d.name, "location": d.location}
                        for d in devices[:5]
                    ],
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
