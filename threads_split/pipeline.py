from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

from preprocessing.models import Message
from threads_split.assigner import ThreadAssigner
from threads_split.embedding import Embedder
from threads_split.models import Thread, ThreadConfig


def _normalize_input_paths(input_paths: Path | str | Sequence[Path | str]) -> list[Path]:
    if isinstance(input_paths, (str, Path)):
        return [Path(input_paths)]
    return [Path(path) for path in input_paths]


def load_messages_from_file(input_path: Path) -> list[Message]:
    with input_path.open(encoding="utf-8") as f:
        raw_messages = json.load(f)
    messages = []
    for source_index, item in enumerate(raw_messages):
        message = Message.from_dict(item)
        message.source_file = str(input_path)  # type: ignore[attr-defined]
        message.source_index = source_index  # type: ignore[attr-defined]
        messages.append(message)
    messages.sort(key=lambda m: m.datetime)
    return messages


def load_messages_stream(input_paths: Sequence[Path]) -> list[Message]:
    messages: list[Message] = []
    for input_path in input_paths:
        messages.extend(load_messages_from_file(input_path))
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
    return assigner.process_messages(
        messages,
        embeddings,
        batch_start=0,
        batch_end=len(messages),
        reset=True,
        finalize=True,
    )


def split_into_threads_stream(
    input_paths: Sequence[Path],
    config: ThreadConfig | None = None,
    embedder: Embedder | None = None,
) -> tuple[list[Message], list[Thread]]:
    config = config or ThreadConfig()
    embedder = embedder or Embedder(model_name=config.embedding_model)
    assigner = ThreadAssigner(config)

    all_messages: list[Message] = []
    all_embeddings: list[Any] = []
    threads: list[Thread] = []

    normalized_paths = list(input_paths)
    for file_index, input_path in enumerate(normalized_paths):
        batch_messages = load_messages_from_file(input_path)
        batch_embeddings = embedder.encode_messages([message.content for message in batch_messages])

        batch_start = len(all_messages)
        all_messages.extend(batch_messages)
        all_embeddings.extend(batch_embeddings)
        batch_end = len(all_messages)

        batch_threads = assigner.process_messages(
            all_messages,
            all_embeddings,
            batch_start=batch_start,
            batch_end=batch_end,
            reset=file_index == 0,
            finalize=file_index == len(normalized_paths) - 1,
        )
        if batch_threads:
            threads = batch_threads

    return all_messages, threads


def threads_to_output(
    threads: list[Thread],
    messages: list[Message],
    source_paths: Sequence[Path],
    config: ThreadConfig,
) -> dict[str, Any]:
    return {
        "threads": [thread.to_dict(messages) for thread in threads],
        "metadata": {
            "sources": [str(path) for path in source_paths],
            "message_count": len(messages),
            "thread_count": len(threads),
            "config": config.to_dict(),
        },
    }


def write_threads_json(
    threads: list[Thread],
    messages: list[Message],
    output_path: Path,
    source_paths: Sequence[Path],
    config: ThreadConfig,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = threads_to_output(threads, messages, source_paths, config)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def run_pipeline(
    input_paths: Path | str | Sequence[Path | str],
    output_path: Path | str,
    config: ThreadConfig | None = None,
    embedder: Embedder | None = None,
) -> dict[str, Any]:
    config = config or ThreadConfig()
    normalized_paths = _normalize_input_paths(input_paths)
    output = Path(output_path)
    messages, threads = split_into_threads_stream(normalized_paths, config=config, embedder=embedder)
    write_threads_json(threads, messages, output, normalized_paths, config)
    return threads_to_output(threads, messages, normalized_paths, config)
