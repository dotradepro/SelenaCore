"""Translation backends for the i18n auto-generator.

Architecture:
    Backend is pluggable. The generator picks the best one available at
    runtime. Tier order:

        1. ArgosBackend   — real translation via argostranslate
        2. StubBackend    — deterministic "[lang]text" marker for local dev
                            without argos-translate installed

    Both implement `translate(text, source, target) -> str`.
    Tests assert against StubBackend so CI doesn't need the Argos language
    packs downloaded just to run unit tests.
"""

from __future__ import annotations

import logging
import threading
from typing import Protocol

log = logging.getLogger(__name__)


class Backend(Protocol):
    name: str
    def translate(self, text: str, source: str, target: str) -> str: ...


class StubBackend:
    """Deterministic stub. Emits `[<lang>]<text>` so generator output is
    structurally valid but obviously-fake. Useful in unit tests and on
    host machines where argostranslate isn't installed."""

    name = "stub"

    def translate(self, text: str, source: str, target: str) -> str:
        if not text.strip():
            return text
        return f"[{target}]{text}"


class ArgosBackend:
    """argostranslate backend. Downloads the en→<target> language package
    on first use per language pair, then caches the Translation object."""

    name = "argos"

    def __init__(self, auto_install: bool = True) -> None:
        # Imports deferred so host Python without argos still works up to
        # the point someone asks for a real translation.
        import argostranslate.package as pkg
        import argostranslate.translate as tr
        self._pkg = pkg
        self._tr = tr
        self._auto_install = auto_install
        self._cache: dict[tuple[str, str], object] = {}
        self._lock = threading.Lock()
        self._updated_index = False

    def _ensure_package(self, source: str, target: str) -> None:
        installed = self._tr.get_installed_languages()
        codes = {lang.code for lang in installed}
        if source in codes and target in codes:
            # Already have the pair — confirm a translation path exists
            src = next(l for l in installed if l.code == source)
            if src.get_translation(next(l for l in installed if l.code == target)):
                return

        if not self._auto_install:
            raise RuntimeError(
                f"argos language pair {source}->{target} not installed and auto_install disabled"
            )

        if not self._updated_index:
            log.info("[argos] updating package index…")
            self._pkg.update_package_index()
            self._updated_index = True

        available = self._pkg.get_available_packages()
        match = next(
            (p for p in available if p.from_code == source and p.to_code == target),
            None,
        )
        if match is None:
            raise RuntimeError(f"no argos package available for {source}->{target}")

        log.info(f"[argos] downloading {source}->{target} package…")
        path = match.download()
        self._pkg.install_from_path(path)

    def _get_translator(self, source: str, target: str):
        key = (source, target)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                return cached
            self._ensure_package(source, target)
            installed = self._tr.get_installed_languages()
            src = next(l for l in installed if l.code == source)
            tgt = next(l for l in installed if l.code == target)
            translator = src.get_translation(tgt)
            if translator is None:
                raise RuntimeError(f"argos: no translator for {source}->{target}")
            self._cache[key] = translator
            return translator

    def translate(self, text: str, source: str, target: str) -> str:
        if not text.strip():
            return text
        translator = self._get_translator(source, target)
        return translator.translate(text)


def pick_backend(prefer: str | None = None) -> Backend:
    """Return the best available backend.

    If `prefer` is 'stub', always return StubBackend — tests use this.
    Otherwise try Argos; on ImportError, fall back to stub with a warning.
    """
    if prefer == "stub":
        return StubBackend()

    try:
        return ArgosBackend()
    except ImportError:
        log.warning(
            "[i18n] argostranslate not importable, falling back to stub backend "
            "(set prefer='argos' explicitly to force an error instead)"
        )
        return StubBackend()
