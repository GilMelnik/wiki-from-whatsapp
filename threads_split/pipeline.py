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
    messages = [Message.from_dict(item) for item in raw_messages]
    messages.sort(key=lambda m: m.datetime)
    return messages


def split_into_threads(
    input_path: Path,
    config: ThreadConfig | None = None,
    embedder: Embedder | None = None,
) -> tuple[list[Message], list[Thread]]:
    config = config or ThreadConfig()
    embedder = embedder or Embedder(model_name=config.embedding_model)
    messages = load_messages(input_path)
    embeddings = embedder.encode_messages([message.content for message in messages])
    assigner = ThreadAssigner(messages, embeddings, config)
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
    input_path: Path | str,
    output_path: Path | str,
    config: ThreadConfig | None = None,
    embedder: Embedder | None = None,
) -> dict[str, Any]:
    config = config or ThreadConfig()
    source_path = Path(input_path)
    output = Path(output_path)
    messages, threads = split_into_threads(source_path, config=config, embedder=embedder)
    write_threads_json(threads, messages, output, source_path, config)
    return threads_to_output(threads, messages, source_path, config)
