from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

import numpy as np

DEFAULT_EMBEDDINGS_PATH = Path("data/message_embeddings.json")


class Embedder:
    def __init__(self, model_name: str = "intfloat/multilingual-e5-large", batch_size: int = 64):
        self.model_name = model_name
        self.batch_size = batch_size
        self._model = None
        self._embedding_dim: int | None = None

    @property
    def model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.model_name)
            dim_method = getattr(self._model, "get_embedding_dimension", None)
            if dim_method is not None:
                self._embedding_dim = dim_method()
            else:
                self._embedding_dim = self._model.get_sentence_embedding_dimension()
        return self._model

    @property
    def embedding_dim(self) -> int:
        if self._embedding_dim is None:
            _ = self.model
        return self._embedding_dim  # type: ignore[return-value]

    def _prepare_text(self, text: str) -> str:
        if "e5" in self.model_name.lower():
            return f"passage: {text}"
        return text

    def encode_messages(self, texts: Sequence[str]) -> list[np.ndarray]:
        if not texts:
            return []

        embeddings: list[np.ndarray] = []
        for start in range(0, len(texts), self.batch_size):
            batch = list(texts[start : start + self.batch_size])
            encoded = self.encode_batch(batch)
            embeddings.extend(encoded)
        return embeddings

    def encode_batch(self, texts: Sequence[str]) -> list[np.ndarray]:
        if not texts:
            return []

        dim = self.embedding_dim
        vectors: list[np.ndarray] = []
        non_empty_indices: list[int] = []
        non_empty_texts: list[str] = []

        for idx, text in enumerate(texts):
            if text and text.strip():
                non_empty_indices.append(idx)
                non_empty_texts.append(self._prepare_text(text.strip()))

        batch_vectors = np.zeros((len(texts), dim), dtype=np.float32)
        if non_empty_texts:
            encoded = self.model.encode(
                non_empty_texts,
                convert_to_numpy=True,
                normalize_embeddings=True,
                show_progress_bar=False,
            )
            for local_idx, global_idx in enumerate(non_empty_indices):
                batch_vectors[global_idx] = encoded[local_idx]

        for row in batch_vectors:
            vectors.append(row)
        return vectors


class MessageEmbeddings:
    def __init__(
        self,
        embeddings: list[np.ndarray],
        metadata: dict[str, Any] | None = None,
    ):
        self.embeddings = embeddings
        self.metadata = metadata or {}

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> MessageEmbeddings:
        vectors = [np.asarray(row, dtype=np.float32) for row in payload["embeddings"]]
        return cls(embeddings=vectors, metadata=payload.get("metadata", {}))

    @classmethod
    def load(cls, path: Path | str) -> MessageEmbeddings:
        with Path(path).open(encoding="utf-8") as f:
            return cls.from_dict(json.load(f))

    def embedding_for(self, message_index: int) -> np.ndarray:
        return self.embeddings[message_index]

    def as_list(self) -> list[np.ndarray]:
        return self.embeddings


def load_message_embeddings(
    input_path: Path | str,
    embeddings_path: Path | str = DEFAULT_EMBEDDINGS_PATH,
    model_name: str = "intfloat/multilingual-e5-large",
    batch_size: int = 64,
    embedder: Embedder | None = None,
) -> list[np.ndarray]:
    embeddings_file = Path(embeddings_path)
    if not embeddings_file.exists():
        from threads_split.embedding.prepare_embeddings import run as prepare_embeddings

        prepare_embeddings(
            input_path=input_path,
            output_path=embeddings_file,
            model_name=model_name,
            batch_size=batch_size,
            embedder=embedder,
        )
    return MessageEmbeddings.load(embeddings_file).as_list()


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    if a.size == 0 or b.size == 0:
        return 0.0
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))
