#!/usr/bin/env python3
"""
tests/experiments/run_noisy_bench.py — realistic STT noise bench.

Real Vosk output is messy:
- misrecognised words ("свитло" instead of "світло")
- filler words ("ну", "это", "типа", "like", "um")
- long sentences with context ("я прийшов додому і хочу щоб було тепло")
- partial words, stuttering ("вк вк включи")
- mixed register ("please turn on ну це як його the light")

This bench simulates that noise to find where the embedding
classifier and LLM fallback break under realistic conditions.

Pipeline: native (noisy) → Helsinki → router.route()

Run inside Pi 5 or Jetson container:

    docker compose exec -T core python3 \\
        /opt/selena-core/tests/experiments/run_noisy_bench.py
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


# ── Noisy corpus ────────────────────────────────────────────────────
#
# Each entry has a `noise` tag describing the kind of degradation:
#   typo     — STT misrecognition / misspelling
#   filler   — filler words / hesitation markers
#   long     — verbose sentence with context before the command
#   stutter  — repeated / partial words
#   mixed    — code-switch / mixed languages
#   context  — command embedded in a longer conversational phrase
#   garbled  — seriously mangled STT output

CORPUS: list[dict] = [

    # ── device.on / device.off with STT noise ──

    {"lang": "uk", "native": "ну включи світло у вітальні",
     "exp_intent": "device.on", "exp_params": {"entity": "light", "location": "living room"},
     "noise": "filler"},

    {"lang": "uk", "native": "свитло увімкни будь ласка",
     "exp_intent": "device.on", "exp_params": {"entity": "light"},
     "noise": "typo"},

    {"lang": "en", "native": "um could you turn on the light in the living room please",
     "exp_intent": "device.on", "exp_params": {"entity": "light", "location": "living room"},
     "noise": "filler"},

    {"lang": "en", "native": "turn turn on the light",
     "exp_intent": "device.on", "exp_params": {"entity": "light"},
     "noise": "stutter"},

    {"lang": "uk", "native": "я прийшов додому вимкни світло у вітальні",
     "exp_intent": "device.off", "exp_params": {"entity": "light", "location": "living room"},
     "noise": "context"},

    {"lang": "en", "native": "hey so like can you turn off the lights",
     "exp_intent": "device.off", "exp_params": {"entity": "light"},
     "noise": "filler"},

    {"lang": "uk", "native": "це ну вимкни кондиціонер",
     "exp_intent": "device.off", "exp_params": {"entity": "air_conditioner"},
     "noise": "filler"},

    # ── temperature with noise ──

    {"lang": "en", "native": "set the temperature to like twenty two degrees or something",
     "exp_intent": "device.set_temperature", "exp_params": {"value": "22"},
     "noise": "filler"},

    {"lang": "uk", "native": "ну встанови там двадцять два градуси на кондиціонері",
     "exp_intent": "device.set_temperature", "exp_params": {},
     "noise": "filler"},

    {"lang": "en", "native": "i just came home and it is really cold can you set the ac to twenty two",
     "exp_intent": "device.set_temperature", "exp_params": {"value": "22"},
     "noise": "long"},

    {"lang": "uk", "native": "яка там температура у вітальні скажи",
     "exp_intent": "device.query_temperature",
     "exp_params": {"location": "living room"},
     "noise": "context"},

    {"lang": "en", "native": "what's the temp in the living room right now",
     "exp_intent": "device.query_temperature",
     "exp_params": {"location": "living room"},
     "noise": "context"},

    # ── set_mode / set_fan_speed with noise ──

    {"lang": "en", "native": "uh set it to cool mode the air conditioner",
     "exp_intent": "device.set_mode", "exp_params": {"value": "cool"},
     "noise": "filler"},

    {"lang": "uk", "native": "швидкість вентилятора на високу постав",
     "exp_intent": "device.set_fan_speed", "exp_params": {"value": "high"},
     "noise": "context"},

    # ── lock / unlock garbled ──

    {"lang": "en", "native": "lock the the front door now",
     "exp_intent": "device.lock", "exp_params": {"entity": "lock"},
     "noise": "stutter"},

    {"lang": "uk", "native": "ну відімкни двері вхідні будь ласка",
     "exp_intent": "device.unlock", "exp_params": {},
     "noise": "filler"},

    {"lang": "en", "native": "can you please make sure the front door is locked",
     "exp_intent": "device.lock", "exp_params": {},
     "noise": "long"},

    # ── media with noise ──

    {"lang": "en", "native": "play some like jazz or something on the radio",
     "exp_intent": "media.play_genre", "exp_params": {"genre": "jazz"},
     "noise": "filler"},

    {"lang": "uk", "native": "ну постав якийсь джаз на радіо",
     "exp_intent": "media.play_genre", "exp_params": {"genre": "jazz"},
     "noise": "filler"},

    {"lang": "en", "native": "wait wait pause the music real quick",
     "exp_intent": "media.pause", "exp_params": {},
     "noise": "stutter"},

    {"lang": "uk", "native": "стоп зупини музику",
     "exp_intent": "media.pause", "exp_params": {},
     "noise": "context"},

    # ── weather with noise ──

    {"lang": "en", "native": "hey what's the weather like outside today",
     "exp_intent": "weather.current", "exp_params": {},
     "noise": "filler"},

    {"lang": "uk", "native": "слухай яка там погода надворі сьогодні",
     "exp_intent": "weather.current", "exp_params": {},
     "noise": "filler"},

    {"lang": "en", "native": "i'm going out is it raining or what",
     "exp_intent": "weather.current", "exp_params": {},
     "noise": "long"},

    # ── timer with noise ──

    {"lang": "en", "native": "uh set a timer for like ten minutes",
     "exp_intent": "clock.set_timer", "exp_params": {},
     "noise": "filler"},

    {"lang": "uk", "native": "ну постав таймер хвилин на десять",
     "exp_intent": "clock.set_timer", "exp_params": {},
     "noise": "filler"},

    {"lang": "en", "native": "i need a timer ten minutes for the pasta",
     "exp_intent": "clock.set_timer", "exp_params": {},
     "noise": "context"},

    # ── privacy with noise ──

    {"lang": "en", "native": "hey stop listening to me right now",
     "exp_intent": "privacy_on", "exp_params": {},
     "noise": "context"},

    {"lang": "uk", "native": "ну все режим приватності увімкни",
     "exp_intent": "privacy_on", "exp_params": {},
     "noise": "filler"},

    # ── unknown with noise ──

    {"lang": "en", "native": "so uh tell me a joke or something funny",
     "exp_intent": "unknown", "exp_params": {},
     "noise": "filler"},

    {"lang": "uk", "native": "ну розкажи щось цікаве",
     "exp_intent": "unknown", "exp_params": {},
     "noise": "filler"},

    {"lang": "en", "native": "blah blah blah i don't know what i want",
     "exp_intent": "unknown", "exp_params": {},
     "noise": "garbled"},

    {"lang": "uk", "native": "що ти взагалі вмієш робити",
     "exp_intent": "unknown", "exp_params": {},
     "noise": "context"},

    {"lang": "en", "native": "open the curtains in the bedroom please",
     "exp_intent": "unknown", "exp_params": {},
     "noise": "context"},

    {"lang": "en", "native": "ajsdfk klsjdf lkj",
     "exp_intent": "unknown", "exp_params": {},
     "noise": "garbled"},

    # ── device.on with long context ──

    {"lang": "en", "native": "i just got home from work and it is really dark in here can you turn on the lights in the living room",
     "exp_intent": "device.on", "exp_params": {"entity": "light", "location": "living room"},
     "noise": "long"},

    {"lang": "uk", "native": "мені холодно увімкни кондиціонер у вітальні на обігрів",
     "exp_intent": "device.on", "exp_params": {"entity": "air_conditioner", "location": "living room"},
     "noise": "long"},

    {"lang": "en", "native": "the bedroom is too dry turn on the humidifier",
     "exp_intent": "device.on", "exp_params": {"entity": "humidifier", "location": "bedroom"},
     "noise": "context"},

    {"lang": "uk", "native": "чайник вимкни на кухні він вже закипів",
     "exp_intent": "device.off", "exp_params": {"location": "kitchen"},
     "noise": "context"},

    {"lang": "en", "native": "what is the current temperature reading in the living room sensor",
     "exp_intent": "device.query_temperature",
     "exp_params": {"location": "living room"},
     "noise": "long"},
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

    noise_counts = Counter(c["noise"] for c in CORPUS)
    print(f"Noisy / realistic STT bench — {len(CORPUS)} cases")
    print(f"Noise types: {dict(noise_counts)}")
    print("Pipeline: native (noisy) → Helsinki → router.route()")
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
    by_noise_pass: Counter = Counter()
    by_noise_total: Counter = Counter()

    for i, case in enumerate(CORPUS, 1):
        lang = case["lang"]
        native = case["native"]
        exp_intent = case["exp_intent"]
        exp_params = case["exp_params"]
        noise = case["noise"]

        if lang == "en":
            en_text = native
        else:
            en_text = translator.to_english(native, lang) or native

        t0 = time.perf_counter()
        result = await router.route(en_text, lang=lang, native_text=native)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        latencies.append(elapsed_ms)
        by_source[result.source] += 1
        by_noise_total[noise] += 1

        intent_ok = result.intent == exp_intent
        params_ok = all(
            (result.params or {}).get(k) == v for k, v in exp_params.items()
        )
        ok = intent_ok and params_ok
        if ok:
            passed += 1
            by_noise_pass[noise] += 1

        mark = "✓" if ok else "✗"
        print(
            f"  {i:2d}. [{lang}/{noise:<7}] {mark}  "
            f"'{native[:35]:<35}'  src={result.source:<10} {elapsed_ms:6.0f}ms"
        )
        if not ok:
            print(f"        EN:  '{en_text}'")
            print(f"        exp: {exp_intent} {exp_params}")
            print(f"        got: {result.intent} {result.params}")

        results.append({
            "i": i, "lang": lang, "native": native, "en": en_text,
            "noise": noise,
            "expected_intent": exp_intent, "expected_params": exp_params,
            "got_intent": result.intent, "got_params": result.params,
            "source": result.source, "ms": round(elapsed_ms, 1), "pass": ok,
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
    print("Accuracy by noise type:")
    for noise in sorted(by_noise_total):
        p = by_noise_pass[noise]
        t = by_noise_total[noise]
        bar = "█" * int(p / t * 20) if t else ""
        print(f"  {noise:<10} {p:>2}/{t:<2}  {bar:<20}  {p/t*100:.0f}%")

    out_path = Path("/opt/selena-core/tests/experiments/results/noisy_bench_results.json")
    if not out_path.parent.is_dir():
        out_path = _here.parent / "results" / "noisy_bench_results.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({
        "accuracy_pct": round(accuracy, 1),
        "passed": passed, "total": len(CORPUS),
        "p50_ms": round(p50, 1), "p95_ms": round(p95, 1),
        "sources": dict(by_source),
        "by_noise": {
            n: {"pass": by_noise_pass[n], "total": by_noise_total[n]}
            for n in by_noise_total
        },
        "cases": results,
    }, ensure_ascii=False, indent=2))
    print(f"\nJSON: {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
