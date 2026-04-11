"""
tests/benchmark/run_intent_bench.py — LLM intent classification benchmark.

Runs the unified ``system`` prompt against a fixed corpus of English
commands on every configured Ollama model and reports accuracy, JSON
validity, latency (p50/p95) and tokens/sec in a markdown table.

Usage (inside the selena-core container):

    docker compose exec core python tests/benchmark/run_intent_bench.py \\
        --corpus tests/benchmark/intent_corpus.jsonl \\
        --models qwen2.5:0.5b,qwen2.5:1.5b,qwen2.5:3b,phi3-mini:3.8b,gemma3:1b \\
        --runs 1 \\
        --out /tmp/intent_bench.json

The script reuses ``core.llm.llm_call`` so the prompt, catalog injection
and JSON-mode settings match production exactly.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

# Ensure repo root is on sys.path whether invoked from inside the container
# (/opt/selena-core) or from a dev checkout.
_here = Path(__file__).resolve()
for candidate in (_here.parents[2], Path("/opt/selena-core")):
    if (candidate / "core" / "llm.py").is_file() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))


async def _ensure_bootstrap() -> None:
    """Wire up the sandbox session factory so the router can read DB.

    The benchmark runs in a standalone ``docker exec python3 …`` process —
    there is no core lifespan to seed ``get_sandbox()._session_factory``.
    We create an AsyncEngine against the live selena.db so the filtered
    catalog builder has DB access.
    """
    from core.module_loader.sandbox import get_sandbox
    sandbox = get_sandbox()
    if sandbox._session_factory is not None:
        return

    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
    db_path = "/var/lib/selena/selena.db"
    if not Path(db_path).is_file():
        db_path = "/var/lib/selena/db/selena.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    sandbox._session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    from system_modules.llm_engine.intent_compiler import get_intent_compiler
    try:
        await get_intent_compiler().full_reload()
    except Exception as exc:
        print(f"  ! IntentCompiler.full_reload: {exc}")


async def _classify_once(text: str, model: str) -> tuple[str, float, int]:
    """Return (raw_output, seconds, catalog_chars) for one classification."""
    from core.llm import llm_call
    from system_modules.llm_engine.intent_router import get_intent_router

    # Per-request filtered catalog — that's the whole point of the new
    # router. Latency includes the catalog build; it's still cheaper than
    # sending 45 intents every time.
    catalog, _allowed = await get_intent_router()._build_filtered_catalog(text)

    import os
    os.environ["OLLAMA_MODEL"] = model

    t0 = time.perf_counter()
    raw = await llm_call(
        text,
        prompt_key="intent",
        extra_context=catalog,
        temperature=0.1,
        max_tokens=256,
        timeout=30.0,
        num_ctx=4096,
    )
    dt = time.perf_counter() - t0
    return raw, dt, len(catalog)


def _parse_json(raw: str) -> dict[str, Any] | None:
    if not raw:
        return None
    s = raw.strip()
    if s.startswith("```"):
        s = s.strip("`")
        if s.startswith("json"):
            s = s[4:]
    a = s.find("{")
    b = s.rfind("}")
    if a == -1 or b == -1:
        return None
    try:
        return json.loads(s[a:b + 1])
    except Exception:
        return None


def _check(expected: dict[str, Any], parsed: dict[str, Any] | None) -> tuple[bool, bool]:
    """Return (intent_ok, params_ok)."""
    if not parsed:
        return False, False
    exp_intent = expected.get("intent", "")
    got_intent = parsed.get("intent", "")
    intent_ok = exp_intent == got_intent
    params_ok = True
    exp_params = expected.get("params") or {}
    got_params = parsed.get("params") or {}
    for k, v in exp_params.items():
        if str(got_params.get(k, "")).lower() != str(v).lower():
            params_ok = False
            break
    return intent_ok, params_ok


async def _run_model(
    model: str, corpus: list[dict[str, Any]], runs: int,
) -> dict[str, Any]:
    latencies: list[float] = []
    catalog_sizes: list[int] = []
    intent_hits = 0
    params_hits = 0
    json_valid = 0
    errors = 0
    total = 0
    misses: list[dict[str, Any]] = []

    for case in corpus:
        for _ in range(runs):
            total += 1
            try:
                raw, dt, cat_len = await _classify_once(case["text"], model)
                catalog_sizes.append(cat_len)
            except Exception as exc:
                print(f"  ! {model} → error: {exc}")
                errors += 1
                continue
            latencies.append(dt * 1000)
            parsed = _parse_json(raw)
            if parsed:
                json_valid += 1
            intent_ok, params_ok = _check(case["expected"], parsed)
            if intent_ok:
                intent_hits += 1
            if params_ok:
                params_hits += 1
            if not intent_ok:
                misses.append({
                    "text": case["text"],
                    "expected": case["expected"].get("intent"),
                    "got": (parsed or {}).get("intent"),
                    "raw": raw[:200],
                })

    def pct(n: int) -> float:
        return (n / total * 100) if total else 0.0

    p50 = statistics.median(latencies) if latencies else 0.0
    p95 = (
        statistics.quantiles(latencies, n=20)[18]
        if len(latencies) >= 20
        else (max(latencies) if latencies else 0.0)
    )

    avg_catalog = int(sum(catalog_sizes) / len(catalog_sizes)) if catalog_sizes else 0
    return {
        "model": model,
        "total": total,
        "errors": errors,
        "intent_acc": round(pct(intent_hits), 1),
        "params_acc": round(pct(params_hits), 1),
        "json_valid": round(pct(json_valid), 1),
        "p50_ms": int(p50),
        "p95_ms": int(p95),
        "avg_catalog_chars": avg_catalog,
        "misses": misses,
    }


def _print_table(rows: list[dict[str, Any]]) -> None:
    print()
    print("| model             | intent_acc | params_acc | json_valid | p50 ms | p95 ms | cat_chars |")
    print("|-------------------|-----------:|-----------:|-----------:|-------:|-------:|----------:|")
    for r in rows:
        print(
            f"| {r['model']:<17} | "
            f"{r['intent_acc']:>9.1f}% | {r['params_acc']:>9.1f}% | "
            f"{r['json_valid']:>9.1f}% | {r['p50_ms']:>6} | {r['p95_ms']:>6} | "
            f"{r.get('avg_catalog_chars', 0):>9} |"
        )
    print()


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default="tests/benchmark/intent_corpus.jsonl")
    ap.add_argument(
        "--models",
        default="qwen2.5:0.5b,qwen2.5:1.5b,qwen2.5:3b,phi3-mini:3.8b,gemma3:1b",
    )
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    corpus_path = Path(args.corpus)
    corpus = [json.loads(line) for line in corpus_path.read_text().splitlines() if line.strip()]
    print(f"Loaded {len(corpus)} test cases from {corpus_path}")

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    print(f"Running {len(models)} models × {args.runs} run(s)")

    await _ensure_bootstrap()
    print("Router bootstrap OK — per-request catalog filtering")

    results: list[dict[str, Any]] = []
    for model in models:
        print(f"\n→ {model}")
        row = await _run_model(model, corpus, args.runs)
        results.append(row)
        print(
            f"   intent={row['intent_acc']}%  params={row['params_acc']}%  "
            f"json={row['json_valid']}%  p50={row['p50_ms']}ms  p95={row['p95_ms']}ms"
        )

    _print_table(results)

    if args.out:
        Path(args.out).write_text(json.dumps({
            "corpus": str(corpus_path),
            "runs": args.runs,
            "results": results,
        }, indent=2))
        print(f"Full results written to {args.out}")


if __name__ == "__main__":
    asyncio.run(main())
