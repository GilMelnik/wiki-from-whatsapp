"""Cached E5 query/passage embeddings for step-5 claim aggregation.

The all-claims embeddings are expensive to recompute, so they are cached under
``data/`` keyed by the claims source + model + claim ids, and rebuilt only when
that fingerprint changes. Also holds the small claim text/id normalization
helpers shared by the clustering and orchestration modules.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from utils.json_io import write_json_file
from utils.paths import (
    CLAIM_PASSAGE_EMBEDDINGS_PATH,
    CLAIM_QUERY_EMBEDDINGS_PATH,
)

DEFAULT_EMBEDDING_MODEL = "intfloat/multilingual-e5-large"


def _normalize(text: str) -> str:
    return " ".join(text.split()).strip()


def _claim_ids(claims: list[dict[str, Any]]) -> list[str]:
    return [c["claim_id"] for c in claims]


def _claim_texts(claims: list[dict[str, Any]]) -> list[str]:
    return [_normalize(c["claim_text"]) for c in claims]


def _claim_embeddings_need_rebuild(
    path: Path,
    *,
    source_path: Path,
    model_name: str,
    claims: list[dict[str, Any]],
    embedding_kind: str,
) -> bool:
    from step_1_threads_split.embedding.embedding import (
        MessageEmbeddings,
        _embedding_kind,
        _source_matches,
    )

    if not path.exists():
        return True
    meta = MessageEmbeddings.load(path).metadata
    if _embedding_kind(meta, default=embedding_kind) != embedding_kind:
        return True
    if meta.get("embedding_model") != model_name:
        return True
    if meta.get("claim_count") != len(claims):
        return True
    if meta.get("claim_ids") != _claim_ids(claims):
        return True
    return not _source_matches(meta, source_path)


def _write_claim_embeddings(
    output_path: Path,
    vectors: list[np.ndarray],
    *,
    source_path: Path,
    model_name: str,
    embedding_dim: int,
    embedding_kind: str,
    claims: list[dict[str, Any]],
    companion_path: Path | None = None,
) -> None:
    metadata: dict[str, Any] = {
        "source": str(source_path.resolve()),
        "claim_count": len(claims),
        "claim_ids": _claim_ids(claims),
        "embedding_model": model_name,
        "embedding_dim": embedding_dim,
        "embedding_kind": embedding_kind,
    }
    if companion_path is not None:
        metadata["companion_path"] = str(companion_path)
    write_json_file(
        {"metadata": metadata, "embeddings": [v.tolist() for v in vectors]},
        output_path,
    )


def ensure_claim_embeddings(
    claims: list[dict[str, Any]],
    texts: list[str],
    source_path: Path | str,
    *,
    query_path: Path | str = CLAIM_QUERY_EMBEDDINGS_PATH,
    passage_path: Path | str = CLAIM_PASSAGE_EMBEDDINGS_PATH,
    model_name: str = DEFAULT_EMBEDDING_MODEL,
) -> tuple[list[np.ndarray], list[np.ndarray]]:
    """Build or load cached query/passage embeddings for all claims."""

    from step_1_threads_split.embedding.embedding import MessageEmbeddings

    source = Path(source_path).resolve()
    query_output = Path(query_path)
    passage_output = Path(passage_path)

    rebuild_passage = _claim_embeddings_need_rebuild(
        passage_output,
        source_path=source,
        model_name=model_name,
        claims=claims,
        embedding_kind="passage",
    )
    rebuild_query = _claim_embeddings_need_rebuild(
        query_output,
        source_path=source,
        model_name=model_name,
        claims=claims,
        embedding_kind="query",
    )

    embedder = None
    if rebuild_passage or rebuild_query:
        from step_1_threads_split.embedding.embedding import Embedder

        embedder = Embedder(model_name=model_name)

    if rebuild_passage:
        assert embedder is not None
        passage_vectors = embedder.encode_messages(texts)
        _write_claim_embeddings(
            passage_output,
            passage_vectors,
            source_path=source,
            model_name=embedder.model_name,
            embedding_dim=embedder.embedding_dim,
            embedding_kind="passage",
            claims=claims,
            companion_path=query_output,
        )
    else:
        passage_vectors = MessageEmbeddings.load(passage_output).as_list()

    if rebuild_query:
        assert embedder is not None
        query_vectors = embedder.encode_queries(texts)
        _write_claim_embeddings(
            query_output,
            query_vectors,
            source_path=source,
            model_name=embedder.model_name,
            embedding_dim=embedder.embedding_dim,
            embedding_kind="query",
            claims=claims,
            companion_path=passage_output,
        )
    else:
        query_vectors = MessageEmbeddings.load(query_output).as_list()

    claim_count = len(claims)
    if len(passage_vectors) != claim_count or len(query_vectors) != claim_count:
        raise ValueError(
            f"Embedding count mismatch for {source}: "
            f"claims={claim_count}, passage={len(passage_vectors)}, query={len(query_vectors)}"
        )
    return query_vectors, passage_vectors
