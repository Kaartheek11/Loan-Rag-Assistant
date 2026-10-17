"""bge embeddings (§8.2): plain passages, prefixed queries, L2-normalized, loaded once."""

from __future__ import annotations

import os
import threading

import numpy as np

QUERY_PREFIX = "Represent this sentence for searching relevant passages: "
BATCH_SIZE = 64


class Embedder:
    def __init__(self, model_name: str, device: str | None = None) -> None:
        from sentence_transformers import SentenceTransformer

        os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
        self.model_name = model_name
        # CPU by default so incremental and rebuilt indexes embed identically.
        self._model = SentenceTransformer(model_name, device=device or "cpu")
        self._tokenizer = self._model.tokenizer
        get_dim = getattr(self._model, "get_embedding_dimension", None)
        dim = get_dim() if get_dim else self._model.get_sentence_embedding_dimension()
        if dim is None:
            raise RuntimeError("embedding model has no fixed dimension")
        self.dim = int(dim)
        self._lock = threading.Lock()  # HF fast tokenizers are not safe to share across threads

    def count_tokens(self, text: str) -> int:
        with self._lock:
            return len(self._tokenizer(text, add_special_tokens=False)["input_ids"])

    def _encode(self, texts: list[str], batch_size: int) -> np.ndarray:
        with self._lock:
            vecs = self._model.encode(
                texts,
                batch_size=batch_size,
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
        out: np.ndarray = np.asarray(vecs, dtype=np.float32)
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return np.asarray(out / norms, dtype=np.float32)

    def embed_passages(self, texts: list[str], batch_size: int = BATCH_SIZE) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        return self._encode(texts, batch_size)

    def embed_query(self, question: str) -> np.ndarray:
        vec: np.ndarray = self._encode([QUERY_PREFIX + question], 1)[0]
        return vec
