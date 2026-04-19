#!/usr/bin/env python3
"""
tests/experiments/run_variety_bench.py — robustness stress test.

Same intents as the canonical 40-case corpus, but every utterance is
deliberately rephrased: politeness markers, synonyms, indirect speech,
abbreviated forms, reversed word order, conversational tone. The point
is to find out how the production embedding tier handles realistic
linguistic variety it WAS NOT trained on (anchors were tuned to the
canonical phrasings).

Two things this measures:

1. **Embedding robustness** — does cosine over MiniLM-L6-v2 generalise
   from "turn on the light" to "lights please" / "make it bright"?
2. **Fallback rate** — how often do varied phrasings drop below the
   score/margin thresholds and fall through to the LLM tier?

Pipeline: native → Helsinki → router.route() (full production chain).

Run inside the selena-core container:

    docker compose exec -T core python3 \\
        /opt/selena-core/tests/experiments/run_variety_bench.py
"""
from __future__ import annotations

import asyncio
import json
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

_here = Path(__file__).resolve()
for candidate in (_here.parents[2], Path("/opt/selena-core")):
    if (candidate / "core" / "llm.py").is_file() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))


# ── Varied corpus ───────────────────────────────────────────────────
#
# Each entry: (lang, native, expected_intent, expected_params, twist)
# Twist tag describes WHY this phrasing is hard:
#   syn      — synonym / different verb
#   polite   — politeness markers (please, could you, would you)
#   indirect — indirect speech / desire ("I want", "I need")
#   short    — abbreviated / elliptical ("lights please")
#   reorder  — reversed word order
#   casual   — colloquial / spoken register

CORPUS: list[dict] = [
    # ── device.on / device.off via synonyms ──
    {"lang": "en", "native": "switch on the lights",
     "exp_intent": "device.on", "exp_params": {"entity": "light"},
     "twist": "syn"},
    {"lang": "en", "native": "could you turn on the light please",
     "exp_intent": "device.on", "exp_params": {"entity": "light"},
     "twist": "polite"},
    {"lang": "en", "native": "lights please",
     "exp_intent": "device.on", "exp_params": {"entity": "light"},
     "twist": "short"},
    {"lang": "en", "native": "make it bright in here",
     "exp_intent": "device.on", "exp_params": {"entity": "light"},
     "twist": "indirect"},
    {"lang": "uk", "native": "запали світло",
     "exp_intent": "device.on", "exp_params": {"entity": "light"},
     "twist": "syn"},
    {"lang": "uk", "native": "ввімкни лампу будь ласка",
     "exp_intent": "device.on", "exp_params": {"entity": "light"},
     "twist": "polite"},
    {"lang": "en", "native": "kill the lights",
     "exp_intent": "device.off", "exp_params": {"entity": "light"},
     "twist": "casual"},
    {"lang": "en", "native": "lights off",
     "exp_intent": "device.off", "exp_params": {"entity": "light"},
     "twist": "short"},
    {"lang": "uk", "native": "погаси світло",
     "exp_intent": "device.off", "exp_params": {"entity": "light"},
     "twist": "syn"},

    # ── temperature / climate variations ──
    {"lang": "en", "native": "make it 22 degrees",
     "exp_intent": "device.set_temperature", "exp_params": {"value": "22"},
     "twist": "indirect"},
    {"lang": "en", "native": "i want 22 in here",
     "exp_intent": "device.set_temperature", "exp_params": {"value": "22"},
     "twist": "indirect"},
    {"lang": "en", "native": "set the AC to 22",
     "exp_intent": "device.set_temperature", "exp_params": {"value": "22"},
     "twist": "syn"},
    {"lang": "uk", "native": "хочу 22 градуси",
     "exp_intent": "device.set_temperature", "exp_params": {"value": "22"},
     "twist": "indirect"},
    {"lang": "en", "native": "make the AC cooler",
     "exp_intent": "device.set_mode", "exp_params": {"value": "cool"},
     "twist": "indirect"},
    {"lang": "en", "native": "AC to cool mode",
     "exp_intent": "device.set_mode", "exp_params": {"value": "cool"},
     "twist": "short"},

    # ── temperature query (NOT weather) ──
    {"lang": "en", "native": "how warm is the living room",
     "exp_intent": "device.query_temperature",
     "exp_params": {"location": "living room"}, "twist": "indirect"},
    {"lang": "uk", "native": "скільки градусів у вітальні",
     "exp_intent": "device.query_temperature",
     "exp_params": {"location": "living room"}, "twist": "syn"},

    # ── lock / unlock variations ──
    {"lang": "en", "native": "secure the front door",
     "exp_intent": "device.lock", "exp_params": {}, "twist": "syn"},
    {"lang": "en", "native": "open the front door for me",
     "exp_intent": "device.unlock", "exp_params": {}, "twist": "polite"},
    {"lang": "uk", "native": "відімкни вхідні двері",
     "exp_intent": "device.unlock", "exp_params": {}, "twist": "syn"},

    # ── media / radio ──
    {"lang": "en", "native": "i want some jazz",
     "exp_intent": "media.play_genre",
     "exp_params": {"genre": "jazz"}, "twist": "indirect"},
    {"lang": "en", "native": "put on some classical music",
     "exp_intent": "media.play_genre",
     "exp_params": {"genre": "classical"}, "twist": "casual"},
    {"lang": "uk", "native": "хочу послухати джаз",
     "exp_intent": "media.play_genre",
     "exp_params": {"genre": "jazz"}, "twist": "indirect"},
    {"lang": "en", "native": "stop the music",
     "exp_intent": "media.pause", "exp_params": {}, "twist": "syn"},
    {"lang": "uk", "native": "зупини музику",
     "exp_intent": "media.pause", "exp_params": {}, "twist": "syn"},

    # ── weather variations ──
    {"lang": "en", "native": "is it cold outside",
     "exp_intent": "weather.current", "exp_params": {}, "twist": "indirect"},
    {"lang": "en", "native": "weather please",
     "exp_intent": "weather.current", "exp_params": {}, "twist": "short"},
    {"lang": "uk", "native": "розкажи яка погода",
     "exp_intent": "weather.current", "exp_params": {}, "twist": "polite"},

    # ── timer / clock ──
    {"lang": "en", "native": "remind me in 10 minutes",
     "exp_intent": "clock.set_timer",
     "exp_params": {}, "twist": "syn"},
    {"lang": "en", "native": "ten minute timer",
     "exp_intent": "clock.set_timer",
     "exp_params": {}, "twist": "short"},
    {"lang": "uk", "native": "став таймер на десять хвилин",
     "exp_intent": "clock.set_timer",
     "exp_params": {}, "twist": "syn"},

    # ── privacy ──
    {"lang": "en", "native": "stop listening",
     "exp_intent": "privacy_on", "exp_params": {}, "twist": "indirect"},
    {"lang": "en", "native": "go private",
     "exp_intent": "privacy_on", "exp_params": {}, "twist": "casual"},
    {"lang": "uk", "native": "не слухай мене",
     "exp_intent": "privacy_on", "exp_params": {}, "twist": "indirect"},

    # ── unknown / weird ──
    {"lang": "en", "native": "what is the meaning of life",
     "exp_intent": "unknown", "exp_params": {}, "twist": "indirect"},
    {"lang": "en", "native": "sing me a song",
     "exp_intent": "unknown", "exp_params": {}, "twist": "casual"},
    {"lang": "uk", "native": "як справи",
     "exp_intent": "unknown", "exp_params": {}, "twist": "casual"},
    {"lang": "en", "native": "open the garage door",  # not in registry
     "exp_intent": "unknown", "exp_params": {}, "twist": "syn"},
    {"lang": "en", "native": "asdf qwerty",
     "exp_intent": "unknown", "exp_params": {}, "twist": "casual"},
]


async def _bootstrap_db():
    from sqlalchemy.ext.asyncio import (
        AsyncSession, async_sessionmaker, create_async_engine,
    )
    from core.module_loader.sandbox import get_sandbox
    from system_modules.llm_engine.intent_compiler import get_intent_compiler

    sandbox = get_sandbox()
    if sandbox._session_factory is None:
        db_path = "/var/lib/selena/selena.db"
        if not Path(db_path).is_file():
            db_path = "/var/lib/selena/db/selena.db"
        eng = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
        sandbox._session_factory = async_sessionmaker(
            eng, class_=AsyncSession, expire_on_commit=False,
        )
    await get_intent_compiler().full_reload()


async def main() -> None:
    from core.translation.local_translator import get_input_translator
    from system_modules.llm_engine.intent_router import get_intent_router

    await _bootstrap_db()

    translator = get_input_translator()
    router = get_intent_router()

    print(f"Variety stress bench (rephrasings of canonical 40-case corpus)")
    print(f"Cases: {len(CORPUS)}")
    print(f"Twists: {dict(Counter(c['twist'] for c in CORPUS))}")
    print("Pipeline: native → Helsinki → router.route() (full production chain)")
    print("=" * 80)
    print("Pre-warming embedding classifier …")
    t_warm = time.perf_counter()
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, router.warmup_embedding)
    print(f"  warmup done in {(time.perf_counter()-t_warm)*1000:.0f} ms")
    print()

    results: list[dict] = []
    passed = 0
    latencies: list[float] = []
    by_source: Counter = Counter()
    by_twist_pass: Counter = Counter()
    by_twist_total: Counter = Counter()

    for i, case in enumerate(CORPUS, 1):
        lang = case["lang"]
        native = case["native"]
        exp_intent = case["exp_intent"]
        exp_params = case["exp_params"]
        twist = case["twist"]

        if lang == "en":
            en_text = native
        else:
            en_text = translator.to_english(native, lang) or native

        t0 = time.perf_counter()
        result = await router.route(en_text, lang=lang, native_text=native)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        latencies.append(elapsed_ms)
        by_source[result.source] += 1
        by_twist_total[twist] += 1

        intent_ok = result.intent == exp_intent
        params_ok = all(
            (result.params or {}).get(k) == v for k, v in exp_params.items()
        )
        ok = intent_ok and params_ok
        if ok:
            passed += 1
            by_twist_pass[twist] += 1

        mark = "✓" if ok else "✗"
        print(
            f"  {i:2d}. [{lang}/{twist:<8}] {mark}  "
            f"'{native[:32]:<32}'  src={result.source:<10} {elapsed_ms:6.0f}ms"
        )
        if not ok:
            print(f"        EN:  '{en_text}'")
            print(f"        exp: {exp_intent} {exp_params}")
            print(f"        got: {result.intent} {result.params}")

        results.append({
            "i": i, "lang": lang, "native": native, "en": en_text,
            "twist": twist,
            "expected_intent": exp_intent, "expected_params": exp_params,
            "got_intent": result.intent, "got_params": result.params,
            "source": result.source, "ms": round(elapsed_ms, 1), "pass": ok,
            "raw": result.raw_llm,
        })

    p50 = statistics.median(latencies)
    p95 = (
        statistics.quantiles(latencies, n=20)[18]
        if len(latencies) >= 20 else max(latencies)
    )
    accuracy = passed / len(CORPUS) * 100

    print("=" * 80)
    print(f"Accuracy:        {passed}/{len(CORPUS)}  ({accuracy:.1f}%)")
    print(f"Latency:         p50={p50:.0f}ms  p95={p95:.0f}ms")
    print(f"Sources:         {dict(by_source)}")
    print()
    print("Accuracy by twist (which kinds of variation hold up):")
    for twist in sorted(by_twist_total):
        p = by_twist_pass[twist]
        t = by_twist_total[twist]
        bar = "█" * int(p / t * 20)
        print(f"  {twist:<10} {p:>2}/{t:<2}  {bar:<20}  {p/t*100:.0f}%")

    out_path = Path("/opt/selena-core/tests/experiments/results/variety_bench_results.json")
    if not out_path.parent.is_dir():
        out_path = _here.parent / "results" / "variety_bench_results.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({
        "accuracy_pct": round(accuracy, 1),
        "passed": passed, "total": len(CORPUS),
        "p50_ms": round(p50, 1), "p95_ms": round(p95, 1),
        "sources": dict(by_source),
        "by_twist": {
            t: {"pass": by_twist_pass[t], "total": by_twist_total[t]}
            for t in by_twist_total
        },
        "cases": results,
    }, ensure_ascii=False, indent=2))
    print(f"\nJSON: {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
