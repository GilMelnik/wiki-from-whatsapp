"""The wider gold score ("window + context") and the edges of what the owner saw.

In each gold window the thread tagger showed whole threads, so the owner also tagged the
messages of those threads that lie outside the window. ``add_context`` adds them to the gold
file as an additive part (the window part and its score stay exactly as frozen)::

    windows[i].context_message_ids: [wa_id, ...]   # shown outside window i, in time order
    context_messages: {wa_id: cluster label}
    metadata.context: {added_at, tagged_source, shown_sources, message_count}

What the owner saw for a window ("shown") is every message of every thread that has a message
inside the window, in any of the given thread files (the split the tagger started from and
the tagged file). A context message gets the label of its tagged thread: the label the gold
already gives that thread inside the window, or else the id of its first shown message.

The bias of this score: which outside messages were shown was decided by the old splitter, so
whatever it missed outside the windows is invisible here.

**Edges.** The owner saw only what a window showed; a real thread may begin before or go on
after it. A gold thread is *cut by an edge* when the chat message right before its first shown
message, or right after its last one, was not shown in that window: the owner could not see
whether the conversation went on there. ``edge_cut_labels`` names these threads so that
disagreements involving them can be told apart from plain errors.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from step_1_threads_split.gold import Window, load_gold, message_moment
from utils.json_io import write_json_file
from utils.reconcile import backup_file
from utils.threads_io import load_threads
from utils.timestamps import local_now

logger = logging.getLogger(__name__)


def shown_ids(window: Window, *thread_files: Sequence[dict[str, Any]]) -> set[str]:
    """Every message of every thread (in any file) that has a message inside ``window``."""
    shown: set[str] = set()
    for threads in thread_files:
        for thread in threads:
            if any(window.contains(message_moment(m)) for m in thread["messages"]):
                shown.update(m["id"] for m in thread["messages"])
    return shown


def add_context(
    gold: dict[str, Any],
    tagged: Sequence[dict[str, Any]],
    others: Sequence[Sequence[dict[str, Any]]],
    *,
    tagged_source: str,
    shown_sources: Sequence[str],
) -> dict[str, Any]:
    """A copy of ``gold`` with the context part built from the ``tagged`` threads.

    ``others`` are the further thread files whose threads were shown (the split the tagger
    started from). Raises when the tagged file does not agree with the frozen labels (it is
    then not the file the gold was frozen from) or when a message is shown in two windows.
    """
    tagged_of = {m["id"]: t["thread_id"] for t in tagged for m in t["messages"]}
    moments = {m["id"]: message_moment(m) for t in tagged for m in t["messages"]}
    labels: dict[str, str] = gold["messages"]
    thread_label: dict[str, str] = {}
    for wa_id, label in labels.items():
        if wa_id not in tagged_of:
            raise ValueError(f"gold message {wa_id} is not in the tagged threads")
        known = thread_label.setdefault(tagged_of[wa_id], label)
        if known != label:
            raise ValueError(
                f"tagged thread {tagged_of[wa_id]!r} holds gold messages of two clusters "
                f"({known}, {label}); is this the file the gold was frozen from?"
            )
    windows: list[dict[str, Any]] = []
    context: dict[str, str] = {}
    for raw in gold["windows"]:
        window = Window.from_dict(raw)
        outside = shown_ids(window, tagged, *others) - set(raw["message_ids"])
        missing = sorted(wa_id for wa_id in outside if wa_id not in tagged_of)
        if missing:
            raise ValueError(f"{len(missing)} shown messages are not tagged, e.g. {missing[:3]}")
        ordered = sorted(outside, key=lambda wa_id: (moments[wa_id], wa_id))
        for wa_id in ordered:
            if wa_id in context:
                raise ValueError(f"message {wa_id} was shown in two gold windows")
            context[wa_id] = thread_label.setdefault(tagged_of[wa_id], wa_id)
        windows.append({**raw, "context_message_ids": ordered})
    metadata = {
        **gold["metadata"],
        "context": {
            "added_at": local_now(),
            "tagged_source": tagged_source,
            "shown_sources": list(shown_sources),
            "message_count": len(context),
        },
    }
    return {**gold, "windows": windows, "context_messages": context, "metadata": metadata}


def add_context_to_file(gold_path: Path, tagged_path: Path, shown_paths: Sequence[Path]) -> dict[str, Any]:
    """``add_context`` on the gold file: back it up, write the result, check it, return it."""
    updated = add_context(
        load_gold(gold_path),
        load_threads(tagged_path)["threads"],
        [load_threads(path)["threads"] for path in shown_paths],
        tagged_source=str(tagged_path),
        shown_sources=[str(path) for path in shown_paths],
    )
    backup = backup_file(gold_path)
    write_json_file(updated, gold_path)
    load_gold(gold_path)
    logger.info(
        f"added {updated['metadata']['context']['message_count']} context messages to "
        f"{gold_path} (previous file: {backup})"
    )
    return updated


def edge_cut_labels(gold: dict[str, Any], chat_order: Sequence[str]) -> list[set[str]]:
    """Per gold window, the labels of the gold threads cut by an edge of what was shown.

    ``chat_order`` is every message id of the chat in time order (the messages step 1 keeps).
    Needs the context part: without it every thread that leaves its window would count as
    cut, which says nothing about what the owner saw.
    """
    if "context_messages" not in gold:
        raise ValueError("the gold file has no context part; run --add-context first")
    position = {wa_id: index for index, wa_id in enumerate(chat_order)}
    labels = {**gold["messages"], **gold["context_messages"]}
    result: list[set[str]] = []
    for window in gold["windows"]:
        shown = [*window["message_ids"], *window["context_message_ids"]]
        shown_set = set(shown)
        spans: dict[str, list[int]] = {}
        for wa_id in shown:
            spans.setdefault(labels[wa_id], []).append(position[wa_id])
        cut = set()
        for label, places in spans.items():
            before, after = min(places) - 1, max(places) + 1
            if _unshown(chat_order, before, shown_set) or _unshown(chat_order, after, shown_set):
                cut.add(label)
        result.append(cut)
    return result


def _unshown(chat_order: Sequence[str], index: int, shown: set[str]) -> bool:
    return 0 <= index < len(chat_order) and chat_order[index] not in shown


def scored_ids(gold: dict[str, Any], window: dict[str, Any], with_context: bool) -> list[str]:
    """The ids a window scores: its own messages, plus its context when asked."""
    if not with_context:
        return list(window["message_ids"])
    if "context_message_ids" not in window:
        raise ValueError("the gold file has no context part; run --add-context first")
    return [*window["message_ids"], *window["context_message_ids"]]


def all_labels(gold: dict[str, Any]) -> dict[str, str]:
    return {**gold["messages"], **gold.get("context_messages", {})}


def without_labels(gold: dict[str, Any], dropped: Iterable[set[str]]) -> dict[str, Any]:
    """``gold`` without the messages of the dropped labels (one set per window)."""
    labels = all_labels(gold)
    windows = []
    for window, drop in zip(gold["windows"], dropped):
        keep = {**window}
        for key in ("message_ids", "context_message_ids"):
            if key in window:
                keep[key] = [wa_id for wa_id in window[key] if labels[wa_id] not in drop]
        windows.append(keep)
    return {**gold, "windows": windows}
