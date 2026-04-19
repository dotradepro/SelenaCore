"""
tests/benchmark/run_full_bench.py — multi-model voice pipeline benchmark.

For every Ollama model under test:
  1. Unload ALL loaded models (no cross-contamination on RAM)
  2. Rewrite ``voice.llm_model`` in core.yaml so the next request uses it
  3. Cold: one request through the core's ``/api/ui/modules/voice-core/
     test-command`` endpoint (forces the model to page into RAM)
  4. Warm corpus: the full corpus × ``--consistency`` runs
  5. Hot micro-bench: one short English phrase × 5 (best-case latency)
  6. Unload the model at the end

Everything runs through the **real voice pipeline** — Argos input
translation → keyword-filtered catalog → LLM → substring sanitizer →
device-control resolve — because that's the path a live voice command
takes. No direct Ollama API calls for classification, so the numbers
reflect end-to-end latency the user actually experiences.

Output:
  tests/experiments/results/benchmark_<timestamp>.txt     — human-readable report
  tests/experiments/results/benchmark_<timestamp>.json    — raw results for graphing

Usage (inside the selena-core container):
    docker compose exec -T core python3 \\
        /opt/selena-core/tests/benchmark/run_full_bench.py \\
        --corpus /opt/selena-core/tests/benchmark/full_corpus.jsonl \\
        --models tinyllama:latest,qwen2.5:0.5b,qwen2.5:1.5b \\
        --consistency 3
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

_here = Path(__file__).resolve()
for candidate in (_here.parents[2], Path("/opt/selena-core")):
    if (candidate / "core" / "llm.py").is_file() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))


OLLAMA_URL = "http://localhost:11434"
CORE_URL = "http://127.0.0.1:80"
TEST_COMMAND = f"{CORE_URL}/api/ui/modules/voice-core/test-command"


# ── Ollama lifecycle ───────────────────────────────────────────────────


async def _ollama_list_loaded(client) -> list[str]:
    try:
        r = await client.get(f"{OLLAMA_URL}/api/ps", timeout=10.0)
        r.raise_for_status()
        return [m["name"] for m in r.json().get("models", [])]
    except Exception:
        return []


async def _ollama_unload(client, model: str) -> None:
    try:
        await client.post(
            f"{OLLAMA_URL}/api/generate",
            json={"model": model, "prompt": "", "keep_alive": 0},
            timeout=30.0,
        )
    except Exception as exc:
        print(f"    ! unload {model}: {exc}")


async def _unload_all(client) -> None:
    """Evict every currently-resident model so the next run starts cold."""
    loaded = await _ollama_list_loaded(client)
    if not loaded:
        return
    print(f"    unloading: {loaded}")
    for m in loaded:
        await _ollama_unload(client, m)
    # small pause for ollama to flush
    await asyncio.sleep(1.0)


# ── Core config switch ────────────────────────────────────────────────


def _set_active_model(model: str) -> None:
    """Rewrite ``voice.llm_model`` in core.yaml so ``core.llm._get_provider``
    picks the new model on the very next request.

    Also clears any provider-specific override under
    ``voice.providers.ollama.model`` so the top-level ``llm_model``
    wins unambiguously.
    """
    from core.config_writer import read_config, write_config
    cfg = read_config()
    voice = cfg.setdefault("voice", {})
    voice["llm_model"] = model
    providers = voice.setdefault("providers", {})
    ollama = providers.setdefault("ollama", {})
    if ollama.get("model"):
        ollama["model"] = ""
    write_config(cfg)


# ── test-command bridge ───────────────────────────────────────────────


async def _test_command(client, text: str, lang: str, speak: bool = False) -> dict[str, Any]:
    t0 = time.perf_counter()
    resp = await client.post(
        TEST_COMMAND,
        json={"text": text, "lang": lang, "speak": speak},
        timeout=120.0,
    )
    wall_ms = (time.perf_counter() - t0) * 1000
    resp.raise_for_status()
    data = resp.json()
    return {
        "wall_ms": wall_ms,
        "intent": data.get("intent"),
        "params": data.get("params") or {},
        "source": data.get("source"),
        "latency_ms": int(data.get("latency_ms") or 0),
        "duration_ms": int(data.get("duration_ms") or 0),
        "raw_llm": (data.get("raw_llm") or "")[:300],
        "trace": data.get("trace") or [],
    }


# ── Scoring ───────────────────────────────────────────────────────────


def _check(expected: dict, got_intent: str | None, got_params: dict | None) -> tuple[bool, bool]:
    if not got_intent:
        return False, False
    exp_intent = expected.get("intent", "")
    intent_ok = exp_intent == got_intent
    params_ok = True
    exp_params = expected.get("params") or {}
    got = got_params or {}
    for k, v in exp_params.items():
        if str(got.get(k, "")).lower() != str(v).lower():
            params_ok = False
            break
    return intent_ok, params_ok


# ── RAM helpers ───────────────────────────────────────────────────────


def _ram_sample() -> dict[str, int]:
    """Return container + tegra RAM stats (MB)."""
    import subprocess
    out: dict[str, int] = {}
    try:
        r = subprocess.run(
            ["docker", "stats", "selena-core", "--no-stream", "--format", "{{.MemUsage}}"],
            check=True, capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        first = r.split(" / ")[0].strip()
        if first.endswith("GiB"):
            out["container_mb"] = int(float(first[:-3]) * 1024)
        elif first.endswith("MiB"):
            out["container_mb"] = int(float(first[:-3]))
    except Exception:
        pass
    try:
        r = subprocess.run(
            ["tegrastats", "--interval", "100"],
            capture_output=True, text=True, timeout=0.5,
        )
    except subprocess.TimeoutExpired as exc:
        line = (exc.stdout or "").splitlines()[0] if exc.stdout else ""
        if "RAM " in line:
            try:
                # "RAM 5147/7607MB"
                after = line.split("RAM ", 1)[1]
                used, total = after.split("MB", 1)[0].split("/")
                out["tegra_used_mb"] = int(used)
                out["tegra_total_mb"] = int(total)
            except Exception:
                pass
    except Exception:
        pass
    return out


# ── Model runner ──────────────────────────────────────────────────────


async def bench_model(
    client, model: str, corpus: list[dict], consistency_runs: int,
) -> dict[str, Any]:
    print(f"\n→ {model}")

    # ── Isolation: unload everything, set config ──
    await _unload_all(client)
    _set_active_model(model)
    print(f"    active model → {model}")

    ram_before = _ram_sample()
    print(f"    container RAM pre-load: {ram_before.get('container_mb', '?')} MB")

    # ── Cold: one request ──
    print("    cold first request …")
    cold_case = corpus[0]
    try:
        cold = await _test_command(client, cold_case["text"], cold_case.get("lang", "en"))
        cold_ms = cold["duration_ms"] or int(cold["wall_ms"])
        print(f"    cold duration={cold_ms} ms  intent={cold['intent']}")
    except Exception as exc:
        print(f"    ! cold failed: {exc}")
        cold_ms = 0

    ram_loaded = _ram_sample()
    print(f"    container RAM after load: {ram_loaded.get('container_mb', '?')} MB")

    # ── Warm: full corpus × N ──
    print(f"    warm corpus × {consistency_runs} ({len(corpus)} cases each) …")
    latencies: list[float] = []
    intent_hits = 0
    params_hits = 0
    total_runs = 0
    errors = 0
    misses: list[dict] = []
    per_case_hits: dict[str, int] = {}

    for run_idx in range(consistency_runs):
        for case in corpus:
            total_runs += 1
            try:
                result = await _test_command(
                    client, case["text"], case.get("lang", "en"),
                )
            except Exception as exc:
                print(f"      ! {case['text'][:40]}: {exc}")
                errors += 1
                continue
            latencies.append(result["duration_ms"] or result["wall_ms"])
            intent_ok, params_ok = _check(
                case["expected"], result["intent"], result["params"],
            )
            if intent_ok:
                intent_hits += 1
                per_case_hits[case["text"]] = per_case_hits.get(case["text"], 0) + 1
            if params_ok:
                params_hits += 1
            if not intent_ok and run_idx == 0:
                misses.append({
                    "text": case["text"],
                    "lang": case.get("lang", "en"),
                    "expected": case["expected"].get("intent"),
                    "got_intent": result["intent"],
                    "got_params": result["params"],
                    "raw_llm": result["raw_llm"],
                    "duration_ms": result["duration_ms"],
                })

    # ── Hot: one phrase × 5 ──
    print("    hot micro-bench (1 phrase × 5) …")
    hot_ms: list[float] = []
    for _ in range(5):
        try:
            r = await _test_command(client, "turn on the light", "en")
            hot_ms.append(r["duration_ms"] or r["wall_ms"])
        except Exception as exc:
            print(f"      ! hot: {exc}")

    ram_peak = _ram_sample()
    print(f"    container RAM peak: {ram_peak.get('container_mb', '?')} MB")

    # ── Unload this model so the next one starts cold ──
    await _ollama_unload(client, model)
    await asyncio.sleep(1.0)

    # Metrics
    def _safe_q(values: list[float], q_index: int, n: int) -> float:
        if len(values) < 2:
            return values[0] if values else 0.0
        try:
            return statistics.quantiles(values, n=n)[q_index]
        except Exception:
            return max(values)

    per_case_consistency = {
        c["text"]: per_case_hits.get(c["text"], 0) / consistency_runs for c in corpus
    }
    confidence = (
        statistics.mean(per_case_consistency.values())
        if per_case_consistency else 0.0
    )
    pct = lambda n: (n / total_runs * 100) if total_runs else 0.0  # noqa: E731

    return {
        "model": model,
        "corpus_size": len(corpus),
        "runs": consistency_runs,
        "cold_ms": int(cold_ms),
        "warm_p50_ms": int(statistics.median(latencies)) if latencies else 0,
        "warm_p95_ms": int(_safe_q(latencies, 18, 20)) if latencies else 0,
        "warm_avg_ms": int(statistics.mean(latencies)) if latencies else 0,
        "warm_min_ms": int(min(latencies)) if latencies else 0,
        "hot_min_ms": int(min(hot_ms)) if hot_ms else 0,
        "hot_avg_ms": int(statistics.mean(hot_ms)) if hot_ms else 0,
        "intent_acc_pct": round(pct(intent_hits), 1),
        "params_acc_pct": round(pct(params_hits), 1),
        "confidence_pct": round(confidence * 100, 1),
        "errors": errors,
        "ram_before_mb": ram_before.get("container_mb"),
        "ram_loaded_mb": ram_loaded.get("container_mb"),
        "ram_peak_mb": ram_peak.get("container_mb"),
        "misses": misses,
    }


# ── Report renderer ───────────────────────────────────────────────────


def _render_report(results: list[dict], devices: list[dict], corpus: list[dict]) -> str:
    lines: list[str] = []
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines.append("SelenaCore voice pipeline — multi-model benchmark")
    lines.append("=" * 68)
    lines.append(f"Run time:    {now}")
    lines.append("Pipeline:    test-command endpoint (full Argos → router → LLM → resolve)")
    lines.append(f"Corpus:      {len(corpus)} cases (bilingual, real + fictional devices)")
    lines.append(f"Devices:     {len(devices)} in DB")
    for d in devices:
        lines.append(
            f"  - {d['name']!r:20s} type={d['entity_type']:20s} "
            f"loc={d.get('location', ''):15s} "
            f"name_en={d.get('name_en', ''):20s} "
            f"loc_en={d.get('location_en', '')}"
        )
    lines.append("")

    lines.append("Summary (sorted by intent accuracy)")
    lines.append("-" * 68)
    header = (
        f"{'model':<22} {'intent%':>7} {'params%':>7} {'conf%':>6} "
        f"{'cold':>6} {'p50':>6} {'p95':>6} {'hot':>6} "
        f"{'RAM MB':>7}"
    )
    lines.append(header)
    lines.append("-" * len(header))
    for r in sorted(results, key=lambda x: -x["intent_acc_pct"]):
        lines.append(
            f"{r['model']:<22} "
            f"{r['intent_acc_pct']:>6.1f}% "
            f"{r['params_acc_pct']:>6.1f}% "
            f"{r['confidence_pct']:>5.1f}% "
            f"{r['cold_ms']:>6d} "
            f"{r['warm_p50_ms']:>6d} "
            f"{r['warm_p95_ms']:>6d} "
            f"{r['hot_min_ms']:>6d} "
            f"{(r['ram_peak_mb'] or 0):>7d}"
        )
    lines.append("")
    lines.append("Legend: latency in ms. cold = first request after unload, p50/p95 = warm")
    lines.append("corpus, hot = best of 5 repeats on 'turn on the light'. conf = fraction")
    lines.append("of cases where all N consistency runs produced the expected intent.")
    lines.append("")

    for r in results:
        lines.append("=" * 68)
        lines.append(f"Model: {r['model']}")
        lines.append("-" * 68)
        lines.append(f"  Corpus cases:       {r['corpus_size']}")
        lines.append(f"  Consistency runs:   {r['runs']}")
        lines.append(f"  Errors:             {r['errors']}")
        lines.append("")
        lines.append("  Latency through full voice pipeline (test-command endpoint)")
        lines.append(f"    cold (unload+load+run): {r['cold_ms']} ms")
        lines.append(
            f"    warm corpus:    p50={r['warm_p50_ms']}  p95={r['warm_p95_ms']}  "
            f"avg={r['warm_avg_ms']}  min={r['warm_min_ms']}  ms"
        )
        lines.append(
            f"    hot (best of 5):  {r['hot_min_ms']} ms (avg {r['hot_avg_ms']} ms)"
        )
        lines.append("")
        lines.append("  Accuracy")
        lines.append(f"    intent match:       {r['intent_acc_pct']}%")
        lines.append(f"    params match:       {r['params_acc_pct']}%")
        lines.append(
            f"    confidence:         {r['confidence_pct']}% "
            f"(consistency across runs)"
        )
        lines.append("")
        lines.append("  Container RAM (selena-core)")
        lines.append(f"    before model load:  {r.get('ram_before_mb', '?')} MB")
        lines.append(f"    after cold load:    {r.get('ram_loaded_mb', '?')} MB")
        lines.append(f"    peak during run:    {r.get('ram_peak_mb', '?')} MB")
        lines.append("")
        if r["misses"]:
            lines.append(f"  First-run misses (showing up to 12 of {len(r['misses'])}):")
            for m in r["misses"][:12]:
                lines.append(f"    [{m['lang']}] {m['text']!r}")
                lines.append(
                    f"      expected: {m['expected']}  got: {m['got_intent']}  "
                    f"params: {m['got_params']}  ({m['duration_ms']} ms)"
                )
                if m["raw_llm"]:
                    lines.append(f"      raw:      {m['raw_llm']}")
        lines.append("")

    lines.append("=" * 68)
    lines.append("Corpus used")
    lines.append("-" * 68)
    for i, case in enumerate(corpus, 1):
        lines.append(f"{i:3d}. [{case.get('lang','en')}] {case['text']!r}")
        lines.append(f"     expected: {case['expected']}")
    lines.append("")
    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default="tests/benchmark/full_corpus.jsonl")
    ap.add_argument(
        "--models", default="",
        help="Comma-separated model list. Empty → every installed Ollama model.",
    )
    ap.add_argument("--consistency", type=int, default=3)
    ap.add_argument("--out-dir", default="tests/experiments/results")
    args = ap.parse_args()

    import httpx

    async with httpx.AsyncClient() as client:
        # Discover installed models if --models not given
        if args.models:
            models = [m.strip() for m in args.models.split(",") if m.strip()]
        else:
            r = await client.get(f"{OLLAMA_URL}/api/tags", timeout=10.0)
            models = [t["name"] for t in r.json().get("models", [])]
        print(f"Models under test: {models}")

        # Remember the originally-configured model so we can restore it.
        from core.config_writer import read_config
        original_cfg = read_config().get("voice", {})
        original_model = original_cfg.get("llm_model", "")

        corpus_path = Path(args.corpus)
        if not corpus_path.is_absolute():
            alt = Path("/opt/selena-core") / args.corpus
            if alt.is_file():
                corpus_path = alt
        corpus = [
            json.loads(line)
            for line in corpus_path.read_text().splitlines()
            if line.strip()
        ]
        print(f"Corpus: {len(corpus)} cases from {corpus_path}")

        # Health probe — abort if the core isn't responding.
        try:
            r = await client.get(f"{CORE_URL}/api/v1/health", timeout=5.0)
            r.raise_for_status()
        except Exception as exc:
            print(f"FATAL: core not reachable at {CORE_URL}: {exc}")
            return

        # Snapshot device state for the report header
        devices: list[dict] = []
        try:
            from sqlalchemy import select
            from sqlalchemy.ext.asyncio import (
                AsyncSession, async_sessionmaker, create_async_engine,
            )
            from core.registry.models import Device
            db_path = "/var/lib/selena/selena.db"
            if not Path(db_path).is_file():
                db_path = "/var/lib/selena/db/selena.db"
            eng = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
            SF = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
            async with SF() as session:
                rows = (await session.execute(select(Device))).scalars().all()
                for d in rows:
                    try:
                        meta = json.loads(d.meta) if d.meta else {}
                    except Exception:
                        meta = {}
                    devices.append({
                        "name": d.name,
                        "entity_type": d.entity_type or "",
                        "location": d.location or "",
                        "name_en": meta.get("name_en") or "",
                        "location_en": meta.get("location_en") or "",
                    })
        except Exception as exc:
            print(f"  ! device dump: {exc}")

        results: list[dict] = []
        try:
            for model in models:
                try:
                    row = await bench_model(client, model, corpus, args.consistency)
                    results.append(row)
                except Exception as exc:
                    print(f"  ! fatal error on {model}: {exc}")
                    import traceback; traceback.print_exc()
                    results.append({
                        "model": model, "error": str(exc),
                        "corpus_size": len(corpus), "runs": args.consistency,
                        "cold_ms": 0,
                        "warm_p50_ms": 0, "warm_p95_ms": 0,
                        "warm_avg_ms": 0, "warm_min_ms": 0,
                        "hot_min_ms": 0, "hot_avg_ms": 0,
                        "intent_acc_pct": 0.0, "params_acc_pct": 0.0,
                        "confidence_pct": 0.0, "errors": 0,
                        "ram_before_mb": None, "ram_loaded_mb": None,
                        "ram_peak_mb": None, "misses": [],
                    })
        finally:
            # Always restore the original config + unload everything we used.
            if original_model:
                _set_active_model(original_model)
                print(f"\nRestored original voice.llm_model = {original_model!r}")
            await _unload_all(client)

    report = _render_report(results, devices, corpus)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out_dir)
    if not out_dir.is_absolute():
        for candidate in (
            Path("/opt/selena-core") / args.out_dir,
            Path.cwd() / args.out_dir,
        ):
            if candidate.parent.is_dir():
                out_dir = candidate
                break
    out_dir.mkdir(parents=True, exist_ok=True)

    txt_path = out_dir / f"benchmark_{ts}.txt"
    json_path = out_dir / f"benchmark_{ts}.json"
    txt_path.write_text(report, encoding="utf-8")
    json_path.write_text(json.dumps({
        "timestamp": datetime.now().isoformat(),
        "devices": devices,
        "corpus": corpus,
        "results": results,
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    print()
    print(report)
    print()
    print(f"Report: {txt_path}")
    print(f"JSON:   {json_path}")


if __name__ == "__main__":
    asyncio.run(main())
