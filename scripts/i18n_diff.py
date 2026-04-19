#!/usr/bin/env python3
"""Diff translation-key coverage between locale files.

Compares each target locale (uk, ru, ...) against the reference locale (en)
and reports missing keys, orphan keys, and placeholder-interpolation drift.

Usage:
    python scripts/i18n_diff.py                # defaults: en -> all peers
    python scripts/i18n_diff.py --target uk    # just uk
    python scripts/i18n_diff.py --ci           # exit 1 if any drift
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
LOCALES_DIR = REPO_ROOT / "src" / "i18n" / "locales"
EXPORT_SCRIPT = REPO_ROOT / "scripts" / "i18n_export.mjs"
PLACEHOLDER_RE = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def load_locale(lang: str) -> dict[str, Any]:
    result = subprocess.run(
        ["npx", "tsx", str(EXPORT_SCRIPT), lang],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"i18n_export failed for {lang}: {result.stderr.strip()}")
    return json.loads(result.stdout)


def flatten(tree: Any, prefix: str = "") -> dict[str, Any]:
    flat: dict[str, Any] = {}
    if isinstance(tree, dict):
        for key, value in tree.items():
            path = f"{prefix}.{key}" if prefix else key
            flat.update(flatten(value, path))
    else:
        flat[prefix] = tree
    return flat


def discover_target_langs() -> list[str]:
    return sorted(
        p.stem
        for p in LOCALES_DIR.glob("*.ts")
        if p.stem != "en"
    )


def compare(reference: dict[str, Any], target: dict[str, Any], ref_lang: str, tgt_lang: str) -> int:
    ref_keys = set(reference)
    tgt_keys = set(target)

    missing = sorted(ref_keys - tgt_keys)
    orphan = sorted(tgt_keys - ref_keys)

    placeholder_drift: list[str] = []
    for key in ref_keys & tgt_keys:
        ref_v, tgt_v = reference[key], target[key]
        if not isinstance(ref_v, str) or not isinstance(tgt_v, str):
            continue
        ref_ph = set(PLACEHOLDER_RE.findall(ref_v))
        tgt_ph = set(PLACEHOLDER_RE.findall(tgt_v))
        if ref_ph != tgt_ph:
            placeholder_drift.append(
                f"  {key}\n    {ref_lang}: {sorted(ref_ph)}\n    {tgt_lang}: {sorted(tgt_ph)}"
            )

    issues = len(missing) + len(orphan) + len(placeholder_drift)
    print(f"\n=== {ref_lang} → {tgt_lang} ===")
    print(f"  keys: {len(ref_keys)} reference · {len(tgt_keys)} target")
    print(f"  missing in {tgt_lang}: {len(missing)}")
    for key in missing:
        print(f"    - {key}")
    print(f"  orphan in {tgt_lang} (not in {ref_lang}): {len(orphan)}")
    for key in orphan:
        print(f"    - {key}")
    print(f"  placeholder drift: {len(placeholder_drift)}")
    for line in placeholder_drift:
        print(line)
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", default="en", help="reference locale (default: en)")
    parser.add_argument("--target", help="single target locale (default: all peers)")
    parser.add_argument("--ci", action="store_true", help="exit 1 if any drift found")
    args = parser.parse_args()

    reference = flatten(load_locale(args.reference))
    targets = [args.target] if args.target else discover_target_langs()
    total_issues = 0
    for tgt_lang in targets:
        if tgt_lang == args.reference:
            continue
        target = flatten(load_locale(tgt_lang))
        total_issues += compare(reference, target, args.reference, tgt_lang)

    print(f"\nTotal issues across {len(targets)} target(s): {total_issues}")
    return 1 if (args.ci and total_issues) else 0


if __name__ == "__main__":
    sys.exit(main())
