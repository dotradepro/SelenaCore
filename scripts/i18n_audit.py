#!/usr/bin/env python3
"""Scan JSX/TSX for probable hardcoded user-facing strings.

Heuristic, not perfect — produces a ranked list of candidates for human
review. The goal is to make the `{t('...')}`-coverage gap measurable and
trackable over time, not to be a strict linter.

Usage:
    python scripts/i18n_audit.py                        # prints report to stdout
    python scripts/i18n_audit.py --json                 # machine-readable
    python scripts/i18n_audit.py --path src/components  # narrow scope
    python scripts/i18n_audit.py --ci --max 10          # exit 1 if > 10 hits
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCAN_DEFAULT = REPO_ROOT / "src"

# JSX text nodes: content between > and < that starts with a capital letter
# and contains at least one word >= 3 chars. Skips single tokens (those are
# usually identifiers, not prose).
JSX_TEXT_RE = re.compile(
    r">\s*([A-Z][A-Za-z][^<>{}\n]{2,}?)\s*<",
)

# String-literal JSX attributes that commonly hold user-facing text.
ATTR_RE = re.compile(
    r"""\b(placeholder|title|aria-label|alt|label|tooltip)\s*=\s*["']([^"']{3,})["']""",
)

# Skip if the whole line already contains a `t(...)` call for that snippet.
T_CALL_RE = re.compile(r"\bt\(")

# Tokens that are not prose even if they look like words.
TECHNICAL_TOKENS = {
    "true", "false", "null", "undefined", "props", "state",
    "http", "https", "api", "url", "uri", "uuid",
    "json", "xml", "yaml", "html", "css",
}

# Exclude these filename patterns — they are not user-facing UI.
EXCLUDE_FILES = (
    "vite-env.d.ts",
    ".test.",
    ".spec.",
    "/node_modules/",
    "/dist/",
)


@dataclass
class Finding:
    file: str
    line: int
    column: int
    snippet: str
    kind: str  # 'text' | 'attr'

    def to_dict(self) -> dict:
        return {"file": self.file, "line": self.line, "column": self.column,
                "snippet": self.snippet, "kind": self.kind}


def looks_translatable(text: str) -> bool:
    text = text.strip()
    if len(text) < 4:
        return False
    if text.lower() in TECHNICAL_TOKENS:
        return False
    # Purely symbols/numbers
    if not re.search(r"[A-Za-z]{3,}", text):
        return False
    # Obvious URL / path / CSS / identifier patterns
    if re.match(r"^[a-z][\w.:/\-]*$", text):
        return False
    if text.startswith("--") or text.startswith("#") or text.startswith("/"):
        return False
    # Looks like at least 2 English words
    if re.search(r"[A-Za-z]+\s+[A-Za-z]+", text):
        return True
    # Single capitalized word worth flagging (e.g. "Stopped", "Anthropic")
    if re.match(r"^[A-Z][a-z]{3,}$", text):
        return True
    return False


def scan_file(path: Path) -> list[Finding]:
    findings: list[Finding] = []
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return findings

    rel = str(path.relative_to(REPO_ROOT))
    for lineno, line in enumerate(content.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("//") or stripped.startswith("*"):
            continue

        for match in JSX_TEXT_RE.finditer(line):
            text = match.group(1)
            if not looks_translatable(text):
                continue
            # Don't double-flag lines that already call t()
            if T_CALL_RE.search(line):
                continue
            findings.append(Finding(rel, lineno, match.start(1) + 1, text, "text"))

        for match in ATTR_RE.finditer(line):
            attr_value = match.group(2)
            if not looks_translatable(attr_value):
                continue
            if T_CALL_RE.search(line):
                continue
            findings.append(Finding(rel, lineno, match.start(2) + 1, attr_value, "attr"))

    return findings


def walk(root: Path) -> list[Path]:
    out: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix not in (".tsx", ".jsx"):
            continue
        rel = str(path)
        if any(pattern in rel for pattern in EXCLUDE_FILES):
            continue
        out.append(path)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", default=str(SCAN_DEFAULT), help="root dir to scan")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument("--ci", action="store_true", help="exit 1 if findings > --max")
    parser.add_argument("--max", type=int, default=0, help="threshold for --ci")
    args = parser.parse_args()

    root = Path(args.path).resolve()
    all_findings: list[Finding] = []
    for path in walk(root):
        all_findings.extend(scan_file(path))

    if args.json:
        print(json.dumps([f.to_dict() for f in all_findings], indent=2))
    else:
        by_file: dict[str, list[Finding]] = {}
        for f in all_findings:
            by_file.setdefault(f.file, []).append(f)
        for file, items in sorted(by_file.items()):
            print(f"\n{file}")
            for f in items:
                print(f"  L{f.line}:{f.column}  [{f.kind}]  {f.snippet!r}")
        print(f"\nTotal candidates: {len(all_findings)} across {len(by_file)} file(s)")

    return 1 if (args.ci and len(all_findings) > args.max) else 0


if __name__ == "__main__":
    sys.exit(main())
