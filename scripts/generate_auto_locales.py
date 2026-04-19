#!/usr/bin/env python3
"""Generate auto-translated locale bundles from the English source.

Pipeline:
    en.ts  →  flat key tree  →  per-string translation (Argos)  →
    glossary restoration  →  plural-form expansion  →  {lang}.auto.json

Skeleton in this revision — translation backend is stubbed. Real Argos
wiring lands when CI is set up (A3) and the installed argos-translate
pipeline can be exercised end-to-end. The skeleton here establishes the
file shape, hash-idempotency, and glossary contract so A3 only has to
fill in `translate_string()`.

Usage:
    python scripts/generate_auto_locales.py \\
        --source src/i18n/locales/en.ts \\
        --output src/i18n/locales/auto/ \\
        --glossary src/i18n/glossary.json

    python scripts/generate_auto_locales.py --dry-run --targets ru,pl
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from i18n_config import AUTO_LANGUAGES, MANUAL_LANGUAGES, NATIVE_NAMES, DIRECTION
from i18n_backends import pick_backend, Backend
from i18n_plurals import expand_plural_forms, pluralize_key, has_count_placeholder

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = REPO_ROOT / "src" / "i18n" / "locales" / "en.ts"
DEFAULT_OUTPUT = REPO_ROOT / "src" / "i18n" / "locales" / "auto"
DEFAULT_GLOSSARY = REPO_ROOT / "src" / "i18n" / "glossary.json"
EXPORT_SCRIPT = REPO_ROOT / "scripts" / "i18n_export.mjs"

PLACEHOLDER_RE = re.compile(r"(\{\{\s*\w+\s*\}\})")
TERM_TOKEN_FMT = "__TERM_{}__"


def _display_path(path: Path) -> str:
    """Show path relative to the repo when possible, else absolute."""
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def load_source(path: Path) -> dict[str, Any]:
    """Load a locale source from either a .json file (direct) or a .ts
    file (via the Node helper). .json takes the fast path — useful for
    environments without Node installed (e.g. the selena-core container)."""
    if path.suffix == ".json":
        return json.loads(path.read_text())
    result = subprocess.run(
        ["npx", "tsx", str(EXPORT_SCRIPT), path.stem],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout)


def source_hash(data: dict[str, Any]) -> str:
    payload = json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def load_glossary(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"keep_original": [], "per_language_overrides": {}}
    data = json.loads(path.read_text())
    # Sort keep_original by length descending so multi-word terms win over
    # their substrings during substitution.
    data["keep_original"] = sorted(
        (t for t in data.get("keep_original", []) if isinstance(t, str)),
        key=len, reverse=True,
    )
    return data


def apply_glossary_shield(text: str, terms: list[str]) -> tuple[str, dict[str, str]]:
    """Replace glossary terms with opaque placeholders. Returns shielded text
    plus a token→original map for later restoration."""
    tokens: dict[str, str] = {}
    for idx, term in enumerate(terms):
        token = TERM_TOKEN_FMT.format(idx)
        if term in text:
            text = text.replace(term, token)
            tokens[token] = term
    return text, tokens


def restore_glossary(text: str, tokens: dict[str, str],
                     overrides: dict[str, str] | None) -> str:
    for token, original in tokens.items():
        replacement = original
        if overrides and original in overrides:
            replacement = overrides[original]
        text = text.replace(token, replacement)
    return text


def _translate_one(text: str, target_lang: str, glossary: dict[str, Any],
                   backend: Backend) -> str:
    """Translate a single string, honoring glossary and `{{placeholder}}` tokens."""
    shielded, tokens = apply_glossary_shield(text, glossary.get("keep_original", []))
    parts = PLACEHOLDER_RE.split(shielded)
    translated_parts = [
        p if PLACEHOLDER_RE.fullmatch(p) else backend.translate(p, "en", target_lang)
        for p in parts
    ]
    translated = "".join(translated_parts)
    overrides = glossary.get("per_language_overrides", {}).get(target_lang, {})
    return restore_glossary(translated, tokens, overrides)


def translate_tree(tree: Any, target_lang: str,
                   glossary: dict[str, Any], backend: Backend) -> Any:
    """Walk the nested key tree and translate every leaf string, preserving
    `{{placeholder}}` tokens and glossary terms. Strings with `{{count}}`
    expand to CLDR plural forms (e.g. `*_one`, `*_few`, `*_many`, `*_other`)."""
    if isinstance(tree, dict):
        out: dict[str, Any] = {}
        for key, value in tree.items():
            if isinstance(value, str) and has_count_placeholder(value):
                forms = expand_plural_forms(
                    value, target_lang,
                    translate=lambda s: _translate_one(s, target_lang, glossary, backend),
                )
                out.update(pluralize_key(key, forms))
            else:
                out[key] = translate_tree(value, target_lang, glossary, backend)
        return out
    if isinstance(tree, list):
        return [translate_tree(v, target_lang, glossary, backend) for v in tree]
    if not isinstance(tree, str):
        return tree
    return _translate_one(tree, target_lang, glossary, backend)


def generate_for_language(source: dict[str, Any], target_lang: str,
                          glossary: dict[str, Any],
                          output_dir: Path, src_hash: str,
                          backend: Backend) -> Path:
    translated = translate_tree(source, target_lang, glossary, backend)
    out_path = output_dir / f"{target_lang}.auto.json"
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(translated, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return out_path


def write_meta(output_dir: Path, src_hash: str, generated: list[str],
               skipped: list[str]) -> Path:
    meta_path = output_dir / "_meta.json"
    meta = {
        "source_hash": src_hash,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator": "scripts/generate_auto_locales.py",
        "generator_version": "0.1.0-skeleton",
        "manual": [
            {"code": code, "nativeName": NATIVE_NAMES.get(code, code),
             "direction": DIRECTION.get(code, "ltr"), "quality": "manual"}
            for code in MANUAL_LANGUAGES
        ],
        "auto": [
            {"code": code, "nativeName": NATIVE_NAMES.get(code, code),
             "direction": DIRECTION.get(code, "ltr"), "quality": "auto",
             "regenerated": code in generated}
            for code in AUTO_LANGUAGES
        ],
        "summary": {"regenerated": generated, "skipped_unchanged": skipped},
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n")
    return meta_path


def read_existing_hash(meta_path: Path) -> str | None:
    if not meta_path.exists():
        return None
    try:
        return json.loads(meta_path.read_text()).get("source_hash")
    except (OSError, json.JSONDecodeError):
        return None


def discover_module_sources() -> list[Path]:
    """Find every per-module (or common) `locales/en.json`:

        core/i18n/common/en.json           ← shared strings used by every widget
        system_modules/*/locales/en.json   ← system modules
        modules/*/locales/en.json          ← user-installed modules

    Each returned path becomes a translation source; the generator emits
    `{lang}.auto.json` siblings in the same dir, each with their own
    `_meta.json` so their source hashes evolve independently."""
    out: list[Path] = []

    common = REPO_ROOT / "core" / "i18n" / "common" / "en.json"
    if common.is_file():
        out.append(common)

    for base_name in ("system_modules", "modules"):
        base = REPO_ROOT / base_name
        if not base.is_dir():
            continue
        for en_file in base.glob("*/locales/en.json"):
            out.append(en_file)
    return sorted(out)


def run_module_mode(backend: Backend, glossary: dict[str, Any],
                    targets: list[str], force: bool, dry_run: bool) -> int:
    """Regenerate auto locales for every module that has a `locales/en.json`.
    Each module gets its own `_meta.json` with its own source hash, so they
    can be regenerated independently when one module's EN source changes."""
    sources = discover_module_sources()
    if not sources:
        print("no module sources found under system_modules/*/locales/en.json "
              "or modules/*/locales/en.json")
        return 0

    print(f"found {len(sources)} module source(s):")
    for src in sources:
        print(f"  {_display_path(src)}")

    if dry_run:
        return 0

    total_regen = 0
    total_skipped = 0
    for src in sources:
        source = load_source(src)
        src_hash = source_hash(source)
        module_output = src.parent

        existing = read_existing_hash(module_output / "_meta.json")
        if existing == src_hash and not force:
            total_skipped += 1
            continue

        print(f"\n[{src.parent.parent.name}]")
        generated = []
        for lang in targets:
            path = generate_for_language(source, lang, glossary, module_output, src_hash, backend)
            print(f"  wrote {_display_path(path)}")
            generated.append(lang)
        meta_path = write_meta(module_output, src_hash,
                               generated, [l for l in AUTO_LANGUAGES if l not in generated])
        print(f"  wrote {_display_path(meta_path)}")
        total_regen += 1

    print(f"\nmodule summary: {total_regen} regenerated, {total_skipped} unchanged")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--glossary", type=Path, default=DEFAULT_GLOSSARY)
    parser.add_argument("--targets", help="comma-separated list (default: all AUTO_LANGUAGES)")
    parser.add_argument("--force", action="store_true", help="regenerate even if source hash unchanged")
    parser.add_argument("--dry-run", action="store_true", help="print plan, don't write")
    parser.add_argument("--backend", choices=["auto", "argos", "stub"], default="auto",
                        help="translation backend (default: auto = argos if importable, else stub)")
    parser.add_argument("--modules", action="store_true",
                        help="regenerate module locales (system_modules/*/locales/en.json) "
                             "instead of the SPA root locale")
    args = parser.parse_args()

    glossary = load_glossary(args.glossary)
    targets = [t.strip() for t in args.targets.split(",")] if args.targets else list(AUTO_LANGUAGES)
    unknown = [t for t in targets if t not in AUTO_LANGUAGES]
    if unknown:
        print(f"unknown target language(s): {unknown}", file=sys.stderr)
        return 2

    backend_prefer = None if args.backend == "auto" else args.backend
    backend = pick_backend(prefer=backend_prefer)
    print(f"using backend: {backend.name}")

    if args.modules:
        return run_module_mode(backend, glossary, targets, args.force, args.dry_run)

    source = load_source(args.source)
    src_hash = source_hash(source)

    existing_hash = read_existing_hash(args.output / "_meta.json")
    if existing_hash == src_hash and not args.force:
        print(f"source hash unchanged ({src_hash}), nothing to do (use --force to override)")
        return 0

    if args.dry_run:
        print(f"dry-run: would generate {len(targets)} locale(s): {', '.join(targets)}")
        print(f"source hash: {src_hash}")
        return 0

    generated: list[str] = []
    for lang in targets:
        path = generate_for_language(source, lang, glossary, args.output, src_hash, backend)
        print(f"  wrote {_display_path(path)}")
        generated.append(lang)

    skipped = [lang for lang in AUTO_LANGUAGES if lang not in generated]
    meta_path = write_meta(args.output, src_hash, generated, skipped)
    print(f"  wrote {_display_path(meta_path)}")
    print(f"done: {len(generated)} regenerated, {len(skipped)} unchanged")
    return 0


if __name__ == "__main__":
    sys.exit(main())
