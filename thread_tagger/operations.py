"""Recompute thread stats and structural edit operations."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any

from wiki_build.taxonomy import page_ids


def _participants_from_messages(messages: list[dict[str, Any]]) -> set[str]:
    participants: set[str] = set()
    for message in messages:
        sender = message.get("sender")
        if sender:
            participants.add(sender)
        for reaction in message.get("reactions") or []:
            for reaction_sender in reaction.get("senders") or []:
                participants.add(reaction_sender)
    return participants


def recompute_thread_stats(thread: dict[str, Any]) -> dict[str, Any]:
    """Update derived fields on a thread dict in place and return it."""
    messages = thread.get("messages") or []
    if not messages:
        thread.update(
            {
                "participants": [],
                "message_ids": [],
                "num_messages": 0,
                "num_unique_senders": 0,
                "start_time": thread.get("start_time", ""),
                "last_time": thread.get("last_time", ""),
                "last_sender": "",
            }
        )
        return thread

    sorted_messages = sorted(messages, key=lambda m: m.get("datetime", ""))
    thread["messages"] = sorted_messages
    participants = sorted(_participants_from_messages(sorted_messages))
    message_ids = thread.get("message_ids") or []
    if len(message_ids) != len(sorted_messages):
        message_ids = list(range(len(sorted_messages)))

    thread.update(
        {
            "participants": participants,
            "message_ids": message_ids,
            "num_messages": len(sorted_messages),
            "num_unique_senders": len(participants),
            "start_time": sorted_messages[0].get("datetime", ""),
            "last_time": sorted_messages[-1].get("datetime", ""),
            "last_sender": sorted_messages[-1].get("sender", ""),
        }
    )
    return thread


def classification_from_thread(
    thread: dict[str, Any],
    base: dict[str, Any] | None = None,
) -> dict[str, Any]:
    record = deepcopy(base) if base else {}
    record.update(
        {
            "thread_id": thread["thread_id"],
            "start_time": thread["start_time"],
            "last_time": thread["last_time"],
            "num_messages": thread["num_messages"],
            "num_unique_senders": thread["num_unique_senders"],
        }
    )
    return record


def update_emergent_tags(record: dict[str, Any]) -> dict[str, Any]:
    known = set(page_ids())
    raw_tags = record.get("topic_tags") or []
    topic_tags = [t for t in raw_tags if isinstance(t, str)]
    record["topic_tags"] = topic_tags
    record["emergent_tags"] = [t for t in topic_tags if t not in known]
    return record


def patch_classification(
    record: dict[str, Any],
    *,
    is_knowledge_bearing: bool | None = None,
    topic_tags: list[str] | None = None,
    entities: list[str] | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    if is_knowledge_bearing is not None:
        record["is_knowledge_bearing"] = is_knowledge_bearing
    if topic_tags is not None:
        record["topic_tags"] = topic_tags
    if entities is not None:
        record["entities"] = entities
    if reason is not None:
        record["reason"] = reason
    return update_emergent_tags(record)


def merge_threads(
    threads: list[dict[str, Any]],
    classifications: dict[str, dict[str, Any]],
    thread_ids: list[str],
    *,
    survivor_id: str | None = None,
    inherit_classification: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], str]:
    if len(thread_ids) < 2:
        raise ValueError("merge requires at least two thread ids")

    by_id = {t["thread_id"]: t for t in threads}
    missing = [tid for tid in thread_ids if tid not in by_id]
    if missing:
        raise ValueError(f"unknown thread ids: {missing}")

    survivor_id = survivor_id or min(thread_ids)
    if survivor_id not in thread_ids:
        raise ValueError("survivor_id must be one of thread_ids")

    all_messages: list[dict[str, Any]] = []
    all_message_ids: list[int] = []
    for tid in thread_ids:
        thread = by_id[tid]
        all_messages.extend(thread.get("messages") or [])
        all_message_ids.extend(thread.get("message_ids") or [])

    sorted_pairs = sorted(
        zip(all_messages, all_message_ids, strict=False)
        if len(all_message_ids) == len(all_messages)
        else [(m, i) for i, m in enumerate(all_messages)],
        key=lambda pair: pair[0].get("datetime", ""),
    )
    merged_messages = [m for m, _ in sorted_pairs]
    merged_ids = [mid for _, mid in sorted_pairs]

    survivor = deepcopy(by_id[survivor_id])
    survivor["messages"] = merged_messages
    survivor["message_ids"] = merged_ids
    recompute_thread_stats(survivor)

    remove = set(thread_ids) - {survivor_id}
    new_threads = [t for t in threads if t["thread_id"] not in remove]
    for i, t in enumerate(new_threads):
        if t["thread_id"] == survivor_id:
            new_threads[i] = survivor
            break

    inherit_from = inherit_classification or survivor_id
    base_class = deepcopy(classifications.get(inherit_from, {}))
    base_class = patch_classification(
        classification_from_thread(survivor, base_class),
        reason=base_class.get("reason") or "manual_merge",
    )
    new_classifications = {
        k: v for k, v in classifications.items() if k not in remove
    }
    new_classifications[survivor_id] = base_class

    return new_threads, new_classifications, survivor_id


def _validate_ranges(message_count: int, ranges: list[dict[str, int]]) -> None:
    covered = [False] * message_count
    for r in ranges:
        start = r["start_index"]
        end = r["end_index"]
        if start < 0 or end >= message_count or start > end:
            raise ValueError(f"invalid range {start}-{end} for {message_count} messages")
        for i in range(start, end + 1):
            if covered[i]:
                raise ValueError(f"overlapping range at index {i}")
            covered[i] = True
    if message_count and not all(covered):
        raise ValueError("ranges must partition all messages")


def split_thread(
    threads: list[dict[str, Any]],
    classifications: dict[str, dict[str, Any]],
    source_id: str,
    ranges: list[dict[str, int]],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], list[str]]:
    by_id = {t["thread_id"]: t for t in threads}
    if source_id not in by_id:
        raise ValueError(f"unknown thread id: {source_id}")

    source = deepcopy(by_id[source_id])
    messages = source.get("messages") or []
    message_ids = source.get("message_ids") or list(range(len(messages)))
    _validate_ranges(len(messages), ranges)

    new_thread_ids: list[str] = []
    new_threads_for_split: list[dict[str, Any]] = []

    for idx, r in enumerate(ranges, start=1):
        start = r["start_index"]
        end = r["end_index"]
        part_messages = messages[start : end + 1]
        part_ids = message_ids[start : end + 1]
        if idx == 1:
            thread_id = source_id
        else:
            thread_id = f"{source_id}-split-{idx - 1}"
        part = deepcopy(source)
        part["thread_id"] = thread_id
        part["messages"] = part_messages
        part["message_ids"] = part_ids
        recompute_thread_stats(part)
        new_threads_for_split.append(part)
        new_thread_ids.append(thread_id)

    new_threads = [t for t in threads if t["thread_id"] != source_id]
    new_threads.extend(new_threads_for_split)

    base_class = deepcopy(classifications.get(source_id, {}))
    new_classifications = {
        k: v for k, v in classifications.items() if k != source_id
    }
    for part in new_threads_for_split:
        cls = patch_classification(
            classification_from_thread(part, deepcopy(base_class)),
            reason="manual_split",
        )
        new_classifications[part["thread_id"]] = cls

    return new_threads, new_classifications, new_thread_ids


def _next_split_id(source_id: str, thread_ids: set[str]) -> str:
    n = 1
    while f"{source_id}-split-{n}" in thread_ids:
        n += 1
    return f"{source_id}-split-{n}"


def indices_for_split_mode(mode: str, message_indices: list[int]) -> set[int]:
    if not message_indices:
        raise ValueError("message_indices must not be empty")
    unique = sorted(set(message_indices))
    if mode == "sparse":
        return set(unique)
    if mode == "range":
        return set(range(unique[0], unique[-1] + 1))
    raise ValueError(f"unknown split mode: {mode}")


def extract_messages_to_new_thread(
    threads: list[dict[str, Any]],
    classifications: dict[str, dict[str, Any]],
    source_id: str,
    message_indices: list[int],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], str, str | None]:
    """Move ``message_indices`` into a new thread; remainder stays in ``source_id``."""
    by_id = {t["thread_id"]: t for t in threads}
    if source_id not in by_id:
        raise ValueError(f"unknown thread id: {source_id}")

    indices = set(message_indices)
    source = deepcopy(by_id[source_id])
    messages = source.get("messages") or []
    message_ids = source.get("message_ids") or list(range(len(messages)))

    invalid = [i for i in indices if i < 0 or i >= len(messages)]
    if invalid:
        raise ValueError(f"invalid message indices: {invalid}")
    if len(indices) == len(messages):
        raise ValueError("cannot extract all messages; leave at least one in the source")

    extracted_indices = sorted(indices)
    extracted_messages = [messages[i] for i in extracted_indices]
    extracted_ids = [message_ids[i] for i in extracted_indices]
    remaining_messages = [m for i, m in enumerate(messages) if i not in indices]
    remaining_ids = [message_ids[i] for i in range(len(messages)) if i not in indices]

    all_ids = {t["thread_id"] for t in threads}
    new_thread_id = _next_split_id(source_id, all_ids)

    new_thread = deepcopy(source)
    new_thread["thread_id"] = new_thread_id
    new_thread["messages"] = extracted_messages
    new_thread["message_ids"] = extracted_ids
    recompute_thread_stats(new_thread)

    base_class = deepcopy(classifications.get(source_id, {}))
    new_class = patch_classification(
        classification_from_thread(new_thread, deepcopy(base_class)),
        reason="manual_split",
    )

    new_threads = [t for t in threads if t["thread_id"] != source_id]
    new_classifications = dict(classifications)
    remainder_id: str | None = source_id

    if remaining_messages:
        source["messages"] = remaining_messages
        source["message_ids"] = remaining_ids
        recompute_thread_stats(source)
        new_threads.append(source)
        new_classifications[source_id] = classification_from_thread(
            source, new_classifications.get(source_id, {})
        )
    else:
        new_classifications.pop(source_id, None)
        remainder_id = None

    new_threads.append(new_thread)
    new_classifications[new_thread_id] = new_class

    return new_threads, new_classifications, new_thread_id, remainder_id


def split_by_mode(
    threads: list[dict[str, Any]],
    classifications: dict[str, dict[str, Any]],
    source_id: str,
    mode: str,
    message_indices: list[int],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], str, str | None]:
    """Split using ``sparse`` (exact indices) or ``range`` (min–max inclusive)."""
    indices = indices_for_split_mode(mode, message_indices)
    return extract_messages_to_new_thread(
        threads, classifications, source_id, sorted(indices)
    )


def move_messages(
    threads: list[dict[str, Any]],
    classifications: dict[str, dict[str, Any]],
    source_id: str,
    message_indices: list[int],
    target_id: str,
    position: str,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    if source_id == target_id:
        raise ValueError("source and target must differ")
    if not message_indices:
        raise ValueError("message_indices must not be empty")

    sorted_indices = sorted(message_indices)
    if sorted_indices != list(range(sorted_indices[0], sorted_indices[-1] + 1)):
        raise ValueError("message_indices must be contiguous")

    by_id = {t["thread_id"]: deepcopy(t) for t in threads}
    if source_id not in by_id or target_id not in by_id:
        raise ValueError("unknown source or target thread id")

    source = by_id[source_id]
    target = by_id[target_id]
    src_messages = source.get("messages") or []
    src_ids = source.get("message_ids") or list(range(len(src_messages)))

    moving_messages = [src_messages[i] for i in sorted_indices]
    moving_ids = [src_ids[i] for i in sorted_indices]

    remaining_messages = [
        m for i, m in enumerate(src_messages) if i not in sorted_indices
    ]
    remaining_ids = [mid for i, mid in enumerate(src_ids) if i not in sorted_indices]

    source["messages"] = remaining_messages
    source["message_ids"] = remaining_ids
    recompute_thread_stats(source)

    tgt_messages = list(target.get("messages") or [])
    tgt_ids = list(target.get("message_ids") or list(range(len(tgt_messages))))

    if position == "prepend":
        combined = list(zip(moving_messages, moving_ids)) + list(
            zip(tgt_messages, tgt_ids, strict=False)
        )
    elif position == "append":
        combined = list(zip(tgt_messages, tgt_ids, strict=False)) + list(
            zip(moving_messages, moving_ids)
        )
    else:
        raise ValueError("position must be prepend or append")

    combined.sort(key=lambda pair: pair[0].get("datetime", ""))
    target["messages"] = [m for m, _ in combined]
    target["message_ids"] = [mid for _, mid in combined]
    recompute_thread_stats(target)

    new_threads = []
    new_classifications = dict(classifications)
    thread_order = [t["thread_id"] for t in threads]
    for tid in thread_order:
        if tid not in by_id:
            continue
        thread = by_id[tid]
        if tid == source_id and not thread.get("messages"):
            new_classifications.pop(source_id, None)
            continue
        new_threads.append(thread)
        if tid in (source_id, target_id):
            cls = new_classifications.get(tid, {})
            new_classifications[tid] = classification_from_thread(thread, cls)

    return new_threads, new_classifications
