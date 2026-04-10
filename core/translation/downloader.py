"""
core/translation/downloader.py

Download and convert Helsinki-NLP opus-mt models to CTranslate2 int8 format.

Models:
  - Helsinki-NLP/opus-mt-mul-en  (any → English, ~300 MB)
  - Helsinki-NLP/opus-mt-en-mul  (English → any, ~300 MB)

Conversion uses ct2-opus-mt-converter (from ctranslate2 package) to produce
int8-quantized models that run in ~150 MB RAM each.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path
from typing import Any

from core.config_writer import get_value, update_config

logger = logging.getLogger(__name__)

MODELS: dict[str, tuple[str, str, str]] = {
    # direction: (huggingface_id, config_key, dir_name)
    "input": ("Helsinki-NLP/opus-mt-mul-en", "input_model_dir", "mul-en"),
    "output": ("Helsinki-NLP/opus-mt-en-mul", "output_model_dir", "en-mul"),
}

_status: dict[str, dict[str, str]] = {
    "input": {"state": "idle", "progress": "", "error": ""},
    "output": {"state": "idle", "progress": "", "error": ""},
}


def get_status(direction: str) -> dict[str, str]:
    return dict(_status.get(direction, {"state": "idle", "progress": "", "error": ""}))


async def download_model(direction: str) -> None:
    """Download and convert a translation model. Updates _status in-place."""
    if direction not in MODELS:
        return

    hf_id, config_key, dir_name = MODELS[direction]
    model_dir = Path(get_value(
        "translation", config_key,
        f"/var/lib/selena/models/translate/{dir_name}",
    ))

    if (model_dir / "model.bin").exists():
        _status[direction] = {
            "state": "ready", "progress": "already downloaded", "error": "",
        }
        return

    _status[direction] = {"state": "downloading", "progress": "starting...", "error": ""}

    try:
        model_dir.parent.mkdir(parents=True, exist_ok=True)
        tmp_dir = model_dir.parent / f"{dir_name}_hf_tmp"

        # Step 1: Download from HuggingFace
        _status[direction]["progress"] = f"downloading {hf_id}..."
        proc = await asyncio.create_subprocess_exec(
            "python3", "-c",
            f"from huggingface_hub import snapshot_download; "
            f"snapshot_download('{hf_id}', local_dir='{tmp_dir}')",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"HF download failed: {stderr.decode()[:300]}")

        # Step 2: Convert to CTranslate2 int8
        _status[direction]["progress"] = "converting to CTranslate2 int8..."
        proc2 = await asyncio.create_subprocess_exec(
            "ct2-opus-mt-converter",
            "--model", str(tmp_dir),
            "--output_dir", str(model_dir),
            "--quantization", "int8",
            "--force",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr2 = await proc2.communicate()
        if proc2.returncode != 0:
            raise RuntimeError(f"CTranslate2 conversion failed: {stderr2.decode()[:300]}")

        # Cleanup temp
        shutil.rmtree(tmp_dir, ignore_errors=True)

        # Enable translation if both models are now ready
        other = "output" if direction == "input" else "input"
        other_key, other_default = MODELS[other][1], f"/var/lib/selena/models/translate/{MODELS[other][2]}"
        other_dir = Path(get_value("translation", other_key, other_default))
        if (other_dir / "model.bin").exists():
            update_config("translation", "enabled", True)

        from core.translation.local_translator import reload_translators
        reload_translators()

        _status[direction] = {"state": "ready", "progress": "done", "error": ""}
        logger.info("Translation model %s ready at %s", hf_id, model_dir)

    except Exception as exc:
        _status[direction] = {"state": "error", "progress": "", "error": str(exc)[:200]}
        logger.error("Translation model download failed [%s]: %s", direction, exc)
