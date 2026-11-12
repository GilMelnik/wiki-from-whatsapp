"""Undo for the conversations tool: the edits of this session, newest last.

An entry keeps what the edit replaced (the threads as they were) and the ids of the threads it
made, plus the stamp of the edited file right after it was saved. An entry is offered only
while the file is still exactly what the tool left: a step-1 run or any other outside write
makes the history void, so an undo never overwrites work done outside the tool.

The history is owned by the tool, not the store, so it survives the store's reloads.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from review_tool.fs import FileStamp


@dataclass(frozen=True)
class UndoEntry:
    label: str
    focus: str
    at: str
    before: tuple[dict[str, Any], ...]
    after_ids: tuple[str, ...]
    stamp_after: FileStamp | None


class EditHistory:
    """The edits of this session that can still be taken back (at most ``depth``)."""

    def __init__(self, depth: int) -> None:
        self.depth = depth
        self._entries: list[UndoEntry] = []

    def record(self, entry: UndoEntry) -> None:
        self._entries = [*self._entries, entry][-self.depth :]

    def latest(self, current: FileStamp | None) -> UndoEntry | None:
        """The newest edit, while the file is still what it left; else the history is void."""
        if self._entries and self._entries[-1].stamp_after != current:
            self._entries = []
        return self._entries[-1] if self._entries else None

    def pop(self, current: FileStamp | None) -> None:
        """Drop the newest entry after it was undone; the file (rewritten by the undo) now
        holds what the entry before it left."""
        self._entries.pop()
        if self._entries:
            self._entries[-1] = replace(self._entries[-1], stamp_after=current)
