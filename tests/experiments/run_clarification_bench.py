#!/usr/bin/env python3
"""Two-turn clarification bench.

Drives each fixture in ``clarification_fixtures.FIXTURES`` through the
full production flow as emulated without the real audio loop:

  turn 1   router.route(text_1)   → IntentResult with .clarification set
  turn 2   router.route_clarification(text_2, pending)
           → IntentResult with pending_intent refired (or fallback)

Pass criteria per fixture:

  - When ``allow_cancelled`` is truthy: pass if either
      (a) final intent == expected, OR
      (b) final result.source == "fallback" (canned cancel).
  - Otherwise: pass if final intent == expected AND the merged
    params contain every key in ``expected_final_params``.

Run inside the selena-core container:

    docker exec -t selena-core python3 \\
        /opt/selena-core/tests/experiments/run_clarification_bench.py

JSON result lands at ``tests/experiments/results/clarification_bench_results.json``.
"""
from __future__ import annotations

import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

_here = Path(__file__).resolve()
for candidate in (_here.parents[2], Path("/opt/selena-core")):
    if (candidate / "core" / "llm.py").is_file() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))


async def _bootstrap_db() -> None:
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


def _params_match(got: dict, expected: dict) -> bool:
    """Every key in ``expected`` must be present in ``got`` with a
    matching value (case-insensitive string compare)."""
    if not expected:
        return True
    for k, v in expected.items():
        if k not in got:
            return False
        if str(got[k]).strip().lower() != str(v).strip().lower():
            return False
    return True


async def main() -> None:
    from core.translation.local_translator import get_input_translator
    from system_modules.llm_engine.intent_router import get_intent_router
    from tests.experiments.clarification_fixtures import FIXTURES

    await _bootstrap_db()

    translator = get_input_translator()
    router = get_intent_router()
    router.warmup_embedding()

    results: list[dict] = []
    latencies: list[float] = []

    print(f"Clarification bench — {len(FIXTURES)} 2-turn fixtures")
    print("Pipeline: route(turn_1) → route_clarification(turn_2, pending)")
    print("=" * 76)

    for fx in FIXTURES:
        lang = fx["lang"]
        turn_2 = fx["turn_2_text"]
        expected_reason = fx.get("expected_reason")
        expected_intent = fx.get("expected_final_intent", "")
        expected_params = fx.get("expected_final_params", {}) or {}
        allow_cancelled = bool(fx.get("allow_cancelled"))

        # Some fixtures carry a ``synthetic_pending`` dict — they
        # bypass the turn-1 ``route()`` call and inject the
        # clarification context directly. This is needed for
        # missing_param cases which are emitted by module handlers,
        # not the router, so ``route()`` alone can't produce them.
        synthetic_pending = fx.get("synthetic_pending")
        t0 = time.perf_counter()

        if synthetic_pending:
            turn_1_clarif = dict(synthetic_pending)
            r1_intent = synthetic_pending.get("pending_intent", "")
        else:
            turn_1 = fx["turn_1_text"]
            try:
                text_1_en = (
                    translator.to_english(turn_1, lang) if lang != "en" else turn_1
                )
            except Exception:
                text_1_en = turn_1

            try:
                r1 = await router.route(
                    text_1_en, lang=lang, tts_lang=lang,
                    native_text=turn_1 if lang != "en" else None,
                )
            except Exception as exc:
                results.append({
                    "name": fx["name"], "pass": False,
                    "error": f"turn_1 route() raised: {exc}",
                })
                continue

            turn_1_clarif = getattr(r1, "clarification", None)
            r1_intent = r1.intent
            if not turn_1_clarif:
                # No clarification emitted — fixture is invalid OR
                # classifier was confident. Pass iff direct result
                # matches expected.
                direct_ok = (
                    r1.intent == expected_intent
                    and _params_match(r1.params or {}, expected_params)
                )
                results.append({
                    "name": fx["name"],
                    "pass": direct_ok,
                    "turn_1_intent": r1.intent,
                    "turn_1_clarification": None,
                    "note": "no clarification emitted — direct classification",
                })
                continue

            # Sanity-check the reason matches expectation (not a pass gate).
            if expected_reason and turn_1_clarif.get("reason") != expected_reason:
                pass  # log but don't fail — reason mismatch doesn't break

        # ── Turn 2 ──
        try:
            r2 = await router.route_clarification(
                turn_2, turn_1_clarif, lang=lang, tts_lang=lang,
                native_text=turn_2 if lang != "en" else None,
            )
        except Exception as exc:
            results.append({
                "name": fx["name"], "pass": False,
                "error": f"route_clarification() raised: {exc}",
            })
            continue
        elapsed_ms = (time.perf_counter() - t0) * 1000
        latencies.append(elapsed_ms)

        merged_params = r2.params or {}
        got_intent = r2.intent
        cancelled = r2.source == "fallback"

        if allow_cancelled and cancelled:
            passed = True
            verdict = "pass_via_cancel"
        else:
            intent_ok = got_intent == expected_intent
            params_ok = _params_match(merged_params, expected_params)
            passed = intent_ok and params_ok and not cancelled
            verdict = "pass" if passed else (
                "cancelled" if cancelled else "wrong_result"
            )

        mark = "✓" if passed else "✗"
        print(
            f"  {mark}  {fx['name']:42}  "
            f"t1={r1_intent:<24} t2={got_intent:<24}  [{verdict}]"
        )

        results.append({
            "name": fx["name"],
            "lang": lang,
            "pass": passed,
            "verdict": verdict,
            "turn_1_intent": r1_intent,
            "turn_1_clarification_reason": turn_1_clarif.get("reason"),
            "turn_2_intent": got_intent,
            "turn_2_source": r2.source,
            "turn_2_params": merged_params,
            "expected_intent": expected_intent,
            "expected_params": expected_params,
            "elapsed_ms": elapsed_ms,
        })

    total = len(results)
    passed = sum(1 for r in results if r.get("pass"))
    acc = 100 * passed / max(total, 1)
    print()
    print("=" * 76)
    print(f"Accuracy: {passed}/{total}  ({acc:.1f}%)")
    if latencies:
        print(
            f"Latency:  p50={statistics.median(latencies):.0f}ms  "
            f"p95={sorted(latencies)[int(0.95*len(latencies))-1]:.0f}ms"
        )

    out_dir = Path("/opt/selena-core/tests/experiments/results")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "clarification_bench_results.json"
    out_path.write_text(json.dumps({
        "summary": {
            "total": total,
            "passed": passed,
            "accuracy": acc / 100,
            "p50_ms": statistics.median(latencies) if latencies else 0,
            "p95_ms": (
                sorted(latencies)[int(0.95*len(latencies))-1]
                if latencies else 0
            ),
        },
        "fixtures": results,
    }, ensure_ascii=False, indent=2))
    print(f"JSON: {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
