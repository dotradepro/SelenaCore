"""
core/translation/downloader.py

Translation model catalog + download/convert pipeline.

Supports three model families:
  - opus-mt-XX-en / en-XX  (language-specific, fastest, ~150MB each)
  - opus-mt-mul-en / en-mul (multilingual, ~300MB each)
  - NLLB-200-distilled-600M (bidirectional, best quality, ~600MB)

All models are converted to CTranslate2 int8 for fast CPU inference.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path
from typing import Any

from core.config_writer import get_value, get_nested, update_config

logger = logging.getLogger(__name__)

# ── Model catalog ──────────────────────────────────────────────────

CATALOG: list[dict[str, Any]] = [
    # Language-specific pairs (fastest, best for single language)
    {
        "id": "opus-mt-uk-en",
        "name": "Opus-MT Ukrainian ↔ English",
        "family": "opus-mt",
        "languages": ["uk"],
        "quality": "good",
        "speed": "fast",
        "input_hf": "Helsinki-NLP/opus-mt-uk-en",
        "output_hf": "Helsinki-NLP/opus-mt-en-uk",
        "size_mb": 300,
        "description_en": "Dedicated UK↔EN pair. Fastest, best for Ukrainian.",
        "description_uk": "Спеціалізована пара UK↔EN. Найшвидша, найкраща для української.",
    },
    {
        "id": "opus-mt-ru-en",
        "name": "Opus-MT Russian ↔ English",
        "family": "opus-mt",
        "languages": ["ru"],
        "quality": "good",
        "speed": "fast",
        "input_hf": "Helsinki-NLP/opus-mt-ru-en",
        "output_hf": "Helsinki-NLP/opus-mt-en-ru",
        "size_mb": 300,
        "description_en": "Dedicated RU↔EN pair. Fastest, best for Russian.",
        "description_uk": "Спеціалізована пара RU↔EN. Найшвидша, найкраща для російської.",
    },
    {
        "id": "opus-mt-de-en",
        "name": "Opus-MT German ↔ English",
        "family": "opus-mt",
        "languages": ["de"],
        "quality": "good",
        "speed": "fast",
        "input_hf": "Helsinki-NLP/opus-mt-de-en",
        "output_hf": "Helsinki-NLP/opus-mt-en-de",
        "size_mb": 300,
        "description_en": "Dedicated DE↔EN pair.",
        "description_uk": "Спеціалізована пара DE↔EN.",
    },
    {
        "id": "opus-mt-es-en",
        "name": "Opus-MT Spanish ↔ English",
        "family": "opus-mt",
        "languages": ["es"],
        "quality": "good",
        "speed": "fast",
        "input_hf": "Helsinki-NLP/opus-mt-es-en",
        "output_hf": "Helsinki-NLP/opus-mt-en-es",
        "size_mb": 300,
        "description_en": "Dedicated ES↔EN pair.",
        "description_uk": "Спеціалізована пара ES↔EN.",
    },
    {
        "id": "opus-mt-fr-en",
        "name": "Opus-MT French ↔ English",
        "family": "opus-mt",
        "languages": ["fr"],
        "quality": "good",
        "speed": "fast",
        "input_hf": "Helsinki-NLP/opus-mt-fr-en",
        "output_hf": "Helsinki-NLP/opus-mt-en-fr",
        "size_mb": 300,
        "description_en": "Dedicated FR↔EN pair.",
        "description_uk": "Спеціалізована пара FR↔EN.",
    },
    {
        "id": "opus-mt-pl-en",
        "name": "Opus-MT Polish ↔ English",
        "family": "opus-mt",
        "languages": ["pl"],
        "quality": "good",
        "speed": "fast",
        "input_hf": "Helsinki-NLP/opus-mt-pl-en",
        "output_hf": "Helsinki-NLP/opus-mt-en-pl",
        "size_mb": 300,
        "description_en": "Dedicated PL↔EN pair.",
        "description_uk": "Спеціалізована пара PL↔EN.",
    },
    # Multilingual (universal, supports 200+ languages)
    {
        "id": "opus-mt-mul-en",
        "name": "Opus-MT Multilingual ↔ English",
        "family": "opus-mt",
        "languages": ["mul"],
        "quality": "medium",
        "speed": "fast",
        "input_hf": "Helsinki-NLP/opus-mt-mul-en",
        "output_hf": "Helsinki-NLP/opus-mt-en-mul",
        "size_mb": 600,
        "description_en": "Universal 200+ languages. Good balance of speed and coverage.",
        "description_uk": "Універсальна 200+ мов. Баланс швидкості та покриття.",
    },
    # NLLB (best quality, slower)
    {
        "id": "nllb-200-distilled-600m",
        "name": "NLLB-200 Distilled 600M",
        "family": "nllb",
        "languages": ["mul"],
        "quality": "high",
        "speed": "medium",
        "input_hf": "facebook/nllb-200-distilled-600M",
        "output_hf": "facebook/nllb-200-distilled-600M",
        "size_mb": 600,
        "description_en": "Meta AI NLLB. Best quality for 200 languages. Bidirectional single model.",
        "description_uk": "Meta AI NLLB. Найвища якість для 200 мов. Двонаправлена одна модель.",
    },
]

# ── Download state ─────────────────────────────────────────────────

_download_state: dict[str, Any] = {
    "active": False,
    "model_id": "",
    "direction": "",
    "progress": 0.0,
    "total_bytes": 0,
    "downloaded_bytes": 0,
    "error": "",
    "done": False,
}


def get_download_status() -> dict[str, Any]:
    return dict(_download_state)


def get_catalog() -> list[dict[str, Any]]:
    """Return catalog with installed/active status."""
    base_dir = Path(get_nested(
        "translation.models_dir", "/var/lib/selena/models/translate",
    ))
    active_id = get_nested("translation.active_model", "")

    result = []
    for m in CATALOG:
        model_dir = base_dir / m["id"]
        installed = (model_dir / "input" / "model.bin").exists() and \
                    (model_dir / "output" / "model.bin").exists()
        result.append({
            **m,
            "installed": installed,
            "active": m["id"] == active_id,
        })
    return result


async def download_model(model_id: str) -> None:
    """Download and convert both directions of a translation model."""
    global _download_state

    model = next((m for m in CATALOG if m["id"] == model_id), None)
    if not model:
        _download_state = {**_download_state, "error": f"Unknown model: {model_id}", "done": True}
        return

    base_dir = Path(get_nested(
        "translation.models_dir", "/var/lib/selena/models/translate",
    ))
    model_dir = base_dir / model_id

    _download_state = {
        "active": True, "model_id": model_id, "direction": "input",
        "progress": 0.0, "total_bytes": 0, "downloaded_bytes": 0,
        "error": "", "done": False,
    }

    try:
        for direction, hf_key in [("input", "input_hf"), ("output", "output_hf")]:
            hf_id = model[hf_key]
            out_dir = model_dir / direction

            if (out_dir / "model.bin").exists():
                _download_state["direction"] = direction
                _download_state["progress"] = 100.0
                continue

            _download_state["direction"] = direction
            _download_state["progress"] = 0.0

            out_dir.mkdir(parents=True, exist_ok=True)
            tmp_dir = model_dir / f"{direction}_hf_tmp"

            # Download from HuggingFace
            _download_state["progress"] = 10.0
            proc = await asyncio.create_subprocess_exec(
                "python3", "-c",
                f"from huggingface_hub import snapshot_download; "
                f"snapshot_download('{hf_id}', local_dir='{tmp_dir}')",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                raise RuntimeError(f"Download failed: {stderr.decode()[:200]}")
            _download_state["progress"] = 60.0

            # Convert to CTranslate2
            proc2 = await asyncio.create_subprocess_exec(
                "ct2-opus-mt-converter",
                "--model", str(tmp_dir),
                "--output_dir", str(out_dir),
                "--quantization", "int8",
                "--force",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr2 = await proc2.communicate()
            if proc2.returncode != 0:
                raise RuntimeError(f"Conversion failed: {stderr2.decode()[:200]}")
            _download_state["progress"] = 95.0

            shutil.rmtree(tmp_dir, ignore_errors=True)

        _download_state["progress"] = 100.0
        _download_state["done"] = True
        _download_state["active"] = False
        logger.info("Translation model %s downloaded", model_id)

    except Exception as exc:
        _download_state["error"] = str(exc)[:200]
        _download_state["done"] = True
        _download_state["active"] = False
        logger.error("Translation model download failed [%s]: %s", model_id, exc)


def activate_model(model_id: str) -> bool:
    """Activate a downloaded translation model."""
    base_dir = Path(get_nested(
        "translation.models_dir", "/var/lib/selena/models/translate",
    ))
    model_dir = base_dir / model_id
    if not (model_dir / "input" / "model.bin").exists():
        return False

    update_config("translation", "active_model", model_id)
    update_config("translation", "input_model_dir", str(model_dir / "input"))
    update_config("translation", "output_model_dir", str(model_dir / "output"))
    update_config("translation", "enabled", True)

    from core.translation.local_translator import reload_translators
    reload_translators()
    logger.info("Translation model activated: %s", model_id)
    return True


def delete_model(model_id: str) -> bool:
    """Delete a downloaded translation model."""
    active = get_nested("translation.active_model", "")
    if model_id == active:
        return False  # Can't delete active model

    base_dir = Path(get_nested(
        "translation.models_dir", "/var/lib/selena/models/translate",
    ))
    model_dir = base_dir / model_id
    if model_dir.exists():
        shutil.rmtree(model_dir, ignore_errors=True)
        logger.info("Translation model deleted: %s", model_id)
        return True
    return False
