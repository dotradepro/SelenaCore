#!/usr/bin/env python3
"""
tests/experiments/run_embedding_bench.py — embedding-classifier benchmark.

Mirrors the production voice pipeline but replaces the LLM classify
step with an EmbeddingIntentClassifier:

    native utterance
      → InputTranslator.to_english (Helsinki for non-EN, pass-through for EN)
      → IntentRouter._build_filtered_catalog (token filter, gives 3-15 candidates)
      → EmbeddingIntentClassifier.classify (ONNX Runtime cosine)
      → hallucination guard against `allowed` set
      → verdict against expected intent + params

Run inside the selena-core container — needs DB + Helsinki model
files which are mounted there:

    docker compose exec -T core python3 \\
        /opt/selena-core/tests/experiments/run_embedding_bench.py

Requires ONNX embedding model at intent.embedding_model_dir
(default: /var/lib/selena/models/embedding/all-MiniLM-L6-v2).
Dependencies: onnxruntime, tokenizers, numpy (all in requirements.txt).
"""
from __future__ import annotations

import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

# Make sure we can import core/system_modules whether the script is
# run from /opt/selena-core inside the container or from a checkout.
_here = Path(__file__).resolve()
for candidate in (_here.parents[2], Path("/opt/selena-core")):
    if (candidate / "core" / "llm.py").is_file() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from system_modules.llm_engine.embedding_classifier import EmbeddingIntentClassifier


# ── Corpus ──────────────────────────────────────────────────────────
# Native utterances + expected intent and params. Same content as
# tests/benchmark/full_corpus.jsonl but flat Python so the file is
# self-contained for an experiment.

CORPUS: list[dict] = [
    {"lang": "en", "native": "turn on the light in the living room",
     "exp_intent": "device.on",
     "exp_params": {"entity": "light", "location": "living room"}},
    {"lang": "en", "native": "turn off the light in the living room",
     "exp_intent": "device.off",
     "exp_params": {"entity": "light", "location": "living room"}},
    {"lang": "uk", "native": "включи світло у вітальні",
     "exp_intent": "device.on",
     "exp_params": {"entity": "light", "location": "living room"}},
    {"lang": "uk", "native": "увімкни світло у вітальні",
     "exp_intent": "device.on",
     "exp_params": {"entity": "light", "location": "living room"}},
    {"lang": "uk", "native": "виключи світло у вітальні",
     "exp_intent": "device.off",
     "exp_params": {"entity": "light", "location": "living room"}},
    {"lang": "uk", "native": "вимкни світло у вітальні",
     "exp_intent": "device.off",
     "exp_params": {"entity": "light", "location": "living room"}},
    {"lang": "en", "native": "turn on the air conditioner",
     "exp_intent": "device.on",
     "exp_params": {"entity": "air_conditioner"}},
    {"lang": "uk", "native": "увімкни кондиціонер у вітальні",
     "exp_intent": "device.on",
     "exp_params": {"entity": "air_conditioner", "location": "living room"}},
    {"lang": "uk", "native": "вимкни кондиціонер",
     "exp_intent": "device.off",
     "exp_params": {"entity": "air_conditioner"}},
    {"lang": "en", "native": "set the air conditioner to 22 degrees",
     "exp_intent": "device.set_temperature",
     "exp_params": {"entity": "air_conditioner", "value": "22"}},
    {"lang": "uk", "native": "встанови кондиціонер на 22 градуси",
     "exp_intent": "device.set_temperature",
     "exp_params": {"value": "22"}},
    {"lang": "en", "native": "set the fan speed to high",
     "exp_intent": "device.set_fan_speed",
     "exp_params": {"value": "high"}},
    {"lang": "uk", "native": "встанови швидкість вентилятора на високу",
     "exp_intent": "device.set_fan_speed",
     "exp_params": {"value": "high"}},
    {"lang": "en", "native": "set cool mode on the air conditioner",
     "exp_intent": "device.set_mode",
     "exp_params": {"value": "cool"}},
    {"lang": "uk", "native": "встанови режим охолодження",
     "exp_intent": "device.set_mode",
     "exp_params": {"value": "cool"}},
    {"lang": "en", "native": "what is the temperature in the living room",
     "exp_intent": "device.query_temperature",
     "exp_params": {"location": "living room"}},
    {"lang": "uk", "native": "яка температура у вітальні",
     "exp_intent": "device.query_temperature",
     "exp_params": {"location": "living room"}},
    {"lang": "en", "native": "lock the front door",
     "exp_intent": "device.lock",
     "exp_params": {"entity": "lock"}},
    {"lang": "uk", "native": "замкни вхідні двері",
     "exp_intent": "device.lock",
     "exp_params": {}},
    {"lang": "en", "native": "unlock the front door",
     "exp_intent": "device.unlock",
     "exp_params": {"entity": "lock"}},
    {"lang": "en", "native": "play jazz radio",
     "exp_intent": "media.play_genre",
     "exp_params": {"genre": "jazz"}},
    {"lang": "uk", "native": "вмикни джазове радіо",
     "exp_intent": "media.play_genre",
     "exp_params": {"genre": "jazz"}},
    {"lang": "en", "native": "pause the music",
     "exp_intent": "media.pause",
     "exp_params": {}},
    {"lang": "uk", "native": "постав музику на паузу",
     "exp_intent": "media.pause",
     "exp_params": {}},
    {"lang": "en", "native": "what's the weather outside",
     "exp_intent": "weather.current",
     "exp_params": {}},
    {"lang": "uk", "native": "яка погода надворі",
     "exp_intent": "weather.current",
     "exp_params": {}},
    {"lang": "en", "native": "set a timer for ten minutes",
     "exp_intent": "clock.set_timer",
     "exp_params": {}},
    {"lang": "uk", "native": "постав таймер на 10 хвилин",
     "exp_intent": "clock.set_timer",
     "exp_params": {}},
    {"lang": "en", "native": "enable privacy mode",
     "exp_intent": "privacy_on",
     "exp_params": {}},
    {"lang": "uk", "native": "увімкни режим приватності",
     "exp_intent": "privacy_on",
     "exp_params": {}},
    {"lang": "en", "native": "turn on the humidifier in the bedroom",
     "exp_intent": "device.on",
     "exp_params": {"entity": "humidifier", "location": "bedroom"}},
    {"lang": "uk", "native": "увімкни зволожувач у спальні",
     "exp_intent": "device.on",
     "exp_params": {"location": "bedroom"}},
    {"lang": "en", "native": "turn off the kettle in the kitchen",
     "exp_intent": "device.off",
     "exp_params": {"location": "kitchen"}},
    {"lang": "uk", "native": "вимкни чайник на кухні",
     "exp_intent": "device.off",
     "exp_params": {"location": "kitchen"}},
    {"lang": "en", "native": "open the curtains",
     "exp_intent": "unknown", "exp_params": {}},
    {"lang": "uk", "native": "відкрий штори",
     "exp_intent": "unknown", "exp_params": {}},
    {"lang": "en", "native": "xyzzy plover quux",
     "exp_intent": "unknown", "exp_params": {}},
    {"lang": "uk", "native": "розкажи анекдот",
     "exp_intent": "unknown", "exp_params": {}},
    {"lang": "en", "native": "who are you",
     "exp_intent": "unknown", "exp_params": {}},
    {"lang": "uk", "native": "хто ти",
     "exp_intent": "unknown", "exp_params": {}},
]


def parse_catalog_to_candidates(catalog_text: str) -> list[dict]:
    """Pull intent rows out of the prompt catalog block.

    The router prints intents like ``  intent.name — description``.
    Lines that start with two spaces and contain ` — ` are intent
    rows; the device/radio sections live under different headers and
    are skipped.
    """
    candidates: list[dict] = []
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


async def _bootstrap_db():
    """Same DB bootstrap as run_trace_bench so IntentCompiler is usable."""
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
    classifier = EmbeddingIntentClassifier()

    print(f"Embedding Classifier Benchmark — {len(CORPUS)} cases")
    print(f"Model: {EmbeddingIntentClassifier.MODEL_NAME}")
    print("Pipeline: native → Helsinki EN → token filter → embedding")
    print("=" * 72)
    print("Loading model …")
    t_warmup0 = time.perf_counter()
    classifier.warmup()
    warmup_ms = (time.perf_counter() - t_warmup0) * 1000
    print(f"  warmup done in {warmup_ms:.0f} ms "
          f"({len(classifier._anchor_cache)} anchor centroids cached)")
    print()

    results: list[dict] = []
    passed = 0
    e2e_latencies: list[float] = []   # full case (translate + filter + classify)
    cls_latencies: list[float] = []   # classify-only
    low_margin: list[int] = []

    for i, case in enumerate(CORPUS, 1):
        lang = case["lang"]
        native = case["native"]
        exp_intent = case["exp_intent"]
        exp_params = case["exp_params"]

        t0 = time.perf_counter()

        # Step 1: translate to English (Helsinki for non-EN)
        if lang == "en":
            en_text = native
        else:
            en_text = translator.to_english(native, lang) or native

        # Step 2: token filter (production code path)
        catalog_text, allowed = await router._build_filtered_catalog(
            en_text, native_text=native,
        )
        candidates = parse_catalog_to_candidates(catalog_text)

        # Step 3: embedding classify (timed separately)
        t_cls0 = time.perf_counter()
        result = classifier.classify(en_text, candidates)
        cls_ms = (time.perf_counter() - t_cls0) * 1000
        cls_latencies.append(cls_ms)

        # Hallucination guard — same as production. The classifier
        # picks from `candidates` which already came from `allowed`,
        # so this should never trip in practice; kept for parity.
        if result.intent not in allowed:
            result.intent = "unknown"
            result.params = {}

        elapsed_ms = (time.perf_counter() - t0) * 1000
        e2e_latencies.append(elapsed_ms)

        # Verdict
        intent_ok = result.intent == exp_intent
        params_ok = all(result.params.get(k) == v for k, v in exp_params.items())
        ok = intent_ok and params_ok
        if ok:
            passed += 1

        if result.margin < EmbeddingIntentClassifier.MARGIN_THRESHOLD:
            low_margin.append(i)

        mark = "✓" if ok else "✗"
        print(f"  {i:2d}. [{lang}] {mark}  '{native[:38]}'")
        if not ok:
            print(f"       EN:        '{en_text}'")
            print(f"       expected:  intent={exp_intent} params={exp_params}")
            print(f"       got:       intent={result.intent} params={result.params}")
            print(f"       score:     {result.score:.3f}  margin={result.margin:.3f}")
            print(f"       runner_up: {result.runner_up} ({result.runner_up_score:.3f})")

        results.append({
            "i": i,
            "lang": lang,
            "native": native,
            "en": en_text,
            "expected_intent": exp_intent,
            "expected_params": exp_params,
            "got_intent": result.intent,
            "got_params": result.params,
            "score": round(result.score, 4),
            "runner_up": result.runner_up,
            "runner_up_score": round(result.runner_up_score, 4),
            "margin": round(result.margin, 4),
            "classify_ms": round(cls_ms, 2),
            "e2e_ms": round(elapsed_ms, 2),
            "pass": ok,
            "candidates": [c["name"] for c in candidates],
        })

    # Summary
    e2e_p50 = statistics.median(e2e_latencies)
    e2e_p95 = (
        statistics.quantiles(e2e_latencies, n=20)[18]
        if len(e2e_latencies) >= 20 else max(e2e_latencies)
    )
    cls_p50 = statistics.median(cls_latencies)
    cls_p95 = (
        statistics.quantiles(cls_latencies, n=20)[18]
        if len(cls_latencies) >= 20 else max(cls_latencies)
    )
    accuracy = passed / len(CORPUS) * 100

    print("=" * 72)
    print(f"Accuracy:        {passed}/{len(CORPUS)}  ({accuracy:.1f}%)")
    print(f"E2E latency:     p50={e2e_p50:.0f}ms  p95={e2e_p95:.0f}ms  "
          f"(translate + filter + classify)")
    print(f"Classify-only:   p50={cls_p50:.0f}ms  p95={cls_p95:.0f}ms")
    print(f"Low-margin:      {len(low_margin)} cases — {low_margin}")
    print()
    print("Comparison to LLM bench (qwen2.5:1.5b + Helsinki + prompt opts):")
    print(f"  qwen 1.5b:    35/40  (87.5%)  p50≈2548ms")
    print(f"  embedding:    {passed}/40  ({accuracy:.1f}%)  p50≈{e2e_p50:.0f}ms")
    if e2e_p50 > 0:
        print(f"  speedup:      ~{2548 / e2e_p50:.0f}× faster end-to-end")
    if cls_p50 > 0:
        print(f"  classify-only speedup: ~{2548 / cls_p50:.0f}× faster")

    # Persist
    out = {
        "model": EmbeddingIntentClassifier.MODEL_NAME,
        "warmup_ms": round(warmup_ms, 1),
        "accuracy_pct": round(accuracy, 1),
        "passed": passed,
        "total": len(CORPUS),
        "e2e_p50_ms": round(e2e_p50, 1),
        "e2e_p95_ms": round(e2e_p95, 1),
        "classify_p50_ms": round(cls_p50, 1),
        "classify_p95_ms": round(cls_p95, 1),
        "low_margin_cases": low_margin,
        "cases": results,
    }
    out_path = Path("/opt/selena-core/_private/embedding_bench_results.json")
    if not out_path.parent.is_dir():
        out_path = (
            _here.parents[2] / "_private" / "embedding_bench_results.json"
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"\nJSON: {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
