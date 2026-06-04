from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from preprocessing.models import Message
from threads_split.assigner import ThreadAssigner
from threads_split.embedding import Embedder
from threads_split.models import Thread, ThreadConfig


def load_messages(input_path: Path) -> list[Message]:
    with input_path.open(encoding="utf-8") as f:
        raw_messages = json.load(f)
    messages = []
    for source_index, item in enumerate(raw_messages):
        message = Message.from_dict(item)
        message.source_index = source_index  # type: ignore[attr-defined]
        messages.append(message)
    messages.sort(key=lambda m: m.datetime)
    return messages


def split_into_threads(
    messages: list[Message],
    config: ThreadConfig | None = None,
    embedder: Embedder | None = None,
) -> list[Thread]:
    config = config or ThreadConfig()
    embedder = embedder or Embedder(model_name=config.embedding_model)
    texts = [message.content for message in messages]
    embeddings = embedder.encode_messages(texts)
    assigner = ThreadAssigner(config)
    return assigner.process_messages(messages, embeddings)


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


def write_threads_json(
    threads: list[Thread],
    messages: list[Message],
    output_path: Path,
    source_path: Path,
    config: ThreadConfig,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = threads_to_output(threads, messages, source_path, config)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def run_pipeline(
    input_path: Path,
    output_path: Path,
    config: ThreadConfig | None = None,
) -> dict[str, Any]:
    config = config or ThreadConfig()
    messages = load_messages(input_path)
    threads = split_into_threads(messages, config=config)
    write_threads_json(threads, messages, output_path, input_path, config)
    return threads_to_output(threads, messages, input_path, config)
