"""The edits of the conversations tool on C1 threads: move messages, merge threads, undo.

Every edit is pure: it returns a new thread list and never modifies the given one. A thread
an edit touches is rebuilt with ``splitter.thread_record`` (its messages in chat order);
every other thread stays the very same object, so what an edit replaced (``Edit.before``) and
what it made (``Edit.after_ids``) follow from identity, which is all that undo needs.

Ids follow contract C1: a thread keeps its id, also when it loses its first message or gains
earlier ones. A new thread split off ``t-x`` (or off one of its parts) is ``t-x-split-N`` with
the lowest free ``N``, the id scheme of the old tagger, which step 1 keeps as it is.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from step_1_threads_split.splitter import thread_record

Thread = dict[str, Any]
SPLIT_SUFFIX = re.compile(r"-split-\d+$")


class EditRefused(ValueError):
    """The edit does not fit the threads as they are; the message says what to do instead."""


class UnknownItem(LookupError):
    """A thread or message id that the threads file does not hold (any more)."""


@dataclass(frozen=True)
class Edit:
    """The outcome of one edit: the new thread list and what undo needs to put it back."""

    label: str
    focus: str
    threads: list[Thread]
    before: tuple[Thread, ...]
    after_ids: tuple[str, ...]
    created: str | None = None
    removed: tuple[str, ...] = ()


def family_base(thread_id: str) -> str:
    """The thread a split part came from: ``t-x-split-2`` → ``t-x``."""
    return SPLIT_SUFFIX.sub("", thread_id)


def ordered(threads: Iterable[Thread]) -> list[Thread]:
    """Threads in the order step 1 writes them: by start time, then id."""
    return sorted(threads, key=lambda thread: (thread["start_time"], thread["thread_id"]))


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


class ThreadEditor:
    """Edits over one list of threads; ``seq`` is every message's place in the chat."""

    def __init__(self, threads: Sequence[Thread], seq: Mapping[str, int]) -> None:
        self.threads = list(threads)
        self.seq = seq
        self.by_id = {thread["thread_id"]: thread for thread in self.threads}
        self.owner = {m["id"]: t["thread_id"] for t in self.threads for m in t["messages"]}

    # ------------------------------------------------------------------ edits
    def move(self, message_ids: Sequence[str], target_id: str | None) -> Edit:
        """Move the messages into ``target_id``, or into a new thread when it is ``None``.

        The messages may come from any threads and need not be next to each other. A new
        thread is split off the thread of the earliest message (when that whole thread is
        selected, it simply receives the others). Threads left empty are removed.
        """
        wanted = sorted(dict.fromkeys(message_ids), key=lambda wa_id: self.seq.get(wa_id, -1))
        if not wanted:
            raise EditRefused("Select at least one message first.")
        unknown = [wa_id for wa_id in wanted if wa_id not in self.owner]
        if unknown:
            raise UnknownItem(f"These messages are not in any conversation: {', '.join(unknown[:5])}.")
        if target_id is not None:
            self._require_threads([target_id])
        by_source: dict[str, set[str]] = {}
        for wa_id in wanted:
            by_source.setdefault(self.owner[wa_id], set()).add(wa_id)
        created = None
        if target_id is None:
            first = self.owner[wanted[0]]
            if len(by_source[first]) == len(self.by_id[first]["messages"]):
                target_id = first
                del by_source[first]
                if not by_source:
                    raise EditRefused("The selected messages already are one whole conversation.")
            else:
                target_id = created = self._split_id(first)
        else:
            by_source.pop(target_id, None)
            if not by_source:
                raise EditRefused(f"The selected messages are already all in {target_id}.")

        result = dict(self.by_id)
        moving = [] if created else list(self.by_id[target_id]["messages"])
        removed: list[str] = []
        for thread_id, selected in by_source.items():
            messages = self.by_id[thread_id]["messages"]
            moving += [m for m in messages if m["id"] in selected]
            kept = [m for m in messages if m["id"] not in selected]
            if kept:
                result[thread_id] = self._record(thread_id, kept)
            else:
                del result[thread_id]
                removed.append(thread_id)
        result[target_id] = self._record(target_id, moving)
        moved = _count(len(wanted), "message")
        label = (
            f"New conversation {created} from {moved}" if created else f"Moved {moved} to {target_id}"
        )
        if removed:
            label += f"; {', '.join(removed)} became empty and was removed"
        return self._edit(result, label, target_id, created=created, removed=removed)

    def merge(self, thread_ids: Sequence[str]) -> Edit:
        """One thread from all messages of ``thread_ids``; it keeps the id of the earliest."""
        ids = list(dict.fromkeys(thread_ids))
        if len(ids) < 2:
            raise EditRefused("Pick two or more conversations to merge.")
        self._require_threads(ids)
        survivor = min(ids, key=lambda thread_id: (self._first_seq(thread_id), thread_id))
        result = dict(self.by_id)
        messages = [m for thread_id in ids for m in self.by_id[thread_id]["messages"]]
        others = [thread_id for thread_id in ids if thread_id != survivor]
        for thread_id in others:
            del result[thread_id]
        result[survivor] = self._record(survivor, messages)
        return self._edit(result, f"Merged {', '.join(others)} into {survivor}", survivor, removed=others)

    def restore(self, before: Sequence[Thread], after_ids: Sequence[str]) -> list[Thread]:
        """The threads with an edit taken back: its threads out, the ones it replaced back in."""
        result = {k: v for k, v in self.by_id.items() if k not in set(after_ids)}
        clash = [thread["thread_id"] for thread in before if thread["thread_id"] in result]
        if clash:
            raise EditRefused(f"Cannot undo: {', '.join(clash)} exists again in the meantime.")
        result.update((thread["thread_id"], thread) for thread in before)
        return ordered(result.values())

    # ---------------------------------------------------------------- helpers
    def _require_threads(self, thread_ids: Iterable[str]) -> None:
        unknown = [thread_id for thread_id in thread_ids if thread_id not in self.by_id]
        if unknown:
            raise UnknownItem(f"No conversation {', '.join(unknown)} (any more).")

    def _first_seq(self, thread_id: str) -> int:
        return min(self.seq[m["id"]] for m in self.by_id[thread_id]["messages"])

    def _record(self, thread_id: str, messages: Iterable[dict[str, Any]]) -> Thread:
        return thread_record(thread_id, sorted(messages, key=lambda m: self.seq[m["id"]]))

    def _split_id(self, source_id: str) -> str:
        base = family_base(source_id)
        number = 1
        while f"{base}-split-{number}" in self.by_id:
            number += 1
        return f"{base}-split-{number}"

    def _edit(
        self,
        result: dict[str, Thread],
        label: str,
        focus: str,
        *,
        created: str | None = None,
        removed: Sequence[str] = (),
    ) -> Edit:
        before = tuple(t for k, t in self.by_id.items() if result.get(k) is not t)
        after_ids = tuple(k for k, t in result.items() if self.by_id.get(k) is not t)
        return Edit(label, focus, ordered(result.values()), before, after_ids, created, tuple(removed))
