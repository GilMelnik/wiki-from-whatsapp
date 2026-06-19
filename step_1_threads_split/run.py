from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from step_0_preprocessing.models import Message
from step_1_threads_split.assigner import ThreadAssigner
from step_1_threads_split.embedding.embedding import Embedder, load_message_embeddings
from step_1_threads_split.models import Thread, ThreadConfig
from utils.json_io import write_json_file


def sort_messages_quote_aware(messages: list[Message]) -> list[Message]:
    """Sort by datetime, then ensure every quoting message follows the message it quotes.

    Timestamps have only minute precision, so a quoting message and its quoted message
    can share a timestamp and be ordered arbitrarily by a plain sort. This stable
    topological pass keeps datetime order while moving a quoting message after its quoted
    message whenever it would otherwise precede it.
    """
    ordered = sorted(messages, key=lambda m: m.datetime)

    occurrences: dict[tuple[str, str], list[tuple[datetime, int]]] = {}
    for position, message in enumerate(ordered):
        key = (message.sender, message.normalized_content())
        occurrences.setdefault(key, []).append((message.datetime, position))

    quoted_by: dict[int, int] = {}
    for position, message in enumerate(ordered):
        key = message.quote_lookup_key()
        if key is None:
            continue
        candidates = [
            (occurred_at, candidate_pos)
            for occurred_at, candidate_pos in occurrences.get(key, [])
            if candidate_pos != position and occurred_at <= message.datetime
        ]
        if candidates:
            quoted_by[position] = max(candidates, key=lambda item: (item[0], item[1]))[1]

    state = [0] * len(ordered)  # 0=unvisited, 1=on stack, 2=emitted
    result: list[Message] = []
    for start in range(len(ordered)):
        if state[start] == 2:
            continue
        stack = [start]
        while stack:
            node = stack[-1]
            if state[node] == 2:
                stack.pop()
                continue
            if state[node] == 0:
                state[node] = 1
                quoted_pos = quoted_by.get(node)
                if quoted_pos is not None and state[quoted_pos] == 0:
                    stack.append(quoted_pos)
                    continue
            state[node] = 2
            result.append(ordered[node])
            stack.pop()

    return result


def load_messages(input_path: Path) -> list[Message]:
    with input_path.open(encoding="utf-8") as f:
        raw_messages = json.load(f)
    messages = [Message.from_android_dict(item) for item in raw_messages]
    messages = [message for message in messages if message.normalized_content()]
    return sort_messages_quote_aware(messages)


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
