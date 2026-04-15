"""
core/translation/downloader.py

Argos Translate package management — download, install, list, delete.

Each language pair is an .argosmodel package (~50-100 MB).
For bidirectional translation (e.g. UK↔EN) two packages are needed:
  - uk→en (input: after Vosk STT)
  - en→uk (output: before Piper TTS)
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from core.config_writer import get_nested, update_config

logger = logging.getLogger(__name__)

# ── Download state ─────────────────────────────────────────────────

_download_state: dict[str, Any] = {
    "active": False,
    "package": "",
    "progress": 0.0,
    "error": "",
    "done": False,
}


def get_download_status() -> dict[str, Any]:
    return dict(_download_state)


def get_installed_packages() -> list[dict[str, Any]]:
    """List installed Argos Translate packages."""
    try:
        import argostranslate.package
        return [
            {
                "from_code": p.from_code,
                "to_code": p.to_code,
                "from_name": p.from_name,
                "to_name": p.to_name,
                "version": getattr(p, "package_version", ""),
            }
            for p in argostranslate.package.get_installed_packages()
        ]
    except Exception:
        return []


def get_available_packages() -> list[dict[str, Any]]:
    """List available Argos Translate packages from index."""
    try:
        import argostranslate.package
        argostranslate.package.update_package_index()
        available = argostranslate.package.get_available_packages()
        installed = {
            (p.from_code, p.to_code)
            for p in argostranslate.package.get_installed_packages()
        }
        return [
            {
                "from_code": p.from_code,
                "to_code": p.to_code,
                "from_name": p.from_name,
                "to_name": p.to_name,
                "version": getattr(p, "package_version", ""),
                "installed": (p.from_code, p.to_code) in installed,
            }
            for p in available
        ]
    except Exception as exc:
        logger.warning("Failed to fetch Argos package index: %s", exc)
        return []


def get_catalog() -> list[dict[str, Any]]:
    """Build UI-friendly catalog: group by language pairs relevant to EN."""
    try:
        import argostranslate.package
        argostranslate.package.update_package_index()
        available = argostranslate.package.get_available_packages()
        installed_set = {
            (p.from_code, p.to_code)
            for p in argostranslate.package.get_installed_packages()
        }
    except Exception:
        return []

    # Group: lang↔en pairs
    pairs: dict[str, dict[str, Any]] = {}
    for p in available:
        if p.to_code == "en" and p.from_code != "en":
            lang = p.from_code
            pairs.setdefault(lang, {
                "id": f"argos-{lang}-en",
                "lang_code": lang,
                "lang_name": p.from_name,
                "input_installed": False,
                "output_installed": False,
            })
            pairs[lang]["input_installed"] = (lang, "en") in installed_set
            pairs[lang]["input_version"] = getattr(p, "package_version", "")
        elif p.from_code == "en" and p.to_code != "en":
            lang = p.to_code
            pairs.setdefault(lang, {
                "id": f"argos-{lang}-en",
                "lang_code": lang,
                "lang_name": p.to_name,
                "input_installed": False,
                "output_installed": False,
            })
            pairs[lang]["output_installed"] = ("en", lang) in installed_set
            pairs[lang]["output_version"] = getattr(p, "package_version", "")

    active_lang = get_nested("translation.active_lang", "")
    active_engine = get_nested("translation.engine", "argos")

    result = []
    for lang, info in sorted(pairs.items(), key=lambda x: x[1].get("lang_name", "")):
        both_installed = info.get("input_installed", False) and info.get("output_installed", False)
        result.append({
            **info,
            "engine": "argos",
            "installed": both_installed,
            "active": (lang == active_lang and active_engine == "argos"),
        })

    # Merge in Helsinki rows. Same row schema, different ``id`` prefix
    # so the activate route can dispatch by engine.
    try:
        from core.translation.helsinki_downloader import get_helsinki_catalog
        result.extend(get_helsinki_catalog())
    except Exception as exc:
        logger.debug("Helsinki catalog merge skipped: %s", exc)

    return result


async def install_package(from_code: str, to_code: str) -> None:
    """Download and install a single Argos package."""
    global _download_state
    label = f"{from_code}→{to_code}"
    _download_state = {
        "active": True, "package": label, "progress": 10.0,
        "error": "", "done": False,
    }
    try:
        import argostranslate.package
        argostranslate.package.update_package_index()
        available = argostranslate.package.get_available_packages()
        pkg = next(
            (p for p in available if p.from_code == from_code and p.to_code == to_code),
            None,
        )
        if not pkg:
            raise RuntimeError(f"Package {label} not found in index")

        _download_state["progress"] = 30.0

        # Download runs in thread to not block event loop
        loop = asyncio.get_event_loop()
        path = await loop.run_in_executor(None, pkg.download)
        _download_state["progress"] = 70.0

        # Install. install_from_path unpacks the .argosmodel into
        # argostranslate's package dir; any post-unpack import/validation
        # noise (e.g. optional stanza sentence-segmenter not available) is
        # logged but NOT treated as a hard failure — the translation files
        # are already on disk and usable. Previously any such noise set
        # _download_state.error, which then blocked install_pair() from
        # queueing the reverse direction.
        try:
            argostranslate.package.install_from_path(path)
        except ModuleNotFoundError as exc:
            logger.warning(
                "Argos install_from_path for %s raised optional-dep error (%s) — "
                "package files are in place, continuing", label, exc,
            )
        _download_state["progress"] = 100.0
        _download_state["done"] = True
        _download_state["active"] = False
        logger.info("Argos package installed: %s", label)

    except Exception as exc:
        _download_state["error"] = str(exc)[:200]
        _download_state["done"] = True
        _download_state["active"] = False
        logger.error("Argos package install failed [%s]: %s", label, exc)


async def install_pair(lang_code: str) -> None:
    """Install both directions for a language (lang→en + en→lang)."""
    await install_package(lang_code, "en")
    if not _download_state.get("error"):
        await install_package("en", lang_code)

    if not _download_state.get("error"):
        # Auto-activate if this is the first pair
        active = get_nested("translation.active_lang", "")
        if not active:
            activate_lang(lang_code)


def activate_lang(lang_code: str) -> bool:
    """Set active translation language and pin engine to argos.

    Writing the engine explicitly on every activate keeps the config
    consistent: each ``Activate`` click in the UI is the source of
    truth for both ``active_lang`` AND ``engine``, so flipping between
    Argos and Helsinki rows in the catalog is always unambiguous.
    """
    update_config("translation", "active_lang", lang_code)
    update_config("translation", "engine", "argos")
    update_config("translation", "enabled", True)
    from core.translation.local_translator import reload_translators
    reload_translators()
    logger.info("Translation activated: argos/%s↔en", lang_code)
    return True


def delete_pair(lang_code: str) -> bool:
    """Delete both directions of a language pair."""
    active = get_nested("translation.active_lang", "")
    if lang_code == active:
        return False

    try:
        import argostranslate.package
        installed = argostranslate.package.get_installed_packages()
        for p in installed:
            if (p.from_code == lang_code and p.to_code == "en") or \
               (p.from_code == "en" and p.to_code == lang_code):
                p.remove()
                logger.info("Removed Argos package: %s→%s", p.from_code, p.to_code)
        return True
    except Exception as exc:
        logger.error("Delete failed: %s", exc)
        return False
