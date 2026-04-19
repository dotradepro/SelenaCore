#!/usr/bin/env python3
"""Render the coverage bench results as a standalone SVG.

Produces a scalable bar chart with per-category accuracy, total-acc
callout, latency and source breakdown. Pure Python (no matplotlib) —
runnable on the host or in the container.

Input:  tests/experiments/results/coverage_bench_results.json
Output: tests/experiments/results/bench_viz/intent-bench.svg

Output lives under ``tests/experiments/results/`` (gitignored) — bench results
are local artefacts and vary per machine / registry state. See
``docs/bench-coverage.md`` for interpretation and publishing guidance.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from xml.sax.saxutils import escape


# ── Layout ─────────────────────────────────────────────────────────────

W = 960
H = 560
M_TOP = 100
M_LEFT = 170
M_RIGHT = 40
BAR_H = 20
BAR_GAP = 6

# Colour palette — muted, fits both light + dark dashboard themes
BG = "#0d1117"
FG = "#c9d1d9"
MUTED = "#8b949e"
GRID = "#30363d"
BAR_HIGH = "#3fb950"  # 100% — green
BAR_GOOD = "#58a6ff"  # ≥ 90% — blue
BAR_WARN = "#d29922"  # ≥ 70% — yellow
BAR_LOW = "#f85149"   # < 70% — red
HEADER = "#f0f6fc"

# Category presentation — ordered for scan
CATEGORY_ORDER = [
    "plain", "ambiguous", "distractor",
    "all_off", "all_on",
    "weather", "automation", "clock", "presence", "media", "system",
    "noise", "variety",
]

# Friendly labels
LABELS = {
    "plain":      "Plain (canonical)",
    "variety":    "Variety (paraphrase)",
    "noise":      "Noise (filler/typo)",
    "ambiguous":  "Ambiguous (no room)",
    "all_off":    "house.all_off",
    "all_on":     "house.all_on",
    "media":      "Media (playback)",
    "clock":      "Clock / alarms",
    "weather":    "Weather",
    "presence":   "Presence",
    "automation": "Automation",
    "system":     "System (energy / privacy)",
    "distractor": "Distractor (no false +)",
}


def _bar_colour(pct: float) -> str:
    if pct >= 100:
        return BAR_HIGH
    if pct >= 90:
        return BAR_GOOD
    if pct >= 70:
        return BAR_WARN
    return BAR_LOW


def render(path_in: Path, path_out: Path) -> None:
    data = json.loads(path_in.read_text())
    s = data["summary"]
    by_cat = data["by_category"]

    total_pct = 100 * s["accuracy"]
    p50 = s["p50_ms"]
    p95 = s["p95_ms"]
    total_cases = s["total"]
    passed_cases = s["passed"]
    emb = s["sources"].get("embedding", 0)
    fb = s["sources"].get("fallback", 0)

    # Ordered categories actually present in data
    cats = [c for c in CATEGORY_ORDER if c in by_cat]

    row_h = BAR_H + BAR_GAP
    track_x = M_LEFT
    track_w = W - M_LEFT - M_RIGHT - 70  # leave room for value labels

    parts: list[str] = []
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
        f'role="img" aria-label="Intent classifier coverage bench">'
    )
    parts.append(f'<rect width="100%" height="100%" fill="{BG}"/>')

    # Header — title top-left, big number top-right
    parts.append(
        f'<text x="{M_LEFT}" y="40" fill="{HEADER}" '
        f'font-family="-apple-system, sans-serif" font-size="22" font-weight="600">'
        f'SelenaCore intent classifier</text>'
    )
    parts.append(
        f'<text x="{M_LEFT}" y="62" fill="{FG}" '
        f'font-family="-apple-system, sans-serif" font-size="14">'
        f'coverage bench · paraphrase-multilingual-MiniLM-L12-v2 · EN + UK</text>'
    )
    parts.append(
        f'<text x="{M_LEFT}" y="82" fill="{MUTED}" '
        f'font-family="-apple-system, sans-serif" font-size="12">'
        f'{total_cases} cases generated from live device registry · '
        f'p50 {p50:.0f}ms · p95 {p95:.0f}ms · '
        f'{emb} via embedding, {fb} via fallback</text>'
    )

    # Big number top-right
    parts.append(
        f'<text x="{W - M_RIGHT}" y="50" text-anchor="end" fill="{BAR_HIGH}" '
        f'font-family="-apple-system, sans-serif" font-size="44" font-weight="700">'
        f'{total_pct:.1f}%</text>'
    )
    parts.append(
        f'<text x="{W - M_RIGHT}" y="74" text-anchor="end" fill="{MUTED}" '
        f'font-family="-apple-system, sans-serif" font-size="12">'
        f'{passed_cases} / {total_cases} pass</text>'
    )

    # Bars
    y = M_TOP
    for cat in cats:
        stats = by_cat[cat]
        pct = 100 * stats["pass"] / max(stats["total"], 1)
        colour = _bar_colour(pct)
        label = LABELS.get(cat, cat)

        # Left label
        parts.append(
            f'<text x="{M_LEFT - 10}" y="{y + BAR_H - 6}" text-anchor="end" '
            f'fill="{FG}" font-family="-apple-system, sans-serif" font-size="12">'
            f'{escape(label)}</text>'
        )
        # Track
        parts.append(
            f'<rect x="{track_x}" y="{y}" width="{track_w}" height="{BAR_H}" '
            f'rx="3" fill="{GRID}"/>'
        )
        # Fill
        fill_w = int(track_w * pct / 100)
        if fill_w > 0:
            parts.append(
                f'<rect x="{track_x}" y="{y}" width="{fill_w}" height="{BAR_H}" '
                f'rx="3" fill="{colour}"/>'
            )
        # Value label
        parts.append(
            f'<text x="{track_x + track_w + 8}" y="{y + BAR_H - 6}" '
            f'fill="{FG}" font-family="-apple-system, sans-serif" '
            f'font-size="12" font-weight="600">'
            f'{pct:.0f}% <tspan fill="{MUTED}" font-weight="400">'
            f'({stats["pass"]}/{stats["total"]})</tspan></text>'
        )
        y += row_h

    # Footer legend — positioned just below the last bar
    leg_y = y + 28
    leg_x = M_LEFT
    parts.append(
        f'<text x="{leg_x}" y="{leg_y - 6}" fill="{MUTED}" '
        f'font-family="-apple-system, sans-serif" font-size="11">'
        f'accuracy band:</text>'
    )
    for label, colour, x_off in [
        ("100%",    BAR_HIGH, 90),
        ("≥ 90%",   BAR_GOOD, 150),
        ("≥ 70%",   BAR_WARN, 220),
        ("&lt; 70%", BAR_LOW,  290),  # entity-escaped — raw '<' would be parsed as a tag
    ]:
        parts.append(
            f'<rect x="{leg_x + x_off}" y="{leg_y - 16}" width="12" height="12" '
            f'rx="2" fill="{colour}"/>'
        )
        parts.append(
            f'<text x="{leg_x + x_off + 18}" y="{leg_y - 6}" fill="{FG}" '
            f'font-family="-apple-system, sans-serif" font-size="11">{label}</text>'
        )

    # Footer right: bench source
    parts.append(
        f'<text x="{W - M_RIGHT}" y="{leg_y - 6}" text-anchor="end" '
        f'fill="{MUTED}" font-family="-apple-system, sans-serif" font-size="10">'
        f'tests/experiments/run_coverage_bench.py</text>'
    )

    parts.append('</svg>')

    path_out.parent.mkdir(parents=True, exist_ok=True)
    path_out.write_text("\n".join(parts))
    print(f"Wrote {path_out} ({path_out.stat().st_size} bytes)")


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    path_in = root / "tests" / "experiments" / "results" / "coverage_bench_results.json"
    path_out = root / "tests" / "experiments" / "results" / "bench_viz" / "intent-bench.svg"
    if len(sys.argv) > 1:
        path_in = Path(sys.argv[1])
    if len(sys.argv) > 2:
        path_out = Path(sys.argv[2])
    render(path_in, path_out)
