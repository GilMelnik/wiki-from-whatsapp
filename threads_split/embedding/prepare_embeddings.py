from __future__ import annotations

from pathlib import Path
from typing import Any

from threads_split.embedding.embedding import Embedder
from threads_split.pipeline import load_messages
from utils import write_json_file


def _build_payload(
    source_path: Path,
    embeddings: list[list[float]],
    model_name: str,
    embedding_dim: int,
) -> dict[str, Any]:
    return {
        "metadata": {
            "source": str(source_path),
            "message_count": len(embeddings),
            "embedding_model": model_name,
            "embedding_dim": embedding_dim,
        },
        "embeddings": embeddings,
    }


def run(
    input_path: Path | str = Path("data/chats_from_phone/chat_android.json"),
    output_path: Path | str = Path("data/message_embeddings.json"),
    model_name: str = "intfloat/multilingual-e5-large",
    batch_size: int = 64,
    max_messages: int | None = None,
    embedder: Embedder | None = None,
) -> dict[str, Any]:
    source_path = Path(input_path)
    output = Path(output_path)
    embedder = embedder or Embedder(model_name=model_name, batch_size=batch_size)

    messages = load_messages(source_path)
    if max_messages is not None:
        messages = messages[:max_messages]

    vectors = embedder.encode_messages([message.content for message in messages])
    serialized = [vector.tolist() for vector in vectors]
    payload = _build_payload(
        source_path=source_path,
        embeddings=serialized,
        model_name=embedder.model_name,
        embedding_dim=embedder.embedding_dim,
    )
    write_json_file(payload, output)

    return {
        "metadata": payload["metadata"],
        "output_path": str(output),
    }


if __name__ == "__main__":
    result = run(max_messages=None, batch_size=64)
    metadata = result["metadata"]
    print(
        f"Wrote message embeddings to {result['output_path']} "
        f"({metadata['message_count']} messages, "
        f"dim={metadata['embedding_dim']}, "
        f"model={metadata['embedding_model']})"
    )
