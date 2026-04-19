#!/usr/bin/env python3
"""Compare the previous and current coverage-bench rounds.

Usage: python3 tests/experiments/compare_rounds.py
       python3 tests/experiments/compare_rounds.py path/to/prev.json path/to/curr.json

Prints summary deltas (accuracy, latency, source mix) and per-bucket deltas
for by_entity / by_lang / by_twist / by_noise / by_category.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
DEFAULT_PREV = RESULTS / "coverage_bench_results_prev.json"
DEFAULT_CURR = RESULTS / "coverage_bench_results.json"


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def fmt_delta(prev: float | None, curr: float | None, pct: bool = False) -> str:
    if prev is None or curr is None:
        return "  n/a"
    diff = curr - prev
    sign = "+" if diff >= 0 else ""
    scale = 100 if pct else 1
    return f"{sign}{diff * scale:+.2f}{'%' if pct else ''}"


def cmp_summary(prev: dict, curr: dict) -> None:
    ps, cs = prev.get("summary", {}), curr.get("summary", {})
    print("== summary ==")
    print(f"  total    : {ps.get('total')} -> {cs.get('total')}")
    print(f"  passed   : {ps.get('passed')} -> {cs.get('passed')}")
    print(
        f"  accuracy : {ps.get('accuracy', 0):.4f} -> "
        f"{cs.get('accuracy', 0):.4f}  ({fmt_delta(ps.get('accuracy'), cs.get('accuracy'), pct=True)})"
    )
    print(
        f"  p50 ms   : {ps.get('p50_ms', 0):.2f} -> "
        f"{cs.get('p50_ms', 0):.2f}  ({fmt_delta(ps.get('p50_ms'), cs.get('p50_ms'))})"
    )
    print(
        f"  p95 ms   : {ps.get('p95_ms', 0):.2f} -> "
        f"{cs.get('p95_ms', 0):.2f}  ({fmt_delta(ps.get('p95_ms'), cs.get('p95_ms'))})"
    )
    p_src, c_src = ps.get("sources", {}), cs.get("sources", {})
    keys = sorted(set(p_src) | set(c_src))
    if keys:
        print("  sources  :")
        for k in keys:
            print(f"    {k:<12} {p_src.get(k, 0)} -> {c_src.get(k, 0)}")


def cmp_bucket(prev: dict, curr: dict, key: str) -> None:
    pb, cb = prev.get(key, {}), curr.get(key, {})
    all_keys = sorted(set(pb) | set(cb))
    if not all_keys:
        return
    print(f"\n== {key} ==")
    print(f"  {'bucket':<28} {'prev acc':>10} {'curr acc':>10} {'delta':>8}")
    for k in all_keys:
        p, c = pb.get(k) or {}, cb.get(k) or {}
        p_acc = (p.get("pass", 0) / p["total"]) if p.get("total") else None
        c_acc = (c.get("pass", 0) / c["total"]) if c.get("total") else None
        p_s = f"{p_acc:.4f}" if p_acc is not None else "   —   "
        c_s = f"{c_acc:.4f}" if c_acc is not None else "   —   "
        delta = fmt_delta(p_acc, c_acc, pct=True) if (p_acc is not None and c_acc is not None) else "   —   "
        print(f"  {k:<28} {p_s:>10} {c_s:>10} {delta:>8}")


def main() -> int:
    prev_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PREV
    curr_path = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_CURR
    if not curr_path.is_file():
        print(f"missing: {curr_path}", file=sys.stderr)
        return 1
    if not prev_path.is_file():
        print(f"no previous round ({prev_path}); showing current summary only\n")
        cmp_summary({"summary": {}}, load(curr_path))
        return 0
    prev, curr = load(prev_path), load(curr_path)
    cmp_summary(prev, curr)
    for bucket in ("by_entity", "by_lang", "by_twist", "by_noise", "by_category"):
        cmp_bucket(prev, curr, bucket)
    return 0


if __name__ == "__main__":
    sys.exit(main())
