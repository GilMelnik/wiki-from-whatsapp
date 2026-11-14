"""What the conversations tool shows: the list of threads and the message views.

A view is a list of *blocks*, one per thread, each with the messages shown of it:

    {"thread_id", "role", "start_time", "last_time", "size", "people", "partial",
     "messages": [{id, datetime, sender, content, quote?, reactions?,
                   seq, position, thread_id, reply_to?: {id, thread_id, sender, datetime, seq}}]}

``role`` says why the thread is in view: ``current`` (the opened thread), ``part`` (another
part of the same split), ``previous`` / ``next`` / ``around`` (the conversation the chat had
right before the thread began / right after it ended / both), ``window`` (a thread with
messages inside the gold window), ``included`` (asked for by the page, e.g. a thread messages
were just moved into), ``context`` (only the messages of a loaded stretch of the chat:
``partial``). ``seq`` is the message's place in the whole chat, so the page can tell where
messages of the chat are not shown (a gap in ``seq``) and offer to load them.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any, Literal

from review_tool.tools.threads.chat import ChatIndex
from review_tool.tools.threads.gold_windows import GoldWindows
from review_tool.tools.threads.operations import UnknownItem, family_base
from step_1_threads_split.gold import message_moment
from utils.config import section

REVIEW_CONFIG = section("threads")["review"]
SortKind = Literal["time", "size"]
Range = tuple[int, int]


def parse_ranges(values: Iterable[str]) -> list[Range]:
    """``["120-129", "300-300"]`` → ``[(120, 129), (300, 300)]``; ``ValueError`` when malformed."""
    ranges = []
    for value in values:
        first, separator, last = value.partition("-")
        if not separator or not first.isdigit() or not last.isdigit() or int(first) > int(last):
            raise ValueError(f"context range {value!r} is not 'first-last' (chat places, first ≤ last)")
        ranges.append((int(first), int(last)))
    return ranges


def excerpt(text: str, limit: int, around: int = 0) -> str:
    """One line of ``text``, at most ``limit`` characters, centred near ``around``."""
    flat = " ".join((text or "").split())
    if len(flat) <= limit:
        return flat
    start = max(0, min(around - limit // 3, len(flat) - limit))
    cut = flat[start : start + limit]
    return f"{'…' if start else ''}{cut}{'…' if start + limit < len(flat) else ''}"


class ThreadViews:
    """Read-only views over one state of the threads."""

    def __init__(self, threads: Sequence[dict[str, Any]], chat: ChatIndex, gold: GoldWindows) -> None:
        self.threads = threads
        self.chat = chat
        self.gold = gold
        self.by_id = {thread["thread_id"]: thread for thread in threads}
        self.limit = int(REVIEW_CONFIG["excerpt_chars"])

    # ----------------------------------------------------------------- the list
    def rows(self, query: str = "", sort: SortKind = "time") -> dict[str, Any]:
        """One row per thread (in time order, or largest first); with ``query`` only the
        threads with a message whose text or sender contains it."""
        needle = query.strip().casefold()
        items = []
        for thread in self.threads:
            messages = thread["messages"]
            row = {
                "thread_id": thread["thread_id"],
                "start_time": thread["start_time"],
                "last_time": thread["last_time"],
                "size": len(messages),
                "people": thread["num_unique_senders"],
                "opening": excerpt(messages[0].get("content") or "", self.limit),
            }
            if needle:
                hits = [m for m in messages if needle in _searchable(m)]
                if not hits:
                    continue
                content = hits[0].get("content") or ""
                where = " ".join(content.split()).casefold().find(needle)
                row.update(matches=len(hits), match=excerpt(content, self.limit, max(where, 0)))
            items.append(row)
        if sort == "size":
            items.sort(key=lambda row: (-row["size"], row["start_time"]))
        return {"items": items, "total": len(self.threads), "query": query.strip()}

    # ---------------------------------------------------------------- the views
    def thread_view(
        self, thread_id: str, include: Sequence[str], context: Sequence[Range], window: int | None
    ) -> dict[str, Any]:
        """The thread, the other parts of its split, and the conversations right around it."""
        thread = self.thread(thread_id)
        base = family_base(thread_id)
        family = [t for t in self.threads if family_base(t["thread_id"]) == base]
        ids = sorted((m["id"] for t in family for m in t["messages"]), key=self.chat.seq.__getitem__)
        before = self.chat.neighbour_thread(ids[0], -1)
        after = self.chat.neighbour_thread(ids[-1], +1)
        roles = [(before, "around" if before == after else "previous")]
        roles += [(t["thread_id"], "current" if t is thread else "part") for t in family]
        roles += [(after, "next")]
        roles += [(extra, "included") for extra in include]
        subject = {"kind": "thread", "thread_id": thread_id}
        return self._view(subject, roles, context, window)

    def window_view(self, index: int, include: Sequence[str], context: Sequence[Range]) -> dict[str, Any]:
        """Every thread with a message in gold window ``index``, in the order they speak."""
        window = self.gold.window(index)
        roles = [(thread_id, "window") for thread_id, _ in self.gold.members(self.threads)[index]]
        roles += [(extra, "included") for extra in include]
        subject = {"kind": "window", "index": index, **window.to_dict()}
        return self._view(subject, roles, context, index)

    def _view(
        self,
        subject: dict[str, Any],
        roles: Sequence[tuple[str | None, str]],
        context: Sequence[Range],
        window: int | None,
    ) -> dict[str, Any]:
        blocks: list[dict[str, Any]] = []
        for thread_id, role in roles:
            if thread_id in self.by_id and all(b["thread_id"] != thread_id for b in blocks):
                blocks.append(self._block(self.by_id[thread_id], role))
        extra, truncated = self._context_blocks(context, {b["thread_id"] for b in blocks})
        blocks += extra
        result = {
            "subject": subject,
            "chat_size": self.chat.size,
            "blocks": blocks,
            "context_truncated": truncated,
            "in_window_ids": None,
        }
        if window is not None:
            span = self.gold.window(window)
            result["window"] = {"index": window, **span.to_dict()}
            result["in_window_ids"] = [
                m["id"] for b in blocks for m in b["messages"] if span.contains(message_moment(m))
            ]
        return result

    def _context_blocks(self, ranges: Sequence[Range], shown: set[str]) -> tuple[list[dict[str, Any]], bool]:
        """Partial blocks for the loaded stretches of the chat (threads not shown whole)."""
        wanted = sorted({wa_id for first, last in ranges for wa_id in self.chat.ids_between(first, last)},
                        key=self.chat.seq.__getitem__)
        cap = int(REVIEW_CONFIG["max_context_messages"])
        grouped: dict[str, list[dict[str, Any]]] = {}
        for wa_id in wanted[:cap]:
            thread_id = self.chat.thread_of[wa_id]
            if thread_id not in shown:
                grouped.setdefault(thread_id, []).append(self.chat.messages[wa_id])
        blocks = [self._block(self.by_id[t], "context", messages) for t, messages in grouped.items()]
        return blocks, len(wanted) > cap

    # ---------------------------------------------------------------- helpers
    def thread(self, thread_id: str) -> dict[str, Any]:
        try:
            return self.by_id[thread_id]
        except KeyError:
            raise UnknownItem(f"No conversation {thread_id} (any more).") from None

    def _block(
        self, thread: dict[str, Any], role: str, messages: Sequence[dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        return {
            "thread_id": thread["thread_id"],
            "role": role,
            "start_time": thread["start_time"],
            "last_time": thread["last_time"],
            "size": len(thread["messages"]),
            "people": thread["num_unique_senders"],
            "partial": messages is not None,
            "messages": [self._message(m) for m in (thread["messages"] if messages is None else messages)],
        }

    def _message(self, message: dict[str, Any]) -> dict[str, Any]:
        chat = self.chat
        wa_id = message["id"]
        out = {
            **message,
            "seq": chat.seq[wa_id],
            "position": chat.position[wa_id],
            "thread_id": chat.thread_of[wa_id],
        }
        quoted = chat.reply_to.get(wa_id)
        if quoted is not None:
            target = chat.messages[quoted]
            out["reply_to"] = {
                "id": quoted,
                "thread_id": chat.thread_of[quoted],
                "sender": target.get("sender"),
                "datetime": target["datetime"],
                "seq": chat.seq[quoted],
            }
        return out


def _searchable(message: dict[str, Any]) -> str:
    return f"{' '.join((message.get('content') or '').split())}\n{message.get('sender') or ''}".casefold()
