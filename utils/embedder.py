"""Hosted text embeddings (Gemini) behind one cached, retrying entry point (contract C5).

``embed_texts(texts, kind)`` returns an ``(n, dim)`` float32 matrix of L2-normalised
vectors; ``cosine_matrix(a, b)`` scores two such matrices. ``kind`` selects the task:
``similarity`` (symmetric, e.g. thread/claim de-duplication), ``query`` and ``document``
(asymmetric retrieval pairs). Settings come from the ``embedding`` section of
``config.json``.

Every embedded text is stored on disk, one store per ``(model, dims, kind)``, under
``<DATA_DIR>/<embedding.cache_dir>/``: ``<model>-<dims>-<kind>.npy`` (float16 matrix)
plus ``.keys.json`` (the sha1 of each exact text, row for row). Only texts missing from
the store are sent to the API; empty or whitespace-only texts get a zero vector with no
API call. Requests are retried with exponential backoff on 429, 5xx and network
timeouts; any other error raises with the model, kind and batch index.

Model families differ in how the task is passed: ``gemini-embedding-001`` takes a
``task_type`` and one string per input; ``gemini-embedding-2`` has no ``task_type`` (the
task goes into the text via ``embedding.prompt_templates``) and must receive each input
wrapped in its own ``Content``, otherwise a list is merged into one embedding. The two
embedding spaces are incompatible, so never mix vectors from different models.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import httpx
import numpy as np
from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types

from utils.config import DATA_DIR, section
from utils.file_lock import file_lock
from utils.json_io import write_json_file

logger = logging.getLogger(__name__)

Kind = Literal["similarity", "query", "document"]
STORE_DTYPE = np.float16


def text_key(text: str) -> str:
    """Cache key of a text: the sha1 of its exact UTF-8 bytes."""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _normalize_rows(matrix: np.ndarray) -> np.ndarray:
    """L2-normalise each row as float32; all-zero rows stay zero."""
    matrix = np.asarray(matrix, dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    return matrix / np.where(norms == 0.0, 1.0, norms)


def cosine_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Cosine similarity of every row of ``a`` with every row of ``b``: shape ``(n, m)``.

    Rows are re-normalised, so any non-zero vectors work; a zero row scores 0 everywhere.
    """
    return _normalize_rows(np.atleast_2d(a)) @ _normalize_rows(np.atleast_2d(b)).T


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, genai_errors.APIError):
        return exc.code == 429 or exc.code >= 500
    return isinstance(exc, (httpx.TimeoutException, httpx.TransportError))


class _VectorStore:
    """Float16 vectors keyed by text sha1, persisted as ``<stem>.npy`` + ``<stem>.keys.json``."""

    def __init__(self, stem: Path, dim: int) -> None:
        self.npy_path = stem.with_name(stem.name + ".npy")
        self.keys_path = stem.with_name(stem.name + ".keys.json")
        self.dim = dim
        self.keys: list[str] = []
        self.matrix = np.zeros((0, dim), dtype=STORE_DTYPE)
        self._index: dict[str, int] = {}
        self._loaded = False

    def _read_disk(self) -> tuple[list[str], np.ndarray]:
        empty = ([], np.zeros((0, self.dim), dtype=STORE_DTYPE))
        if not self.keys_path.exists() or not self.npy_path.exists():
            return empty
        with self.keys_path.open(encoding="utf-8") as f:
            keys: list[str] = json.load(f)
        matrix = np.load(self.npy_path)
        if matrix.ndim != 2 or matrix.shape[1] != self.dim or matrix.shape[0] < len(keys):
            logger.warning(
                f"embedding cache {self.npy_path} is inconsistent "
                f"(matrix {matrix.shape}, {len(keys)} keys, dim {self.dim}); ignoring it"
            )
            return empty
        # An interrupted save may have written the matrix but not the keys yet; rows are
        # only ever appended, so the first len(keys) rows are the consistent prefix.
        return keys, matrix[: len(keys)].astype(STORE_DTYPE, copy=False)

    def load(self) -> None:
        if self._loaded:
            return
        self.keys, self.matrix = self._read_disk()
        self._index = {key: i for i, key in enumerate(self.keys)}
        self._loaded = True
        logger.info(f"embedding cache {self.npy_path.name}: {len(self.keys)} vectors loaded")

    def __contains__(self, key: str) -> bool:
        return key in self._index

    def get(self, keys: Sequence[str]) -> np.ndarray:
        """Stored vectors for ``keys`` (all must be present) as a float32 matrix."""
        rows = [self._index[key] for key in keys]
        return self.matrix[rows].astype(np.float32)

    def add(self, keys: Sequence[str], vectors: np.ndarray) -> None:
        new = [(k, v) for k, v in zip(keys, vectors) if k not in self._index]
        if not new:
            return
        for key, _ in new:
            self._index[key] = len(self.keys)
            self.keys.append(key)
        self.matrix = np.vstack([self.matrix, np.stack([v for _, v in new]).astype(STORE_DTYPE)])

    def save(self) -> None:
        """Merge with what other processes saved meanwhile, then rewrite both files."""
        with file_lock(self.keys_path):
            disk_keys, disk_matrix = self._read_disk()
            extra = [i for i, key in enumerate(disk_keys) if key not in self._index]
            if extra:
                self.add([disk_keys[i] for i in extra], disk_matrix[extra])
            self.npy_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self.npy_path.with_name(f".{self.npy_path.name}.tmp-{os.getpid()}")
            try:
                with tmp_path.open("wb") as f:
                    np.save(f, self.matrix)
                    f.flush()
                    os.fsync(f.fileno())
                tmp_path.replace(self.npy_path)
            except Exception:
                tmp_path.unlink(missing_ok=True)
                raise
            write_json_file(self.keys, self.keys_path)


class Embedder:
    """Embeds texts of one model/dimensionality with a persistent per-kind cache."""

    def __init__(
        self,
        model: str | None = None,
        output_dimensionality: int | None = None,
        cache_dir: Path | None = None,
    ) -> None:
        cfg = section("embedding")
        self.model = model or cfg["model"]
        self.dim = int(output_dimensionality or cfg["output_dimensionality"])
        self.cache_dir = cache_dir if cache_dir is not None else DATA_DIR / cfg["cache_dir"]
        self.batch_size = int(cfg["batch_size"])
        self.save_every_batches = int(cfg["save_every_batches"])
        self.max_retries = int(cfg["max_retries"])
        self.backoff_seconds = float(cfg["retry_backoff_seconds"])
        self.task_types: dict[str, str] = cfg["task_types"]
        self.prompt_templates: dict[str, str] = cfg["prompt_templates"]
        self.uses_prompt_task = self.model.startswith(tuple(cfg["prompt_task_model_prefixes"]))
        self._client: Any = None
        self._stores: dict[str, _VectorStore] = {}

    @classmethod
    def for_config(cls) -> Embedder:
        """Embedder for the model and dimensionality in ``config.json`` ``embedding``."""
        return cls()

    def _store(self, kind: Kind) -> _VectorStore:
        if kind not in self._stores:
            stem = self.cache_dir / f"{self.model}-{self.dim}-{kind}"
            self._stores[kind] = _VectorStore(stem, self.dim)
        return self._stores[kind]

    def _request(self, texts: Sequence[str], kind: Kind) -> list[list[float]]:
        """One embed_content call (no retries); returns one raw vector per text."""
        if self._client is None:
            self._client = genai.Client()
        if self.uses_prompt_task:
            template = self.prompt_templates[kind]
            contents: list[Any] = [
                genai_types.Content(parts=[genai_types.Part(text=template.format(text=t))])
                for t in texts
            ]
            config = genai_types.EmbedContentConfig(output_dimensionality=self.dim)
        else:
            contents = list(texts)
            config = genai_types.EmbedContentConfig(
                task_type=self.task_types[kind], output_dimensionality=self.dim
            )
        response = self._client.models.embed_content(
            model=self.model, contents=contents, config=config
        )
        return [list(e.values) for e in response.embeddings]

    def _embed_batch(self, texts: Sequence[str], kind: Kind, batch_index: int) -> np.ndarray:
        """Embed one batch with retries; returns a float32 L2-normalised ``(n, dim)`` matrix."""
        where = f"model={self.model} kind={kind} batch={batch_index} ({len(texts)} texts)"
        for attempt in range(self.max_retries + 1):
            try:
                raw = self._request(texts, kind)
                break
            except Exception as exc:
                if not _is_retryable(exc) or attempt == self.max_retries:
                    raise RuntimeError(f"embedding request failed: {where}: {exc!r}") from exc
                delay = self.backoff_seconds * 2**attempt
                logger.warning(
                    f"embedding {where} failed ({exc!r}); retry {attempt + 1}/{self.max_retries} "
                    f"in {delay:.1f}s"
                )
                time.sleep(delay)
        vectors = np.asarray(raw, dtype=np.float32)
        if vectors.shape != (len(texts), self.dim):
            raise RuntimeError(
                f"embedding response shape {vectors.shape} != {(len(texts), self.dim)}: {where}"
            )
        return _normalize_rows(vectors)

    def embed_texts(self, texts: Sequence[str], kind: Kind) -> np.ndarray:
        """``(len(texts), dim)`` float32 L2-normalised matrix; blank texts give zero rows."""
        if kind not in self.task_types:
            raise ValueError(f"unknown embedding kind {kind!r}; expected one of {sorted(self.task_types)}")
        store = self._store(kind)
        store.load()
        keys = [text_key(t) for t in texts]
        blank = {key for key, t in zip(keys, texts) if not t.strip()}
        if blank:
            logger.warning(
                f"{sum(1 for t in texts if not t.strip())} empty text(s) get zero vectors (no API call)"
            )
        missing: dict[str, str] = {}
        for key, text in zip(keys, texts):
            if key not in blank and key not in store and key not in missing:
                missing[key] = text
        logger.info(
            f"embed {self.model} kind={kind}: {len(texts)} texts, {len(missing)} to embed, "
            f"{len(texts) - len(missing)} from cache"
        )
        if missing:
            self._embed_missing(store, kind, list(missing), list(missing.values()))
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        wanted = [i for i, key in enumerate(keys) if key not in blank]
        if wanted:
            out[wanted] = _normalize_rows(store.get([keys[i] for i in wanted]))
        return out

    def _embed_missing(
        self, store: _VectorStore, kind: Kind, keys: list[str], texts: list[str]
    ) -> None:
        pending_keys: list[str] = []
        pending_vectors: list[np.ndarray] = []
        try:
            for batch_index, start in enumerate(range(0, len(texts), self.batch_size)):
                chunk = slice(start, start + self.batch_size)
                pending_vectors.append(self._embed_batch(texts[chunk], kind, batch_index))
                pending_keys.extend(keys[chunk])
                if (batch_index + 1) % self.save_every_batches == 0:
                    store.add(pending_keys, np.vstack(pending_vectors))
                    pending_keys, pending_vectors = [], []
                    store.save()
                    logger.info(f"embedded {min(start + self.batch_size, len(texts))}/{len(texts)}")
        finally:
            # Also runs when a batch failed: keep (and pay only once for) what succeeded.
            if pending_vectors:
                store.add(pending_keys, np.vstack(pending_vectors))
            store.save()


@lru_cache(maxsize=1)
def _default_embedder() -> Embedder:
    return Embedder.for_config()


def embed_texts(texts: Sequence[str], kind: Kind) -> np.ndarray:
    """Embed ``texts`` with the configured model (cached); see ``Embedder.embed_texts``."""
    return _default_embedder().embed_texts(texts, kind)
