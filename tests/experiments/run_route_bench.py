#!/usr/bin/env python3
"""
tests/experiments/run_route_bench.py — production-path benchmark.

Unlike run_trace_bench.py (which traces individual pipeline steps in
isolation) and run_embedding_bench.py (which calls
EmbeddingIntentClassifier directly), this bench drives the FULL
IntentRouter.route() chain:

    Module Bus  →  Embedding (Tier 1)  →  Assistant LLM (Tier 2)  →  Fallback

Each case is reported with the tier that actually produced the result
(source field on IntentResult), so you can see how often the embedding
short-circuits the assistant and how often it falls through.

Run inside the selena-core container:

    docker compose exec -T core python3 \\
        /opt/selena-core/tests/experiments/run_route_bench.py
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

# Re-use the same 40-case corpus as the experimental embedding bench.
from tests.experiments.run_embedding_bench import CORPUS  # noqa: E402


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

    print(f"Production-path bench (IntentRouter.route, full chain)")
    print(f"Corpus: {len(CORPUS)} cases")
    print("Pipeline: native → Helsinki → Module Bus → Embedding → Local LLM → Cloud LLM")
    print("=" * 76)

    # Pre-warm embedding classifier so the first case isn't penalised
    # by the ~10-30 sec model load.
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
    fail_cases: list[int] = []

    for i, case in enumerate(CORPUS, 1):
        lang = case["lang"]
        native = case["native"]
        exp_intent = case["exp_intent"]
        exp_params = case["exp_params"]

        # Step 1: translate to English (Helsinki for non-EN)
        if lang == "en":
            en_text = native
        else:
            en_text = translator.to_english(native, lang) or native

        # Step 2: full route() — exercises the live tier chain
        t0 = time.perf_counter()
        result = await router.route(
            en_text, lang=lang, native_text=native,
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000
        latencies.append(elapsed_ms)
        by_source[result.source] += 1

        intent_ok = result.intent == exp_intent
        params_ok = all(
            (result.params or {}).get(k) == v for k, v in exp_params.items()
        )
        ok = intent_ok and params_ok
        if ok:
            passed += 1
        else:
            fail_cases.append(i)

        mark = "✓" if ok else "✗"
        print(
            f"  {i:2d}. [{lang}] {mark}  '{native[:36]:<36}'  "
            f"src={result.source:<10} {elapsed_ms:6.0f}ms"
        )
        if not ok:
            print(f"        EN:  '{en_text}'")
            print(f"        exp: {exp_intent} {exp_params}")
            print(f"        got: {result.intent} {result.params}")
            if result.raw_llm:
                print(f"        meta: {result.raw_llm}")

        results.append({
            "i": i, "lang": lang, "native": native, "en": en_text,
            "expected_intent": exp_intent, "expected_params": exp_params,
            "got_intent": result.intent, "got_params": result.params,
            "source": result.source, "ms": round(elapsed_ms, 1),
            "pass": ok, "raw": result.raw_llm,
        })

    # Summary
    p50 = statistics.median(latencies)
    p95 = (
        statistics.quantiles(latencies, n=20)[18]
        if len(latencies) >= 20 else max(latencies)
    )
    accuracy = passed / len(CORPUS) * 100

    print("=" * 76)
    print(f"Accuracy:        {passed}/{len(CORPUS)}  ({accuracy:.1f}%)")
    print(f"Latency:         p50={p50:.0f}ms  p95={p95:.0f}ms")
    print(f"Sources:         {dict(by_source)}")
    if fail_cases:
        print(f"Failing cases:   {fail_cases}")
    print()
    print("Comparison (qwen 1.5b + Helsinki):")
    print(f"  LLM-only path:   35/40  (87.5%)  p50≈2548ms")
    print(f"  Production route:{passed:>3}/40  ({accuracy:.1f}%)  p50≈{p50:.0f}ms")
    if p50 > 0:
        print(f"  Speedup:         ~{2548 / p50:.1f}× faster end-to-end")

    out_path = Path("/opt/selena-core/_private/route_bench_results.json")
    if not out_path.parent.is_dir():
        out_path = _here.parents[2] / "_private" / "route_bench_results.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({
        "accuracy_pct": round(accuracy, 1),
        "passed": passed,
        "total": len(CORPUS),
        "p50_ms": round(p50, 1),
        "p95_ms": round(p95, 1),
        "sources": dict(by_source),
        "failing_cases": fail_cases,
        "cases": results,
    }, ensure_ascii=False, indent=2))
    print(f"\nJSON: {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
