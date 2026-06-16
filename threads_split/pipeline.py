from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from preprocessing.models import Message
from threads_split.assigner import ThreadAssigner
from threads_split.embedding.embedding import Embedder, load_message_embeddings
from threads_split.models import Thread, ThreadConfig
from utils import write_json_file


def load_messages(input_path: Path) -> list[Message]:
    with input_path.open(encoding="utf-8") as f:
        raw_messages = json.load(f)
    messages = [Message.from_android_dict(item) for item in raw_messages if item['text']]
    messages.sort(key=lambda m: m.datetime)
    return messages


def split_into_threads(
    input_path: Path,
    config: ThreadConfig | None = None,
    embedder: Embedder | None = None,
) -> tuple[list[Message], list[Thread]]:
    config = config or ThreadConfig()
    messages = load_messages(input_path)
    if embedder is None:
        embedder = Embedder(model_name=config.embedding_model)
    passage_embeddings, query_embeddings = load_message_embeddings(
        input_path=input_path,
        model_name=config.embedding_model,
        embedder=embedder,
    )
    assigner = ThreadAssigner(
        messages,
        passage_embeddings,
        query_embeddings=query_embeddings,
        config=config,
        input_path=input_path,
    )
    threads = assigner.process_messages()
    return messages, threads


def threads_to_output(
    threads: list[Thread],
    messages: list[Message],
    source_path: Path,
    config: ThreadConfig,
) -> dict[str, Any]:
    return {
        "threads": [thread.to_dict(messages) for thread in threads],
        "metadata": {
            "source": str(source_path),
            "message_count": len(messages),
            "thread_count": len(threads),
            "config": config.to_dict(),
        },
    }



def run_pipeline(
    input_path: Path | str,
    output_path: Path | str,
    config: ThreadConfig | None = None,
    embedder: Embedder | None = None,
) -> dict[str, Any]:
    config = config or ThreadConfig()
    source_path = Path(input_path)
    output = Path(output_path)
    messages, threads = split_into_threads(source_path, config=config, embedder=embedder)
    payload = threads_to_output(threads, messages, source_path, config)
    write_json_file(payload, output)
    return threads_to_output(threads, messages, source_path, config)


if __name__ == "__main__":
    result = run_pipeline(
        input_path=Path("data/chats_from_phone/chat_android.json"),
        output_path=Path("data/threads.json"),
    )
    print(
        f"Wrote {result['metadata']['thread_count']} threads "
        f"from {result['metadata']['message_count']} messages"
    )