"""
core/api/routes/i18n.py — Per-module i18n bundle endpoint.

System modules (and user modules) load their translated strings by
fetching this endpoint from their settings.html / widget.html iframe
context. Pattern:

    GET /api/i18n/bundle/voice-core?lang=pl
    →   { "title": "Ustawienia gÅ‚osu", "save": "Zapisz", ... }

Resolution order (later tiers override earlier ones):

    1. core/i18n/common/{lang}.json          — shared strings (Save/Cancel/...)
    2. system_modules/{snake_name}/locales/{lang}.auto.json   (auto)
    3. system_modules/{snake_name}/locales/{lang}.community.json (community)
    4. system_modules/{snake_name}/locales/{lang}.json       (manual)

Unknown language falls back to `en` with the same merge order.

This endpoint is deliberately localhost-only UI plumbing — it does NOT
require a module token. It mirrors how `/api/ui/setup/*` and
`/shared/*` routes work (iframes need them at boot, before a token is
available).
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/i18n", tags=["i18n"])

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
COMMON_DIR = REPO_ROOT / "core" / "i18n" / "common"
SYSTEM_MODULES_DIR = REPO_ROOT / "system_modules"
USER_MODULES_DIR = REPO_ROOT / "modules"

FALLBACK_LANG = "en"
MODULE_NAME_RE = re.compile(r"^[a-z][a-z0-9_\-]{1,48}$")


def _kebab_to_snake(name: str) -> str:
    """voice-core → voice_core. system_module directory names use snake_case,
    but their manifest `name` (and widget URL path) uses kebab-case."""
    return name.replace("-", "_")


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _module_locales_dir(manifest_name: str) -> Path | None:
    """Find the on-disk locales dir for a manifest name. Checks system
    modules first, then user-installed modules. Returns None if absent."""
    snake = _kebab_to_snake(manifest_name)
    for base in (SYSTEM_MODULES_DIR, USER_MODULES_DIR):
        candidate = base / snake / "locales"
        if candidate.is_dir():
            return candidate
        # User modules may also live under the manifest name directly
        alt = base / manifest_name / "locales"
        if alt.is_dir():
            return alt
    return None


def _merge_tier(bundle: dict, path: Path) -> None:
    data = _load_json(path)
    if isinstance(data, dict):
        bundle.update(data)


def _merge_dir_for_lang(bundle: dict, locales_dir: Path, lang: str) -> None:
    """Merge the standard 4-tier set from a locales directory:

        en.json  →  {lang}.auto.json  →  {lang}.community.json  →  {lang}.json

    English is always loaded first so higher tiers can override per key.
    For lang == 'en', only en.json is loaded — auto/community/manual tiers
    don't apply to the reference language."""
    _merge_tier(bundle, locales_dir / f"{FALLBACK_LANG}.json")
    if lang == FALLBACK_LANG:
        return
    _merge_tier(bundle, locales_dir / f"{lang}.auto.json")
    _merge_tier(bundle, locales_dir / f"{lang}.community.json")
    _merge_tier(bundle, locales_dir / f"{lang}.json")


@lru_cache(maxsize=256)
def _build_bundle(module_name: str, lang: str) -> dict[str, Any]:
    """Merge common + module locale tiers for (module, lang). Both tiers
    honor the standard auto/community/manual priority internally."""
    bundle: dict[str, Any] = {}
    _merge_dir_for_lang(bundle, COMMON_DIR, lang)
    module_dir = _module_locales_dir(module_name)
    if module_dir is not None:
        _merge_dir_for_lang(bundle, module_dir, lang)
    return bundle


def clear_cache() -> None:
    """Invalidate the bundle cache. Call after editing a module's locale
    file during development — not wired into any hot-reload path yet."""
    _build_bundle.cache_clear()


@router.get("/bundle/{module_name}")
async def get_module_bundle(
    module_name: str,
    lang: str = Query(default=FALLBACK_LANG, min_length=2, max_length=8,
                      pattern=r"^[a-z]{2,3}(?:-[a-zA-Z]{2,4})?$"),
) -> dict[str, Any]:
    """Return the merged i18n bundle for a module, localized to `lang`.

    The response is a flat `{key: string}` map. Widgets consume it as:

        let t = (k) => k;
        fetch(`/api/i18n/bundle/${MODULE_NAME}?lang=${LANG}`)
            .then(r => r.json())
            .then(bundle => { t = (k) => bundle[k] || k; applyLang(); });
    """
    if not MODULE_NAME_RE.fullmatch(module_name):
        raise HTTPException(status_code=400, detail="Invalid module name")

    lang_normalized = lang.lower().split("-")[0]
    return _build_bundle(module_name, lang_normalized)


@router.get("/common")
async def get_common_bundle(
    lang: str = Query(default=FALLBACK_LANG, min_length=2, max_length=8,
                      pattern=r"^[a-z]{2,3}(?:-[a-zA-Z]{2,4})?$"),
) -> dict[str, Any]:
    """Return JUST the common strings (no module tier). Useful for
    widgets that don't have their own locale file yet."""
    lang_normalized = lang.lower().split("-")[0]
    bundle: dict[str, Any] = {}
    _merge_dir_for_lang(bundle, COMMON_DIR, lang_normalized)
    return bundle
