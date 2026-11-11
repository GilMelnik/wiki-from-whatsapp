"""Split chat messages into conversation threads with three explainable rules.

Messages are read in time order and each one either joins an existing thread or opens a new
one:

1. **Reply.** A message that quotes an earlier message joins the thread of the quoted message,
   however long ago it was written (the quote names the sender and the text; the latest earlier
   message with that sender and text is the quoted one).
2. **Same conversation.** Otherwise, a message written at most ``quiet_minutes`` after the
   message before it continues that message's thread.
3. **Resumed conversation.** After a longer silence, the message continues the thread of the
   message before it only if it is about the same thing: its embedding (``utils.embedder``,
   kind ``threads.embedding_kind``) has a cosine of at least ``resume_similarity`` with one of
   that thread's last ``recent_messages`` messages. Otherwise it opens a new thread.

Parameters (``config.json`` ``threads.split``):

- ``quiet_minutes``: the longest pause that still counts as the same conversation.
- ``resume_similarity``: how similar a message after a longer pause must be to the previous
  thread to continue it.
- ``recent_messages``: how many of the previous thread's last messages that test compares with.

Rules 2 and 3 look only at the thread of the previous message: on the owner's gold windows
the most recent thread was the right one for 301 of 305 messages that had a choice, while
picking the most similar thread was right for fewer than 270.

A thread's id is ``t-<WhatsApp id of its first message>``; it is set when the thread is
created and never changes, also when later messages join it (``assign_new_messages``).
"""

from __future__ import annotations

import bisect
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

import numpy as np

from step_1_threads_split.message import normalize_content, quote_key
from utils.config import section

THREADS_CONFIG = section("threads")
EmbedFn = Callable[[Sequence[str]], np.ndarray]


@dataclass(frozen=True)
class SplitParams:
    quiet_minutes: float
    resume_similarity: float
    recent_messages: int

    @classmethod
    def from_config(cls) -> SplitParams:
        return cls(**THREADS_CONFIG["split"])

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def embedding_text(message: dict[str, Any]) -> str:
    """The text a message is embedded by: its content, cut to ``threads.embed_max_chars``."""
    return normalize_content(message.get("content") or "")[: int(THREADS_CONFIG["embed_max_chars"])]


def thread_record(thread_id: str, messages: list[dict[str, Any]]) -> dict[str, Any]:
    """A C1 thread from its messages (kept in the given order).

    ``participants`` are the senders and the people who reacted, and ``num_messages`` /
    ``num_unique_senders`` / ``last_sender`` are filled as well; the conversations tool
    rebuilds every thread it edits with this function.
    """
    people = {m["sender"] for m in messages if m.get("sender")}
    for message in messages:
        for reaction in message.get("reactions") or []:
            people.update(s for s in reaction.get("senders") or [] if s)
    participants = sorted(people)
    return {
        "thread_id": thread_id,
        "start_time": messages[0]["datetime"],
        "last_time": messages[-1]["datetime"],
        "participants": participants,
        "message_ids": [m["id"] for m in messages],
        "num_messages": len(messages),
        "num_unique_senders": len(participants),
        "last_sender": messages[-1]["sender"],
        "messages": messages,
    }


class ThreadSplitter:
    """The three rules of the module docstring over C1 messages and threads."""

    def __init__(self, params: SplitParams, embed: EmbedFn) -> None:
        self.params = params
        self.embed = embed

    def split(self, messages: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        """Threads for ``messages`` from scratch (a full run)."""
        return self.assign_new_messages([], messages)

    def assign_new_messages(
        self, prior_threads: Sequence[dict[str, Any]], new_messages: Sequence[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Add ``new_messages`` (C1 message records) to ``prior_threads`` (C1 threads).

        New messages are taken in time order; each joins a prior thread or a thread opened by
        an earlier new message, or opens a thread of its own. Prior threads keep their ids,
        their messages and their order of messages; a new message is placed by its time. A
        message whose id is already in a prior thread is skipped. Returns every thread
        (sorted by start time, then id) as a new list; the inputs are not modified.
        """
        state = _Timeline(prior_threads)
        fresh = [m for m in new_messages if m["id"] not in state.thread_of]
        fresh.sort(key=lambda m: m["datetime"])
        texts = [embedding_text(m) for m in state.messages.values()]
        texts += [embedding_text(m) for m in fresh]
        vectors = dict(zip([*state.messages, *(m["id"] for m in fresh)], self.embed(texts)))
        for message in fresh:
            state.add(message, self._choose(message, state, vectors))
        return state.threads()

    def _choose(
        self, message: dict[str, Any], state: _Timeline, vectors: dict[str, np.ndarray]
    ) -> str | None:
        """The thread ``message`` joins, or ``None`` for a new thread."""
        quoted = state.quoted(message)
        if quoted is not None:
            return state.thread_of[quoted]
        previous = state.previous(message["datetime"])
        if previous is None:
            return None
        gap = _minutes(previous["datetime"], message["datetime"])
        thread_id = state.thread_of[previous["id"]]
        if gap <= self.params.quiet_minutes:
            return thread_id
        recent = state.recent(thread_id, message["datetime"], self.params.recent_messages)
        similarity = max(float(vectors[message["id"]] @ vectors[wa_id]) for wa_id in recent)
        return thread_id if similarity >= self.params.resume_similarity else None


def _minutes(earlier: str, later: str) -> float:
    return (datetime.fromisoformat(later) - datetime.fromisoformat(earlier)).total_seconds() / 60


class _Timeline:
    """Messages placed so far: their threads, their time order and the quote index."""

    def __init__(self, threads: Sequence[dict[str, Any]]) -> None:
        self.messages: dict[str, dict[str, Any]] = {}
        self.thread_of: dict[str, str] = {}
        self.members: dict[str, list[str]] = {}
        self._order: list[tuple[str, int]] = []  # (datetime, sequence number), sorted
        self._ids: list[str] = []  # wa id per sequence number
        self._quotes: dict[tuple[str, str], list[tuple[str, int]]] = {}
        self._prior: dict[str, tuple[dict[str, Any], list[str]]] = {}
        for thread in threads:
            self.members[thread["thread_id"]] = []
            for message in thread["messages"]:
                self._place(message, thread["thread_id"])
            self._prior[thread["thread_id"]] = (thread, list(self.members[thread["thread_id"]]))

    def _place(self, message: dict[str, Any], thread_id: str) -> None:
        wa_id = message["id"]
        if wa_id in self.thread_of:
            raise ValueError(
                f"message {wa_id} is in threads {self.thread_of[wa_id]!r} and {thread_id!r}"
            )
        moment = message["datetime"]
        self.messages[wa_id] = message
        self.thread_of[wa_id] = thread_id
        members = self.members[thread_id]
        if members and moment < self.messages[members[-1]]["datetime"]:
            times = [self.messages[m]["datetime"] for m in members]
            members.insert(bisect.bisect_right(times, moment), wa_id)
        else:
            members.append(wa_id)
        entry = (moment, len(self._ids))
        bisect.insort(self._order, entry)
        self._ids.append(wa_id)
        key = quote_key(message["sender"], message.get("content") or "")
        self._quotes.setdefault(key, []).append(entry)

    def add(self, message: dict[str, Any], thread_id: str | None) -> None:
        """Place a new message in ``thread_id``, or in a new thread named after it."""
        if thread_id is None:
            thread_id = f"t-{message['id']}"
            number = 1
            while thread_id in self.members:  # only when an edited thread already took the id
                thread_id = f"t-{message['id']}-{number}"
                number += 1
            self.members[thread_id] = []
        self._place(message, thread_id)

    def previous(self, moment: str) -> dict[str, Any] | None:
        """The latest placed message written at or before ``moment``."""
        index = bisect.bisect_right(self._order, (moment, len(self._ids)))
        return self.messages[self._ids[self._order[index - 1][1]]] if index else None

    def quoted(self, message: dict[str, Any]) -> str | None:
        """The id of the message ``message`` replies to, when it is already placed."""
        quote = message.get("quote")
        if not quote:
            return None
        candidates = self._quotes.get(quote_key(quote["sender"], quote.get("text") or ""), [])
        earlier = [entry for entry in candidates if entry[0] <= message["datetime"]]
        return self._ids[max(earlier)[1]] if earlier else None

    def recent(self, thread_id: str, moment: str, count: int) -> list[str]:
        """The last ``count`` messages of a thread written at or before ``moment``."""
        members = self.members[thread_id]
        return [m for m in members if self.messages[m]["datetime"] <= moment][-count:]

    def threads(self) -> list[dict[str, Any]]:
        """Every thread; a prior thread no message joined is returned as it was given."""
        records = []
        for thread_id, members in self.members.items():
            prior, prior_members = self._prior.get(thread_id, (None, None))
            if prior is not None and members == prior_members:
                records.append(prior)
            elif members:
                records.append(thread_record(thread_id, [self.messages[m] for m in members]))
        return sorted(records, key=lambda t: (t["start_time"], t["thread_id"]))
