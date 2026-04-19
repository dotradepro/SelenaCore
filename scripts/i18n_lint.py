#!/usr/bin/env python3
"""Pre-commit / CI gate for i18n drift.

Runs i18n_diff.py (en ↔ uk key parity) and i18n_audit.py (hardcoded strings)
and exits non-zero if the situation has worsened since the last frozen
baseline in docs/i18n_audit_baseline.json.

Usage:
    python scripts/i18n_lint.py                  # compare against baseline
    python scripts/i18n_lint.py --update-baseline  # bless current state
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE_PATH = REPO_ROOT / "docs" / "i18n_audit_baseline.json"
AUDIT = REPO_ROOT / "scripts" / "i18n_audit.py"
DIFF = REPO_ROOT / "scripts" / "i18n_diff.py"


def run_audit() -> list[dict]:
    result = subprocess.run(
        [sys.executable, str(AUDIT), "--json"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout)


def run_diff_ci() -> int:
    result = subprocess.run(
        [sys.executable, str(DIFF), "--ci"],
        cwd=REPO_ROOT,
    )
    return result.returncode


def load_baseline() -> int:
    if not BASELINE_PATH.exists():
        return 0
    data = json.loads(BASELINE_PATH.read_text())
    return int(data.get("audit_candidates", 0))


def write_baseline(count: int) -> None:
    BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    BASELINE_PATH.write_text(json.dumps({"audit_candidates": count}, indent=2) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--update-baseline", action="store_true",
                        help="freeze the current hardcoded-string count as the accepted baseline")
    args = parser.parse_args()

    findings = run_audit()
    count = len(findings)
    baseline = load_baseline()

    print(f"[i18n-lint] hardcoded-string candidates: {count} (baseline: {baseline})")

    if args.update_baseline:
        write_baseline(count)
        print(f"[i18n-lint] baseline updated to {count}")
        return 0

    exit_code = 0

    if count > baseline:
        print(f"[i18n-lint] FAIL: {count - baseline} new hardcoded-string candidate(s) added.")
        print("           Run  python scripts/i18n_audit.py  to inspect,")
        print("           or   python scripts/i18n_lint.py --update-baseline  to bless.")
        exit_code = 1

    diff_code = run_diff_ci()
    if diff_code != 0:
        print("[i18n-lint] FAIL: key-parity drift between en.ts and uk.ts.")
        exit_code = 1

    if exit_code == 0:
        print("[i18n-lint] OK.")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
