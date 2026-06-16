from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from threads_split.embedding.embedding import (
    DEFAULT_PASSAGE_EMBEDDINGS_PATH,
    DEFAULT_QUERY_EMBEDDINGS_PATH,
    Embedder,
    MessageEmbeddings,
    _embeddings_need_rebuild,
)
from threads_split.pipeline import load_messages
from utils import write_json_file


def _build_payload(
    source_path: Path,
    embeddings: list[list[float]],
    *,
    model_name: str,
    embedding_dim: int,
    embedding_kind: str,
    companion_path: Path | None = None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "source": str(source_path),
        "message_count": len(embeddings),
        "embedding_model": model_name,
        "embedding_dim": embedding_dim,
        "embedding_kind": embedding_kind,
    }
    if companion_path is not None:
        metadata["companion_path"] = str(companion_path)
    return {
        "metadata": metadata,
        "embeddings": embeddings,
    }


def _write_embeddings(
    output_path: Path,
    vectors: list[np.ndarray],
    *,
    source_path: Path,
    model_name: str,
    embedding_dim: int,
    embedding_kind: str,
    companion_path: Path | None = None,
) -> None:
    payload = _build_payload(
        source_path=source_path,
        embeddings=[vector.tolist() for vector in vectors],
        model_name=model_name,
        embedding_dim=embedding_dim,
        embedding_kind=embedding_kind,
        companion_path=companion_path,
    )
    write_json_file(payload, output_path)


def ensure_embeddings(
    input_path: Path | str,
    passage_path: Path | str = DEFAULT_PASSAGE_EMBEDDINGS_PATH,
    query_path: Path | str = DEFAULT_QUERY_EMBEDDINGS_PATH,
    model_name: str = "intfloat/multilingual-e5-large",
    batch_size: int = 64,
    max_messages: int | None = None,
    embedder: Embedder | None = None,
) -> tuple[list[np.ndarray], list[np.ndarray]]:
    """Build missing passage/query caches and return both embedding lists."""
    source_path = Path(input_path).resolve()
    passage_output = Path(passage_path)
    query_output = Path(query_path)
    embedder = embedder or Embedder(model_name=model_name, batch_size=batch_size)

    messages = load_messages(source_path)
    if max_messages is not None:
        messages = messages[:max_messages]
    texts = [message.content for message in messages]
    message_count = len(messages)

    rebuild_passage = _embeddings_need_rebuild(
        passage_output,
        input_path=source_path,
        model_name=model_name,
        message_count=message_count,
        embedding_kind="passage",
    )
    rebuild_query = _embeddings_need_rebuild(
        query_output,
        input_path=source_path,
        model_name=model_name,
        message_count=message_count,
        embedding_kind="query",
    )

    if rebuild_passage:
        passage_vectors = embedder.encode_messages(texts)
        _write_embeddings(
            passage_output,
            passage_vectors,
            source_path=source_path,
            model_name=embedder.model_name,
            embedding_dim=embedder.embedding_dim,
            embedding_kind="passage",
            companion_path=query_output,
        )
    else:
        passage_vectors = MessageEmbeddings.load(passage_output).as_list()

    if rebuild_query:
        query_vectors = embedder.encode_queries(texts)
        _write_embeddings(
            query_output,
            query_vectors,
            source_path=source_path,
            model_name=embedder.model_name,
            embedding_dim=embedder.embedding_dim,
            embedding_kind="query",
            companion_path=passage_output,
        )
    else:
        query_vectors = MessageEmbeddings.load(query_output).as_list()

    if len(passage_vectors) != message_count or len(query_vectors) != message_count:
        raise ValueError(
            f"Embedding count mismatch for {source_path}: "
            f"messages={message_count}, passage={len(passage_vectors)}, query={len(query_vectors)}"
        )

    return passage_vectors, query_vectors


def run(
    input_path: Path | str = Path("data/chats_from_phone/chat_android.json"),
    output_path: Path | str = DEFAULT_PASSAGE_EMBEDDINGS_PATH,
    query_output_path: Path | str = DEFAULT_QUERY_EMBEDDINGS_PATH,
    model_name: str = "intfloat/multilingual-e5-large",
    batch_size: int = 64,
    max_messages: int | None = None,
    embedder: Embedder | None = None,
) -> dict[str, Any]:
    passage_vectors, query_vectors = ensure_embeddings(
        input_path=input_path,
        passage_path=output_path,
        query_path=query_output_path,
        model_name=model_name,
        batch_size=batch_size,
        max_messages=max_messages,
        embedder=embedder,
    )
    passage_output = Path(output_path)
    query_output = Path(query_output_path)
    passage_meta = MessageEmbeddings.load(passage_output).metadata
    query_meta = MessageEmbeddings.load(query_output).metadata

    return {
        "metadata": passage_meta,
        "query_metadata": query_meta,
        "output_path": str(passage_output),
        "query_output_path": str(query_output),
        "message_count": len(passage_vectors),
        "query_message_count": len(query_vectors),
    }


if __name__ == "__main__":
    result = run(max_messages=None, batch_size=64)
    metadata = result["metadata"]
    query_metadata = result["query_metadata"]
    print(
        f"Wrote passage embeddings to {result['output_path']} "
        f"({metadata['message_count']} messages, "
        f"dim={metadata['embedding_dim']}, "
        f"model={metadata['embedding_model']})"
    )
    print(
        f"Wrote query embeddings to {result['query_output_path']} "
        f"({query_metadata['message_count']} messages, "
        f"dim={query_metadata['embedding_dim']}, "
        f"model={query_metadata['embedding_model']})"
    )
