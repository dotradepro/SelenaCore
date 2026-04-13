"""Drop-in ONNX Runtime replacement for SentenceTransformer.encode().

Provides the same ``encode(text_or_texts, normalize_embeddings=...)`` API
using ONNX Runtime + HuggingFace ``tokenizers``.  Combined footprint is
~30 MB RAM vs ~1.5 GB for the PyTorch-backed sentence-transformers pipeline.

Required files in *model_dir*:
    model.onnx      — ONNX-exported all-MiniLM-L6-v2 (~22 MB)
    tokenizer.json  — fast tokenizer config (~700 KB)
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)


class OnnxMiniLMEmbedder:
    """Lightweight ONNX-based text embedder (all-MiniLM-L6-v2).

    Replicates the ``SentenceTransformer.encode()`` contract:
        * single string  → ndarray shape ``(384,)``
        * list of strings → ndarray shape ``(N, 384)``
        * ``normalize_embeddings=True`` returns L2-unit vectors
    """

    # Maximum sequence length accepted by the model.
    _MAX_LEN = 256  # MiniLM default; 512 is the hard limit but 256 is enough
    # Embedding dimensionality (all-MiniLM-L6-v2).
    _DIM = 384

    def __init__(self, model_dir: str | Path) -> None:
        model_dir = Path(model_dir)
        onnx_path = model_dir / "model.onnx"
        tok_path = model_dir / "tokenizer.json"

        if not onnx_path.is_file():
            raise FileNotFoundError(f"ONNX model not found: {onnx_path}")
        if not tok_path.is_file():
            raise FileNotFoundError(f"Tokenizer not found: {tok_path}")

        # --- tokenizer ---------------------------------------------------
        from tokenizers import Tokenizer

        self._tokenizer = Tokenizer.from_file(str(tok_path))
        self._tokenizer.enable_truncation(max_length=self._MAX_LEN)
        # Padding is configured per-call in _tokenize() so we can pad to
        # the actual batch max length (not a fixed global value).

        # --- ONNX session -------------------------------------------------
        import onnxruntime as ort

        providers = self._select_providers()
        sess_opts = ort.SessionOptions()
        sess_opts.graph_optimization_level = (
            ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        )
        # Single-threaded intra-op is fine for a 22 MB model on Pi/Jetson.
        sess_opts.intra_op_num_threads = 1

        self._session = ort.InferenceSession(
            str(onnx_path), sess_options=sess_opts, providers=providers,
        )

        # Cache input/output names so we don't look them up on every call.
        self._input_names = [inp.name for inp in self._session.get_inputs()]
        self._output_names = [out.name for out in self._session.get_outputs()]

        logger.info(
            "OnnxMiniLMEmbedder loaded from %s (providers=%s, inputs=%s)",
            model_dir, providers, self._input_names,
        )

    # ------------------------------------------------------------------
    # Public API — matches SentenceTransformer.encode()
    # ------------------------------------------------------------------

    def encode(
        self,
        text_or_texts: str | list[str],
        normalize_embeddings: bool = False,
    ) -> np.ndarray:
        """Encode one or many strings into dense embeddings.

        Returns
        -------
        np.ndarray
            Shape ``(dim,)`` for a single string, ``(N, dim)`` for a list.
        """
        single = isinstance(text_or_texts, str)
        texts = [text_or_texts] if single else text_or_texts
        if not texts:
            return np.zeros((0, self._DIM), dtype=np.float32)

        input_ids, attention_mask, token_type_ids = self._tokenize(texts)

        feeds: dict[str, np.ndarray] = {}
        if "input_ids" in self._input_names:
            feeds["input_ids"] = input_ids
        if "attention_mask" in self._input_names:
            feeds["attention_mask"] = attention_mask
        if "token_type_ids" in self._input_names:
            feeds["token_type_ids"] = token_type_ids

        outputs = self._session.run(self._output_names, feeds)

        # The first output is ``last_hidden_state``  (batch, seq, dim).
        hidden = outputs[0]  # shape (batch, seq_len, 384)

        pooled = self._mean_pool(hidden, attention_mask)

        if normalize_embeddings:
            norms = np.linalg.norm(pooled, axis=1, keepdims=True)
            norms = np.clip(norms, a_min=1e-12, a_max=None)
            pooled = pooled / norms

        return pooled[0] if single else pooled

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _tokenize(
        self, texts: list[str],
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Tokenize a batch with dynamic padding to max-in-batch length."""
        encodings = self._tokenizer.encode_batch(texts)

        max_len = max(len(enc.ids) for enc in encodings)

        batch = len(encodings)
        input_ids = np.zeros((batch, max_len), dtype=np.int64)
        attention_mask = np.zeros((batch, max_len), dtype=np.int64)
        token_type_ids = np.zeros((batch, max_len), dtype=np.int64)

        for i, enc in enumerate(encodings):
            seq_len = len(enc.ids)
            input_ids[i, :seq_len] = enc.ids
            attention_mask[i, :seq_len] = enc.attention_mask
            if enc.type_ids:
                token_type_ids[i, :seq_len] = enc.type_ids

        return input_ids, attention_mask, token_type_ids

    @staticmethod
    def _mean_pool(
        hidden: np.ndarray, attention_mask: np.ndarray,
    ) -> np.ndarray:
        """Mean-pool token embeddings, excluding padding positions."""
        # hidden: (batch, seq, dim),  attention_mask: (batch, seq)
        mask_expanded = attention_mask[:, :, np.newaxis].astype(np.float32)
        sum_embeddings = (hidden * mask_expanded).sum(axis=1)       # (batch, dim)
        sum_mask = mask_expanded.sum(axis=1).clip(min=1e-9)         # (batch, dim)
        return (sum_embeddings / sum_mask).astype(np.float32)

    @staticmethod
    def _select_providers() -> list[str]:
        """Pick ONNX Runtime execution providers, reusing core GPU detection."""
        try:
            from core.hardware import onnxruntime_has_gpu
            if onnxruntime_has_gpu():
                return ["CUDAExecutionProvider", "CPUExecutionProvider"]
        except ImportError:
            pass
        return ["CPUExecutionProvider"]
