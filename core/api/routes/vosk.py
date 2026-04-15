"""
core/api/routes/vosk.py — Vosk STT model management API.

Endpoints:
  GET    /vosk/status              — active model, STT readiness, language
  GET    /vosk/catalog             — model list (cached from alphacephei.com)
  POST   /vosk/download            — start model download
  GET    /vosk/download/status     — current download progress
  WS     /vosk/download/ws         — WebSocket real-time download progress
  POST   /vosk/activate            — activate an installed model
  DELETE /vosk/model/{name}        — delete an installed model
  DELETE /vosk/models/inactive     — delete all inactive models
  GET    /vosk/wake-word           — current wake word phrases
  POST   /vosk/wake-word           — save phrases + LLM generation of variants
  POST   /vosk/language            — change STT language

No module_token auth — localhost only, protected by iptables.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import time
import zipfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from core.config_writer import get_value, read_config, update_config

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/vosk", tags=["vosk"])

# ── Constants ────────────────────────────────────────────────────────────

CATALOG_URL = "https://alphacephei.com/vosk/models/model-list.json"
CATALOG_CACHE_FILE = "vosk_catalog_cache.json"
CATALOG_MAX_AGE_DAYS = 30

# Download state — persisted in memory (survives page refresh, not container restart)
_download_state: dict[str, Any] = {
    "active": False,
    "model_name": "",
    "progress": 0.0,
    "total_bytes": 0,
    "downloaded_bytes": 0,
    "error": "",
    "done": False,
}
_download_lock = asyncio.Lock()


# ── Request/Response Models ──────────────────────────────────────────────

class DownloadRequest(BaseModel):
    name: str


class ActivateRequest(BaseModel):
    name: str


class WakeWordRequest(BaseModel):
    phrases: list[str]


class LanguageRequest(BaseModel):
    lang: str


# ── Helpers ──────────────────────────────────────────────────────────────

def _models_dir() -> str:
    """Get Vosk models directory from config."""
    try:
        cfg = read_config()
        d = cfg.get("stt", {}).get("vosk", {}).get("models_dir", "")
        if d and os.path.isdir(d):
            return d
    except Exception:
        pass
    # Fallback locations
    for path in ["/var/lib/selena/models/vosk", "data/vosk_models"]:
        if os.path.isdir(path):
            return path
    # Create default if nothing exists
    default = "data/vosk_models"
    os.makedirs(default, exist_ok=True)
    return default


def _data_dir() -> str:
    """Get data directory for cache files."""
    d = "data"
    os.makedirs(d, exist_ok=True)
    return d


def _active_model() -> str:
    """Get currently active model name from config."""
    try:
        return get_value("stt", "vosk", {}).get("active_model", "")
    except Exception:
        return ""


def _installed_models() -> list[dict[str, Any]]:
    """List installed Vosk models with metadata."""
    mdir = _models_dir()
    if not os.path.isdir(mdir):
        return []

    active = _active_model()
    models = []
    for entry in sorted(os.listdir(mdir)):
        full = os.path.join(mdir, entry)
        if not os.path.isdir(full):
            continue
        # Check if it looks like a Vosk model
        from core.stt.factory import _is_vosk_model
        if not _is_vosk_model(full):
            continue

        # Calculate directory size
        size_mb = 0
        try:
            total = sum(f.stat().st_size for f in Path(full).rglob("*") if f.is_file())
            size_mb = round(total / (1024 * 1024), 1)
        except Exception:
            pass

        models.append({
            "name": entry,
            "path": full,
            "size_mb": size_mb,
            "active": entry == active,
            "installed": True,
        })
    return models


def _get_voice_core():
    """Get voice-core module instance (for STT provider access)."""
    try:
        from core.module_loader.sandbox import get_sandbox
        return get_sandbox().get_in_process_module("voice-core")
    except Exception:
        return None


# ── GET /vosk/status ─────────────────────────────────────────────────────

@router.get("/status")
async def vosk_status() -> dict[str, Any]:
    """Active model info, STT readiness, language."""
    active = _active_model()
    installed = _installed_models()
    lang = "en"
    ready = False
    loading = False
    provider_name = "none"

    vc = _get_voice_core()
    if vc and hasattr(vc, "_stt_provider"):
        p = vc._stt_provider
        if p and hasattr(p, "status"):
            st = p.status()
            ready = st.get("ready", False)
            loading = st.get("loading", False)
            lang = st.get("lang", "en")
            provider_name = "vosk"
        elif p:
            provider_name = type(p).__name__

    return {
        "provider": provider_name,
        "active_model": active,
        "lang": lang,
        "ready": ready,
        "loading": loading,
        "installed_count": len(installed),
        "models_dir": _models_dir(),
    }


# ── GET /vosk/catalog ────────────────────────────────────────────────────

@router.get("/catalog")
async def vosk_catalog(
    lang: str = Query("", description="Filter by language code (e.g. 'en', 'uk')"),
    quality: str = Query("", description="Filter by model type: 'small' | 'big' | ''"),
    q: str = Query("", description="Search by model name"),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=1000),
) -> dict[str, Any]:
    """Model catalog from alphacephei.com with caching, filtering, pagination.

    Returns:
        models: page slice of filtered models
        total / page / per_page / pages: pagination metadata
        languages: sorted list of {code, label, count} facets across the
                   FULL catalog (so the frontend can populate the language
                   dropdown without fetching every page)
        offline / cached_at: cache status
    """
    catalog = await _load_catalog()

    if not catalog:
        raise HTTPException(status_code=503, detail="Model catalog unavailable (no cache, no internet)")

    all_models = catalog.get("models", [])
    if not all_models and isinstance(catalog, list):
        all_models = catalog

    # Enrich with installed/active status (mutates catalog dicts in cache —
    # acceptable since the cache is per-process)
    installed_names = {m["name"] for m in _installed_models()}
    active = _active_model()
    for m in all_models:
        m["installed"] = m.get("name", "") in installed_names
        m["active"] = m.get("name", "") == active

    # Build language facets from the FULL catalog before any filter is
    # applied, so dropdown options never disappear when the user filters.
    lang_counts: dict[str, dict[str, Any]] = {}
    for m in all_models:
        code = (m.get("lang") or "").lower()
        if not code:
            continue
        label = m.get("lang_text") or code.upper()
        node = lang_counts.setdefault(code, {"code": code, "label": label, "count": 0})
        node["count"] += 1
    languages = sorted(lang_counts.values(), key=lambda x: (-x["count"], x["code"]))

    # Apply filters
    models = all_models
    if lang:
        lang_lower = lang.lower()
        models = [m for m in models if (m.get("lang") or "").lower() == lang_lower]
    if quality:
        q_lower = quality.lower()
        models = [m for m in models if (m.get("type") or "").lower() == q_lower]
    if q:
        q_lower = q.lower()
        models = [m for m in models if q_lower in (m.get("name") or "").lower()
                  or q_lower in (m.get("lang") or "").lower()
                  or q_lower in (m.get("type") or "").lower()]

    # Pagination
    total = len(models)
    start = (page - 1) * per_page
    end = start + per_page
    page_models = models[start:end]

    return {
        "models": page_models,
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": (total + per_page - 1) // per_page,
        "languages": languages,
        "offline": catalog.get("_offline", False),
        "cached_at": catalog.get("_cached_at", ""),
    }


async def _load_catalog() -> dict[str, Any]:
    """Load catalog from cache or fetch from alphacephei.com."""
    cache_path = os.path.join(_data_dir(), CATALOG_CACHE_FILE)

    # Check cache freshness
    cached = None
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r") as f:
                cached = json.load(f)
            cached_ts = cached.get("_cached_at_ts", 0)
            age_days = (time.time() - cached_ts) / 86400
            if age_days < CATALOG_MAX_AGE_DAYS:
                return cached
        except Exception:
            pass

    # Fetch fresh catalog
    try:
        import httpx
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(CATALOG_URL)
            resp.raise_for_status()
            models = resp.json()

        # Wrap and cache
        result = {
            "models": models if isinstance(models, list) else [],
            "_cached_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "_cached_at_ts": time.time(),
            "_offline": False,
        }
        try:
            with open(cache_path, "w") as f:
                json.dump(result, f, ensure_ascii=False)
        except Exception as e:
            logger.warning("Failed to write catalog cache: %s", e)

        return result

    except Exception as e:
        logger.warning("Failed to fetch Vosk model catalog: %s", e)
        # Return stale cache if available
        if cached:
            cached["_offline"] = True
            return cached
        return {}


# ── POST /vosk/download ──────────────────────────────────────────────────

@router.post("/download")
async def vosk_download(req: DownloadRequest) -> dict[str, Any]:
    """Start downloading a Vosk model by name."""
    async with _download_lock:
        if _download_state["active"]:
            raise HTTPException(status_code=409, detail="Download already in progress")

        # Find model URL from catalog
        catalog = await _load_catalog()
        models = catalog.get("models", [])
        if isinstance(catalog, list):
            models = catalog

        model_info = None
        for m in models:
            if m.get("name") == req.name:
                model_info = m
                break

        if not model_info:
            raise HTTPException(status_code=404, detail=f"Model '{req.name}' not found in catalog")

        url = model_info.get("url", "")
        if not url:
            raise HTTPException(status_code=400, detail="Model has no download URL")

        # Check if already installed
        mdir = _models_dir()
        target = os.path.join(mdir, req.name)
        if os.path.isdir(target):
            raise HTTPException(status_code=409, detail="Model already installed")

        # Reset state and start download
        _download_state.update({
            "active": True,
            "model_name": req.name,
            "progress": 0.0,
            "total_bytes": model_info.get("size", 0),
            "downloaded_bytes": 0,
            "error": "",
            "done": False,
        })

    asyncio.create_task(_download_model(url, req.name))
    return {"status": "started", "model": req.name}


async def _download_model(url: str, name: str) -> None:
    """Background task: download and extract Vosk model zip."""
    mdir = _models_dir()
    os.makedirs(mdir, exist_ok=True)
    zip_path = os.path.join(mdir, f"{name}.zip")

    try:
        import httpx
        async with httpx.AsyncClient(timeout=600.0, follow_redirects=True) as client:
            async with client.stream("GET", url) as resp:
                resp.raise_for_status()
                total = int(resp.headers.get("content-length", 0))
                if total:
                    _download_state["total_bytes"] = total

                downloaded = 0
                with open(zip_path, "wb") as f:
                    async for chunk in resp.aiter_bytes(chunk_size=65536):
                        f.write(chunk)
                        downloaded += len(chunk)
                        _download_state["downloaded_bytes"] = downloaded
                        if total:
                            _download_state["progress"] = round(downloaded / total * 100, 1)

        # Extract zip
        logger.info("Extracting Vosk model %s...", name)
        _download_state["progress"] = 99.0

        loop = asyncio.get_running_loop()
        target_dir = os.path.join(mdir, name)
        await loop.run_in_executor(None, _extract_model_zip, zip_path, mdir, name)

        # Clean up zip
        try:
            os.remove(zip_path)
        except Exception:
            pass

        _download_state["progress"] = 100.0
        _download_state["done"] = True
        logger.info("Vosk model %s downloaded and extracted", name)

    except Exception as e:
        logger.error("Vosk model download failed: %s", e)
        _download_state["error"] = str(e)
        # Clean up partial download
        try:
            os.remove(zip_path)
        except Exception:
            pass
    finally:
        _download_state["active"] = False


def _extract_model_zip(zip_path: str, models_dir: str, name: str) -> None:
    """Extract model zip archive. Handles nested directory structure."""
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(models_dir)

    # Vosk zips often contain a single top-level directory with a different name
    # Check if the extracted directory matches the expected name
    target = os.path.join(models_dir, name)
    if not os.path.isdir(target):
        # Find the extracted directory (newest dir that's not the target)
        for entry in os.listdir(models_dir):
            full = os.path.join(models_dir, entry)
            if os.path.isdir(full) and entry != name and not entry.endswith(".zip"):
                from core.stt.factory import _is_vosk_model
                if _is_vosk_model(full):
                    os.rename(full, target)
                    break


# ── GET /vosk/download/status ────────────────────────────────────────────

@router.get("/download/status")
async def vosk_download_status() -> dict[str, Any]:
    """Current download progress (persisted on server, survives page refresh)."""
    return {
        "active": _download_state["active"],
        "model_name": _download_state["model_name"],
        "progress": _download_state["progress"],
        "total_bytes": _download_state["total_bytes"],
        "downloaded_bytes": _download_state["downloaded_bytes"],
        "error": _download_state["error"],
        "done": _download_state["done"],
    }


# ── POST /vosk/activate ─────────────────────────────────────────────────

@router.post("/activate")
async def vosk_activate(req: ActivateRequest) -> dict[str, Any]:
    """Activate an installed Vosk model."""
    mdir = _models_dir()
    model_path = os.path.join(mdir, req.name)

    if not os.path.isdir(model_path):
        raise HTTPException(status_code=404, detail=f"Model '{req.name}' not installed")

    from core.stt.factory import _is_vosk_model
    if not _is_vosk_model(model_path):
        raise HTTPException(status_code=400, detail=f"'{req.name}' is not a valid Vosk model")

    # Save to config
    update_config("stt", "vosk", {
        "models_dir": mdir,
        "active_model": req.name,
    })

    # Reload STT provider in voice-core
    vc = _get_voice_core()
    if vc and hasattr(vc, "_stt_provider"):
        from core.stt.vosk_provider import VoskProvider
        p = vc._stt_provider
        if isinstance(p, VoskProvider):
            lang = p.lang
            # Detect language from model name
            name_lower = req.name.lower()
            if "uk" in name_lower:
                lang = "uk"
            elif "en" in name_lower:
                lang = "en"
            elif "ru" in name_lower:
                lang = "ru"
            await p.reload_model(model_path, lang)
        else:
            # Replace provider entirely
            from core.stt.factory import create_stt_provider
            vc._stt_provider = create_stt_provider()

    return {"status": "ok", "active_model": req.name}


# ── DELETE /vosk/model/{name} ────────────────────────────────────────────

@router.delete("/model/{name}")
async def vosk_delete_model(name: str) -> dict[str, Any]:
    """Delete an installed model."""
    active = _active_model()
    if name == active:
        raise HTTPException(status_code=409, detail="Cannot delete active model. Activate another model first.")

    installed = _installed_models()
    if len(installed) <= 1:
        raise HTTPException(status_code=409, detail="Cannot delete the only installed model.")

    mdir = _models_dir()
    model_path = os.path.join(mdir, name)
    if not os.path.isdir(model_path):
        raise HTTPException(status_code=404, detail=f"Model '{name}' not found")

    try:
        shutil.rmtree(model_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete model: {e}")

    return {"status": "deleted", "model": name}


# ── DELETE /vosk/models/inactive ─────────────────────────────────────────

@router.delete("/models/inactive")
async def vosk_delete_inactive() -> dict[str, Any]:
    """Delete all inactive (non-active) models."""
    active = _active_model()
    installed = _installed_models()
    deleted = []

    for m in installed:
        if m["active"]:
            continue
        try:
            shutil.rmtree(m["path"])
            deleted.append(m["name"])
        except Exception as e:
            logger.warning("Failed to delete model %s: %s", m["name"], e)

    return {"status": "ok", "deleted": deleted, "count": len(deleted)}


# ── GET /vosk/wake-word ──────────────────────────────────────────────────

@router.get("/wake-word")
async def vosk_get_wake_word() -> dict[str, Any]:
    """Current wake word phrases and generated variants."""
    try:
        cfg = read_config()
        wake_cfg = cfg.get("voice", {}).get("wake_word", {})
        phrases = wake_cfg.get("phrases", [])
        variants = wake_cfg.get("vosk_grammar_variants", [])
        lang = wake_cfg.get("lang", "")
    except Exception:
        phrases = []
        variants = []
        lang = ""

    return {
        "phrases": phrases,
        "variants": variants,
        "lang": lang,
    }


# ── POST /vosk/wake-word ────────────────────────────────────────────────

@router.post("/wake-word")
async def vosk_save_wake_word(req: WakeWordRequest) -> dict[str, Any]:
    """Save wake word phrases and generate pronunciation variants via LLM.

    LLM generates phonetic variants for each phrase in the active language.
    All variants are fed to Vosk grammar recognizer.
    """
    if not req.phrases:
        raise HTTPException(status_code=400, detail="At least one phrase required")

    # Get active language
    try:
        cfg = read_config()
        lang = cfg.get("voice", {}).get("tts", {}).get("primary", {}).get("lang", "en")
    except Exception:
        lang = "en"

    # Generate pronunciation variants via LLM
    variants = await _generate_wake_variants(req.phrases, lang)

    # Save to config
    wake_cfg = {
        "phrases": req.phrases,
        "vosk_grammar_variants": variants,
        "lang": lang,
    }
    update_config("voice", "wake_word", wake_cfg)

    # Update Vosk grammar in running provider
    vc = _get_voice_core()
    if vc and hasattr(vc, "_stt_provider"):
        from core.stt.vosk_provider import VoskProvider
        p = vc._stt_provider
        if isinstance(p, VoskProvider):
            p.set_grammar(variants)

    return {
        "status": "ok",
        "phrases": req.phrases,
        "variants": variants,
        "lang": lang,
    }


async def _generate_wake_variants(phrases: list[str], lang: str) -> list[str]:
    """Generate pronunciation variants for wake word phrases using LLM.

    The LLM expands each phrase into multiple phonetic variants
    that Vosk might recognize (lowercase, with common mishearings).
    """
    # Start with original phrases (lowercase, stripped)
    variants: list[str] = []
    for p in phrases:
        clean = p.strip().lower()
        if clean and clean not in variants:
            variants.append(clean)

    # Try LLM generation
    try:
        from core.module_loader.sandbox import get_sandbox
        llm = get_sandbox().get_in_process_module("llm-engine")
        if llm and hasattr(llm, "generate"):
            lang_name = {"en": "English", "uk": "Ukrainian", "ru": "Russian"}.get(lang, lang)
            prompt = (
                f"Generate pronunciation variants for speech recognition wake words. "
                f"Language: {lang_name}. "
                f"Original phrases: {', '.join(phrases)}. "
                f"Return ONLY a JSON array of lowercase strings — the original phrases "
                f"plus common phonetic variants, mishearings, and abbreviations. "
                f"Maximum 15 variants total. No explanations."
            )
            result = await llm.generate(prompt, max_tokens=200)
            if result:
                # Parse JSON array from LLM response
                text = result.strip()
                # Find JSON array in response
                start = text.find("[")
                end = text.rfind("]")
                if start >= 0 and end > start:
                    arr = json.loads(text[start:end + 1])
                    if isinstance(arr, list):
                        for v in arr:
                            v_clean = str(v).strip().lower()
                            if v_clean and v_clean not in variants:
                                variants.append(v_clean)
    except Exception as e:
        logger.warning("LLM wake word variant generation failed: %s", e)

    return variants


# ── WS /vosk/download/ws ─────────────────────────────────────────────

@router.websocket("/download/ws")
async def vosk_download_ws(websocket: WebSocket) -> None:
    """WebSocket real-time download progress.

    Sends JSON every 500ms: {"active", "model_name", "progress", "total_bytes",
    "downloaded_bytes", "error", "done"}.
    Closes when download completes or errors.
    """
    await websocket.accept()
    try:
        while True:
            await websocket.send_json({
                "active": _download_state["active"],
                "model_name": _download_state["model_name"],
                "progress": _download_state["progress"],
                "total_bytes": _download_state["total_bytes"],
                "downloaded_bytes": _download_state["downloaded_bytes"],
                "error": _download_state["error"],
                "done": _download_state["done"],
            })
            if _download_state["done"] or _download_state["error"]:
                await asyncio.sleep(0.5)
                await websocket.send_json({
                    "active": _download_state["active"],
                    "model_name": _download_state["model_name"],
                    "progress": _download_state["progress"],
                    "done": _download_state["done"],
                    "error": _download_state["error"],
                })
                break
            if not _download_state["active"] and not _download_state["done"]:
                break
            await asyncio.sleep(0.5)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        try:
            await websocket.close()
        except Exception:
            pass


# ── POST /vosk/language ──────────────────────────────────────────────

@router.post("/language")
async def vosk_set_language(req: LanguageRequest) -> dict[str, Any]:
    """Change STT language. Triggers model switch if needed.

    Also checks if current wake word phrases match the new language
    and returns a mismatch warning if not.
    """
    lang = req.lang.strip().lower()
    if not lang or len(lang) > 5:
        raise HTTPException(status_code=400, detail="Invalid language code")

    # Check wake word language mismatch
    wake_mismatch = False
    try:
        cfg = read_config()
        wake_cfg = cfg.get("voice", {}).get("wake_word", {})
        wake_lang = wake_cfg.get("lang", "")
        if wake_lang and wake_lang != lang:
            wake_mismatch = True
    except Exception:
        pass

    # Try to find a model for this language
    mdir = _models_dir()
    installed = _installed_models()
    matching_model = None
    for m in installed:
        name_lower = m["name"].lower()
        if lang in name_lower or f"-{lang}-" in name_lower:
            matching_model = m["name"]
            break

    # Update TTS primary language (which drives STT language selection)
    try:
        cfg = read_config()
        tts_cfg = cfg.get("voice", {}).get("tts", {})
        primary = tts_cfg.get("primary", {})
        primary["lang"] = lang
        tts_cfg["primary"] = primary
        update_config("voice", "tts", tts_cfg)
    except Exception as e:
        logger.warning("Failed to update TTS lang config: %s", e)

    # Activate matching model if found
    activated = None
    if matching_model:
        active = _active_model()
        if matching_model != active:
            model_path = os.path.join(mdir, matching_model)
            update_config("stt", "vosk", {
                "models_dir": mdir,
                "active_model": matching_model,
            })
            vc = _get_voice_core()
            if vc and hasattr(vc, "_stt_provider"):
                from core.stt.vosk_provider import VoskProvider
                p = vc._stt_provider
                if isinstance(p, VoskProvider):
                    await p.reload_model(model_path, lang)
            activated = matching_model

    return {
        "status": "ok",
        "lang": lang,
        "activated_model": activated,
        "wake_word_mismatch": wake_mismatch,
        "wake_word_mismatch_msg": (
            f"Wake word phrases were generated for '{wake_lang}'. "
            f"Update wake word settings to regenerate for '{lang}'."
        ) if wake_mismatch else None,
    }
