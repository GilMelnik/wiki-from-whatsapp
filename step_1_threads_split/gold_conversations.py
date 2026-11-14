"""The owner's corrected conversations in the gold file, and their score.

The owner corrects the split in the conversations tool; every conversation of the edited file
that step 1's own file does not have (same messages) was created or changed by those edits.
Each one is asserted complete: exactly these messages, no others. ``add_conversations``
adds them to the gold file as an additive part (the windows and the context part stay
exactly as they are)::

    conversations: [{thread_id, start, message_ids: [wa_id, ...], added_at}]
    metadata.conversations: {added_at, original_source, edited_source, count}

``thread_id`` is the edited file's id at the time (for reading only; scores never use it),
``start`` the datetime of its first message. Conversations already in the file are kept as
they are; one that is already there with the same messages is not added twice, and one that
shares messages with a kept conversation but differs from it stops the command (the owner
re-edited a gold conversation; the gold file must be fixed by hand first).

``python -m step_1_threads_split.gold_conversations [--gold PATH] [--original PATH]
[--edited PATH]`` adds the conversations of the current edits (the gold file is backed up
first). ``conversation_counts`` scores a split on them (``step_1_threads_split.measure``):
B-cubed over their messages, where a predicted thread's messages that belong to no gold
conversation count against precision, as the gold conversation is complete.
"""

from __future__ import annotations

import argparse
import json
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from step_1_threads_split.evaluate import _f1, markdown_table
from step_1_threads_split.gold import load_gold
from utils.json_io import write_json_file
from utils.logging_setup import git_commit, setup_step_logging
from utils.paths import EDITED_THREADS_PATH, GOLD_THREADS_PATH, ORIGINAL_THREADS_PATH, STEP_1
from utils.reconcile import backup_file
from utils.threads_io import load_threads
from utils.timestamps import local_now

logger = logging.getLogger(STEP_1)


def changed_conversations(
    original: Sequence[dict[str, Any]], edited: Sequence[dict[str, Any]]
) -> list[dict[str, Any]]:
    """The edited threads whose messages are not exactly those of an original thread.

    Returns ``[{thread_id, start, message_ids}]`` in time order (message ids in the edited
    thread's order).
    """
    kept = {frozenset(m["id"] for m in t["messages"]) for t in original}
    changed = [
        as_conversation(thread)
        for thread in edited
        if frozenset(m["id"] for m in thread["messages"]) not in kept
    ]
    return sorted(changed, key=lambda c: (c["start"], c["thread_id"]))


def as_conversation(thread: dict[str, Any]) -> dict[str, Any]:
    """``{thread_id, start, message_ids}`` of a C1 thread."""
    return {
        "thread_id": thread["thread_id"],
        "start": min(m["datetime"] for m in thread["messages"]),
        "message_ids": [m["id"] for m in thread["messages"]],
    }


def add_conversations(
    gold: dict[str, Any],
    conversations: Sequence[dict[str, Any]],
    *,
    original_source: str,
    edited_source: str,
) -> tuple[dict[str, Any], int]:
    """A copy of ``gold`` with ``conversations`` added after the kept ones; and how many were new.

    Raises when a new conversation shares messages with a kept one but is not the same.
    """
    kept: list[dict[str, Any]] = list(gold.get("conversations", []))
    owner = {wa_id: index for index, c in enumerate(kept) for wa_id in c["message_ids"]}
    added_at = local_now()
    fresh = []
    for conversation in conversations:
        ids = set(conversation["message_ids"])
        clashing = {owner[wa_id] for wa_id in ids if wa_id in owner}
        if clashing == set():
            fresh.append({**conversation, "added_at": added_at})
            continue
        if len(clashing) == 1 and set(kept[clashing.pop()]["message_ids"]) == ids:
            continue
        raise ValueError(
            f"conversation {conversation['thread_id']} (starts {conversation['start']}) shares "
            "messages with a conversation already in the gold file but is not the same; the "
            "owner re-edited a gold conversation: remove the old one from the gold file first"
        )
    metadata = {
        **gold["metadata"],
        "conversations": {
            "added_at": added_at,
            "original_source": original_source,
            "edited_source": edited_source,
            "count": len(kept) + len(fresh),
        },
    }
    return {**gold, "conversations": [*kept, *fresh], "metadata": metadata}, len(fresh)


def add_conversations_to_file(
    gold_path: Path, original_path: Path, edited_path: Path
) -> tuple[dict[str, Any], int]:
    """``add_conversations`` on the gold file: back it up, write the result, check it."""
    found = changed_conversations(
        load_threads(original_path)["threads"], load_threads(edited_path)["threads"]
    )
    updated, added = add_conversations(
        load_gold(gold_path),
        found,
        original_source=str(original_path),
        edited_source=str(edited_path),
    )
    if not added:
        logger.info(f"{gold_path}: all {len(found)} changed conversations are already there")
        return updated, 0
    backup = backup_file(gold_path)
    write_json_file(updated, gold_path)
    load_gold(gold_path)
    logger.info(f"added {added} conversations to {gold_path} (previous file: {backup})")
    return updated, added


def conversation_counts(
    pred_threads: Sequence[dict[str, Any]], conversations: Sequence[dict[str, Any]]
) -> list[tuple[float, float, int]]:
    """Per gold conversation: B-cubed precision sum, recall sum and message count.

    A message's predicted thread is all of its messages (also those outside the gold
    conversations); a gold message the prediction lacks is a thread of its own. Pool the
    rows with ``pooled_bcubed``.
    """
    thread_of = {m["id"]: i for i, t in enumerate(pred_threads) for m in t["messages"]}
    members: dict[Any, set[str]] = {}
    for wa_id, index in thread_of.items():
        members.setdefault(index, set()).add(wa_id)
    rows = []
    for conversation in conversations:
        gold_ids = set(conversation["message_ids"])
        precision = recall = 0.0
        for wa_id in gold_ids:
            predicted = members[thread_of[wa_id]] if wa_id in thread_of else {wa_id}
            shared = len(predicted & gold_ids)
            precision += shared / len(predicted)
            recall += shared / len(gold_ids)
        rows.append((precision, recall, len(gold_ids)))
    return rows


def pooled_bcubed(rows: Sequence[tuple[float, float, int]]) -> dict[str, float]:
    """Micro B-cubed precision, recall and F1 of ``conversation_counts`` rows."""
    messages = sum(n for _, _, n in rows)
    if not messages:
        return {"bcubed_precision": 1.0, "bcubed_recall": 1.0, "bcubed_f1": 1.0, "messages": 0}
    precision = sum(p for p, _, _ in rows) / messages
    recall = sum(r for _, r, _ in rows) / messages
    return {"bcubed_precision": precision, "bcubed_recall": recall,
            "bcubed_f1": _f1(precision, recall), "messages": messages}  # fmt: skip


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m step_1_threads_split.gold_conversations",
        description="Add the conversations created or changed by the owner's edits to the "
        "gold file (backed up first).",
    )
    parser.add_argument("--gold", type=Path, default=GOLD_THREADS_PATH)
    parser.add_argument("--original", type=Path, default=ORIGINAL_THREADS_PATH)
    parser.add_argument("--edited", type=Path, default=EDITED_THREADS_PATH)
    args = parser.parse_args(argv)
    setup_step_logging(STEP_1)
    logger.info(f"gold_conversations | git commit {git_commit()} | args {json.dumps(vars(args), default=str)}")
    gold, added = add_conversations_to_file(args.gold, args.original, args.edited)
    conversations = gold["conversations"]
    table = markdown_table(
        ["conversations", "new now", "messages", "largest"],
        [[len(conversations), added, sum(len(c["message_ids"]) for c in conversations),
          max((len(c["message_ids"]) for c in conversations), default=0)]],  # fmt: skip
    )
    logger.info(f"gold conversations\n{table}")


if __name__ == "__main__":
    main()
