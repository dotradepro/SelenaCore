#!/usr/bin/env python3
"""One-off measurement: distribution of cosine margin over the current bench corpus.

Runs every case through ``IntentRouter.route()`` exactly like
``run_coverage_bench.py``, but records the ``score`` and ``margin`` from
``IntentResult.raw_llm`` (already populated by the embedding classifier).
Buckets margins into 0.005-wide intervals and writes both the raw CSV
and a human-readable histogram summary.

Purpose: pick an empirically-defended low-margin band for clarification
triggering (see plan §R1 in ``.claude/plans/eager-hatching-tower.md``).

Run inside the container:

    docker exec -t selena-core python3 \\
        /opt/selena-core/tests/experiments/measure_margins.py
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path


_here = Path(__file__).resolve()
for candidate in (_here.parents[2], Path("/opt/selena-core")):
    if (candidate / "core" / "llm.py").is_file() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))


# Parse "score=0.693 margin=0.029 runner_up=device.set_mode(0.664)"
_RAW_RE = re.compile(
    r"score=(?P<score>[\d.]+)\s+margin=(?P<margin>-?[\d.]+)\s+"
    r"runner_up=(?P<runner_up>[\w.]+)\(",
)


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


async def main() -> None:
    from core.translation.local_translator import get_input_translator
    from system_modules.llm_engine.intent_router import get_intent_router
    from tests.experiments.corpus_generator import generate

    await _bootstrap_db()

    translator = get_input_translator()
    router = get_intent_router()
    router.warmup_embedding()

    corpus = await generate()
    print(f"Measuring margins across {len(corpus)} cases…")

    records: list[dict] = []
    for idx, case in enumerate(corpus, 1):
        lang = case["lang"]
        native = case["native"]
        try:
            text_en = translator.to_english(native, lang) if lang != "en" else native
        except Exception:
            text_en = native

        try:
            result = await router.route(
                text_en, lang=lang, tts_lang=lang,
                native_text=native if lang != "en" else None,
            )
        except Exception:
            continue

        raw = result.raw_llm or ""
        m = _RAW_RE.search(raw)
        if not m:
            continue  # non-embedding result (fallback / chat), skip
        score = float(m.group("score"))
        margin = float(m.group("margin"))
        runner_up = m.group("runner_up")

        records.append({
            "idx": idx,
            "lang": lang,
            "category": case.get("category"),
            "exp_intent": case.get("exp_intent"),
            "got_intent": result.intent,
            "score": score,
            "margin": margin,
            "runner_up": runner_up,
            "native": native,
            "correct": result.intent == case.get("exp_intent"),
        })

        if idx % 100 == 0:
            print(f"  {idx}/{len(corpus)}")

    total = len(records)
    print(f"\nCaptured {total} embedding-source classifications")
    print(f"  (remaining {len(corpus) - total} cases went to fallback / chat)\n")

    # Bucket margins
    buckets: list[tuple[float, float]] = [
        (0.000, 0.003),
        (0.003, 0.005),
        (0.005, 0.008),
        (0.008, 0.010),
        (0.010, 0.012),
        (0.012, 0.015),
        (0.015, 0.020),
        (0.020, 0.030),
        (0.030, 0.050),
        (0.050, 0.100),
        (0.100, 1.001),
    ]

    # Build histogram with pass/fail within each bucket
    hist: dict[tuple[float, float], dict] = defaultdict(
        lambda: {"total": 0, "correct": 0, "samples": []},
    )
    for r in records:
        for lo, hi in buckets:
            if lo <= r["margin"] < hi:
                b = hist[(lo, hi)]
                b["total"] += 1
                if r["correct"]:
                    b["correct"] += 1
                if len(b["samples"]) < 5:
                    b["samples"].append(r)
                break

    # Write human report
    out_dir = Path("/opt/selena-core/tests/experiments/results")
    out_dir.mkdir(parents=True, exist_ok=True)
    hist_path = out_dir / "bench_margin_histogram.txt"
    csv_path = out_dir / "bench_margins.csv"

    with hist_path.open("w") as f:
        f.write(f"Margin distribution across {total} embedding cases\n")
        f.write(f"(corpus total: {len(corpus)})\n\n")
        f.write(f"{'bucket':<18} {'count':>6} {'%':>6} {'correct':>7} {'acc%':>6}\n")
        f.write("-" * 50 + "\n")
        cum = 0
        for (lo, hi), b in sorted(hist.items()):
            cum += b["total"]
            pct = 100 * b["total"] / max(total, 1)
            acc = 100 * b["correct"] / max(b["total"], 1) if b["total"] else 0
            f.write(
                f"{lo:.3f}-{hi:.3f}"
                f"{b['total']:>10} "
                f"{pct:>6.1f} "
                f"{b['correct']:>7} "
                f"{acc:>6.1f}\n"
            )
        f.write("-" * 50 + "\n")
        f.write(f"Cumulative total: {cum}\n\n")

        # Per-bucket samples (first 5 each, mix of pass/fail)
        f.write("\n=== Per-bucket samples (first 5 each) ===\n")
        for (lo, hi), b in sorted(hist.items()):
            f.write(f"\n[{lo:.3f}-{hi:.3f}]  ({b['total']} cases, {b['correct']} correct)\n")
            for s in b["samples"]:
                mark = "✓" if s["correct"] else "✗"
                f.write(
                    f"  {mark}  margin={s['margin']:.4f}  "
                    f"{s['lang']}/{s['category']:>10}  "
                    f"{s['got_intent']:<30}  "
                    f"{s['native'][:60]!r}\n"
                )

        # Suggested band
        f.write("\n=== Band recommendations ===\n")
        for (lo, hi), b in sorted(hist.items()):
            if b["total"] == 0:
                continue
            pct = 100 * b["total"] / total
            acc = 100 * b["correct"] / b["total"]
            if 0.003 <= lo <= 0.015:
                f.write(
                    f"  Band {lo:.3f}-{hi:.3f}: "
                    f"{pct:.1f}% of cases, {acc:.0f}% correct.\n"
                )

    with csv_path.open("w") as f:
        f.write("idx,lang,category,exp_intent,got_intent,correct,score,margin,runner_up,native\n")
        for r in records:
            f.write(
                f"{r['idx']},{r['lang']},{r['category']},{r['exp_intent']},"
                f"{r['got_intent']},{int(r['correct'])},"
                f"{r['score']:.4f},{r['margin']:.4f},{r['runner_up']},"
                f'"{r["native"].replace(chr(34), chr(39))}"\n'
            )

    print(f"Histogram: {hist_path}")
    print(f"CSV:       {csv_path}")

    # Echo highlights
    print()
    for (lo, hi), b in sorted(hist.items()):
        pct = 100 * b["total"] / max(total, 1)
        acc = 100 * b["correct"] / max(b["total"], 1) if b["total"] else 0
        bar = "█" * int(pct)
        print(f"  {lo:.3f}-{hi:.3f}  {b['total']:>4}  {pct:>5.1f}%  {acc:>5.1f}% correct  {bar}")


if __name__ == "__main__":
    asyncio.run(main())
