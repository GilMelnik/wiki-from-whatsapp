"""The chat as one time line: where each message is, what it replies to, what lies around it.

The threads file holds every message of the chat once, cut into threads. ``ChatIndex`` puts
them back in chat order (``seq`` 0, 1, 2, ...: time, then the export's own order for messages
of the same minute) and answers what a reviewer needs to judge a split:

- ``reply_to``: the message a reply quotes, found as step 1's reply rule finds it (same sender
  and text, the latest earlier one), so "who replied to whom" is the splitter's own view;
- ``thread_of`` / ``position``: the thread a message sits in now and its place there (updated
  after every edit with ``locate``);
- ``ids_between`` / ``neighbour_thread``: a stretch of the chat and the conversations right
  around a message, for the context a view does not show yet.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from step_1_threads_split.message import quote_key

Message = dict[str, Any]


def export_rank(export_path: Path) -> dict[str, int]:
    """WhatsApp id → place in the chat export (empty when there is no export file)."""
    if not export_path.is_file():
        return {}
    entries = json.loads(export_path.read_text(encoding="utf-8"))
    return {str(entry["id"]): rank for rank, entry in enumerate(entries)}


class ChatIndex:
    """Every message of ``threads`` in chat order, with replies and current places."""

    def __init__(self, threads: Sequence[dict[str, Any]], rank: dict[str, int]) -> None:
        last = len(rank)
        messages = [m for thread in threads for m in thread["messages"]]
        messages.sort(key=lambda m: (m["datetime"], rank.get(m["id"], last)))
        self.ids: list[str] = [m["id"] for m in messages]
        self.seq: dict[str, int] = {wa_id: index for index, wa_id in enumerate(self.ids)}
        self.messages: dict[str, Message] = {m["id"]: m for m in messages}
        self.reply_to: dict[str, str] = self._replies(messages)
        self.thread_of: dict[str, str] = {}
        self.position: dict[str, int] = {}
        self.locate(threads)

    @property
    def size(self) -> int:
        return len(self.ids)

    def locate(self, threads: Iterable[dict[str, Any]]) -> None:
        """Record which thread each message sits in now, and where in it."""
        self.thread_of = {}
        self.position = {}
        for thread in threads:
            for position, message in enumerate(thread["messages"]):
                self.thread_of[message["id"]] = thread["thread_id"]
                self.position[message["id"]] = position

    def ids_between(self, first: int, last: int) -> list[str]:
        """The ids of chat places ``first`` to ``last`` (inclusive, clipped to the chat)."""
        return self.ids[max(first, 0) : min(last, self.size - 1) + 1]

    def neighbour_thread(self, wa_id: str, step: int) -> str | None:
        """The thread of the chat message right before (``step=-1``) or after (``+1``) ``wa_id``."""
        index = self.seq[wa_id] + step
        return self.thread_of.get(self.ids[index]) if 0 <= index < self.size else None

    @staticmethod
    def _replies(messages: Sequence[Message]) -> dict[str, str]:
        """Reply id → quoted id: the latest earlier message with the quote's sender and text."""
        seen: dict[tuple[str, str], list[int]] = {}
        replies: dict[str, str] = {}
        for index, message in enumerate(messages):
            quote = message.get("quote")
            if quote:
                earlier = seen.get(quote_key(quote.get("sender") or "", quote.get("text") or ""))
                if earlier:  # indices of earlier messages, ascending
                    replies[message["id"]] = messages[earlier[-1]]["id"]
            key = quote_key(message.get("sender") or "", message.get("content") or "")
            seen.setdefault(key, []).append(index)
        return replies
