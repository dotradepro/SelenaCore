"""
core/translation/helsinki_downloader.py

Helsinki engine analog of ``downloader.py``: lists, installs, activates,
and deletes pre-converted CTranslate2 / opus-mt language pairs from
:mod:`core.translation.helsinki_catalog`.

Mirrors the public surface of the Argos downloader so the REST routes
in ``core/api/routes/setup.py`` can dispatch by engine without growing
new function names:

* :func:`get_helsinki_catalog`        ↔ ``downloader.get_catalog``
* :func:`install_helsinki_pair`       ↔ ``downloader.install_pair``
* :func:`activate_helsinki_lang`      ↔ ``downloader.activate_lang``
* :func:`delete_helsinki_pair`        ↔ ``downloader.delete_pair``
* :func:`get_helsinki_download_status` ↔ ``downloader.get_download_status``

Local-first install
-------------------
Before fetching anything from a URL the installer checks whether the
target directory under ``translation.input_model_dir`` /
``output_model_dir`` is already a complete CT2 layout
(``model.bin`` + ``source.spm`` + ``target.spm``). If yes — install
becomes a no-op and the catalog row reports ``installed=True``. This
removes the GitHub-release dependency for day-1 deployments where the
operator drops the Colab archives onto disk by hand.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import shutil
import tarfile
import tempfile
from pathlib import Path
from typing import Any

from core.config_writer import get_nested, update_config

from core.translation.helsinki_catalog import HELSINKI_CATALOG, get_entry

logger = logging.getLogger(__name__)

_DEFAULT_INPUT_DIR = "/var/lib/selena/models/translate/helsinki/in"
_DEFAULT_OUTPUT_DIR = "/var/lib/selena/models/translate/helsinki/out"

# Mirrors downloader._download_state — single global because the UI
# only ever runs one download at a time and polls /translate/download/
# status. Keeping the schema identical means the route handlers don't
# need to special-case helsinki state.
_download_state: dict[str, Any] = {
    "active": False,
    "package": "",
    "progress": 0.0,
    "error": "",
    "done": False,
}


def get_helsinki_download_status() -> dict[str, Any]:
    return dict(_download_state)


def _input_dir() -> Path:
    return Path(get_nested("translation.input_model_dir", _DEFAULT_INPUT_DIR))


def _output_dir() -> Path:
    return Path(get_nested("translation.output_model_dir", _DEFAULT_OUTPUT_DIR))


def _layout_ok(d: Path) -> bool:
    return (
        (d / "model.bin").is_file()
        and (d / "source.spm").is_file()
        and (d / "target.spm").is_file()
    )


def _input_pair_dir(lang_code: str) -> Path:
    return _input_dir() / f"{lang_code}-en"


def _output_pair_dir(lang_code: str) -> Path:
    return _output_dir() / f"en-{lang_code}"


# ── Catalog ─────────────────────────────────────────────────────────


def get_helsinki_catalog() -> list[dict[str, Any]]:
    """UI-friendly Helsinki catalog rows.

    Schema matches the Argos catalog rows produced by
    ``downloader.get_catalog`` so the merged list rendered by
    ``setup.py /translate/catalog`` looks uniform to the frontend.
    The ``id`` field is prefixed ``helsinki-`` so the activate route
    can dispatch on it.
    """
    active_lang = get_nested("translation.active_lang", "")
    active_engine = get_nested("translation.engine", "argos")

    rows: list[dict[str, Any]] = []
    for entry in HELSINKI_CATALOG:
        lang = entry["lang_code"]
        in_installed = _layout_ok(_input_pair_dir(lang))
        out_installed = _layout_ok(_output_pair_dir(lang))
        rows.append({
            "id": f"helsinki-{lang}-en",
            "engine": "helsinki",
            "lang_code": lang,
            "lang_name": entry["lang_name"],
            "input_installed": in_installed,
            "output_installed": out_installed,
            "input_version": entry.get("input_model", ""),
            "output_version": entry.get("output_model", ""),
            "installed": in_installed and out_installed,
            "active": (lang == active_lang and active_engine == "helsinki"),
        })
    return rows


# ── Download / extract ──────────────────────────────────────────────


async def _download_to(url: str, dest: Path, expected_sha256: str = "") -> None:
    """Stream a URL to ``dest`` with progress, optional sha256 verify."""
    import httpx

    dest.parent.mkdir(parents=True, exist_ok=True)
    sha = hashlib.sha256()
    bytes_read = 0
    total = 0
    async with httpx.AsyncClient(timeout=600.0) as client:
        async with client.stream("GET", url, follow_redirects=True) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length", 0))
            with dest.open("wb") as f:
                async for chunk in resp.aiter_bytes(chunk_size=1 << 16):
                    f.write(chunk)
                    sha.update(chunk)
                    bytes_read += len(chunk)
                    if total:
                        _download_state["progress"] = round(
                            bytes_read / total * 100.0, 1,
                        )

    if expected_sha256:
        got = sha.hexdigest()
        if got.lower() != expected_sha256.lower():
            dest.unlink(missing_ok=True)
            raise ValueError(
                f"sha256 mismatch for {url}: expected {expected_sha256}, got {got}"
            )


def _extract_tar(archive: Path, target_dir: Path) -> None:
    """Extract a tar.gz into ``target_dir``, flattening one top-level
    folder if the archive nests everything inside one.
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        with tarfile.open(archive, "r:gz") as tf:
            tf.extractall(tmp_path)
        # If the archive contains exactly one top-level dir, treat its
        # contents as the model layout (typical for tar of opus-mt-*-ct2/).
        entries = list(tmp_path.iterdir())
        src = entries[0] if (len(entries) == 1 and entries[0].is_dir()) else tmp_path
        for item in src.iterdir():
            dest = target_dir / item.name
            if dest.exists():
                if dest.is_dir():
                    shutil.rmtree(dest)
                else:
                    dest.unlink()
            shutil.move(str(item), str(dest))


async def _install_one(url: str, sha256: str, target_dir: Path, label: str) -> None:
    """Download (if needed) + extract one model archive into target_dir."""
    if _layout_ok(target_dir):
        logger.info("Helsinki: %s already on disk at %s, skipping download", label, target_dir)
        return
    if not url:
        raise ValueError(
            f"Helsinki: no download URL for {label} and no local model "
            f"at {target_dir}. Place the converted CT2 folder there manually "
            "or fill the catalog URL."
        )
    _download_state["package"] = label
    _download_state["progress"] = 0.0
    with tempfile.NamedTemporaryFile(
        suffix=".tar.gz", delete=False,
    ) as tmpf:
        tmp_path = Path(tmpf.name)
    try:
        await _download_to(url, tmp_path, sha256)
        _extract_tar(tmp_path, target_dir)
        if not _layout_ok(target_dir):
            raise RuntimeError(
                f"Helsinki: extracted archive at {target_dir} is missing "
                "model.bin / source.spm / target.spm — bad archive layout?"
            )
        logger.info("Helsinki: installed %s into %s", label, target_dir)
    finally:
        tmp_path.unlink(missing_ok=True)


def install_helsinki_archive(
    lang_code: str, direction: str, archive_path: Path,
) -> None:
    """End-user upload path: extract a Colab-converted tar.gz into the
    correct pair directory and validate the layout.

    ``direction`` is ``"input"`` (lang→en) or ``"output"`` (en→lang).
    Raises ValueError on bad direction or missing files after extraction.
    Used by the ``POST /translate/upload`` route so non-programmers can
    install Helsinki models entirely through the browser without ever
    touching SCP or the catalog URL.
    """
    if direction == "input":
        target = _input_pair_dir(lang_code)
    elif direction == "output":
        target = _output_pair_dir(lang_code)
    else:
        raise ValueError(f"direction must be 'input' or 'output', got {direction!r}")

    _extract_tar(archive_path, target)

    if not _layout_ok(target):
        # Help the user diagnose what's missing instead of leaving them
        # to grep the docs.
        missing = []
        for fname in ("model.bin", "source.spm", "target.spm"):
            if not (target / fname).is_file():
                missing.append(fname)
        raise ValueError(
            f"Extracted archive at {target} is missing: {', '.join(missing)}. "
            "Did the Colab snippet copy source.spm + target.spm from the HF "
            "cache into the output dir before tar-gz'ing? See "
            "docs/helsinki-translator.md."
        )

    logger.info("Helsinki: installed %s/%s from upload at %s",
                lang_code, direction, target)

    # Refresh translator caches so the next request sees the new model.
    try:
        from core.translation.local_translator import reload_translators
        reload_translators()
    except Exception:
        pass


async def install_helsinki_pair(lang_code: str) -> None:
    """Install both directions for a Helsinki language pair."""
    global _download_state

    entry = get_entry(lang_code)
    if entry is None:
        _download_state = {
            "active": False, "package": "", "progress": 0.0,
            "error": f"unknown helsinki language: {lang_code}", "done": True,
        }
        return

    _download_state = {
        "active": True,
        "package": f"helsinki:{lang_code}↔en",
        "progress": 0.0,
        "error": "",
        "done": False,
    }
    try:
        await _install_one(
            entry.get("input_url", ""),
            entry.get("input_sha256", "") or "",
            _input_pair_dir(lang_code),
            f"{lang_code}→en",
        )
        await _install_one(
            entry.get("output_url", ""),
            entry.get("output_sha256", "") or "",
            _output_pair_dir(lang_code),
            f"en→{lang_code}",
        )
        _download_state["progress"] = 100.0
        _download_state["done"] = True
        _download_state["active"] = False

        # Auto-activate if nothing was active before.
        if not get_nested("translation.active_lang", ""):
            activate_helsinki_lang(lang_code)
    except Exception as exc:
        logger.exception("Helsinki install failed: %s", exc)
        _download_state["error"] = str(exc)
        _download_state["active"] = False
        _download_state["done"] = True


# ── Activate / delete ───────────────────────────────────────────────


def activate_helsinki_lang(lang_code: str) -> bool:
    """Activate a Helsinki language pair: writes engine + lang + reload.

    Both directions must already be installed (either via download or
    by being dropped on disk). If a direction is missing we still write
    the config — voice pipeline will degrade gracefully via
    pass-through and log a warning on first call.
    """
    update_config("translation", "active_lang", lang_code)
    update_config("translation", "engine", "helsinki")
    update_config("translation", "enabled", True)
    from core.translation.local_translator import reload_translators
    reload_translators()
    logger.info("Translation activated: helsinki/%s↔en", lang_code)
    return True


def delete_helsinki_pair(lang_code: str) -> bool:
    """Delete both directions of a Helsinki language pair from disk."""
    active = get_nested("translation.active_lang", "")
    active_engine = get_nested("translation.engine", "argos")
    if lang_code == active and active_engine == "helsinki":
        return False  # cannot delete the currently active pair

    removed = False
    for d in (_input_pair_dir(lang_code), _output_pair_dir(lang_code)):
        if d.is_dir():
            shutil.rmtree(d)
            logger.info("Helsinki: removed %s", d)
            removed = True

    if removed:
        from core.translation.local_translator import reload_translators
        reload_translators()
    return removed
