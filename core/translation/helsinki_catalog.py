"""
core/translation/helsinki_catalog.py — static list of supported
Helsinki-NLP / opus-mt language pairs for the Helsinki engine.

Each entry describes ONE language pair (lang ↔ en) and the two
pre-converted CTranslate2 model archives that back it. ``input_*``
fields cover ``lang → en`` (consumed by HelsinkiInputTranslator after
Vosk STT) and ``output_*`` cover ``en → lang`` (consumed by
HelsinkiOutputTranslator before Piper TTS).

URLs point at GitHub release assets on dotradepro/SelenaCore. Day-1
behaviour: if the model directory is already on disk under
``translation.{input,output}_model_dir`` (e.g. you dropped the Colab
output there manually), the downloader skips the URL fetch entirely
and the catalog row is reported as ``installed=True``. The URLs only
matter when another user wants to install through the UI.

To add a new language pair:
  1. Run the Colab snippet in docs/helsinki-translator.md for the new
     pair → produces opus-mt-{src}-{tgt}-ct2.tar.gz × 2.
  2. Upload both archives as release assets.
  3. Append a row below with the URLs + sha256 + size.
"""
from __future__ import annotations

from typing import Any

# Single source of truth. Order = display order in the UI catalog.
#
# For Ukrainian we deliberately use the Tatoeba Challenge "tc-big"
# East-Slavic models, NOT the original opus-mt-uk-en / opus-mt-en-uk
# pairs. Two reasons:
#
# 1. opus-mt-en-uk has a known bug where it sometimes produces Russian
#    text instead of Ukrainian. Open issue on the Helsinki-NLP repo,
#    no fix coming. Confirmed in our trace bench: voice replies were
#    Russian until we switched models.
#
# 2. tc-big-zle-en (East Slavic → English) and tc-big-en-zle (English
#    → East Slavic) are newer (2022+), trained on more data, and
#    cover Ukrainian / Russian / Belarusian with one model each. The
#    output direction needs a leading language token piece (`>>ukr<<`)
#    which the wrapper handles via _OUTPUT_LANG_TOKENS in
#    core/translation/helsinki_translator.py.
#
# Sizes below are int8-quantized .tar.gz from the standard Colab
# conversion (see docs/helsinki-translator.md).
HELSINKI_CATALOG: list[dict[str, Any]] = [
    {
        "lang_code": "uk",
        "lang_name": "Ukrainian",
        "input_model": "Helsinki-NLP/opus-mt-tc-big-zle-en",
        "input_url": (
            "https://github.com/dotradepro/SelenaCore/releases/download/"
            "translators-v1/opus-mt-tc-big-zle-en-ct2-int8.tar.gz"
        ),
        "input_sha256": "",  # filled after upload
        "input_size_mb": 240,
        "output_model": "Helsinki-NLP/opus-mt-tc-big-en-zle",
        "output_url": (
            "https://github.com/dotradepro/SelenaCore/releases/download/"
            "translators-v1/opus-mt-tc-big-en-zle-ct2-int8.tar.gz"
        ),
        "output_sha256": "",  # filled after upload
        "output_size_mb": 240,
    },
]


def get_entry(lang_code: str) -> dict[str, Any] | None:
    """Lookup a catalog row by language code, or None."""
    for row in HELSINKI_CATALOG:
        if row["lang_code"] == lang_code:
            return row
    return None
