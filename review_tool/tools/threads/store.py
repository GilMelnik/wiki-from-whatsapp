"""The threads store of the conversations tool: step 1's threads, as the next steps will read them.

It reads the edited file when it exists, else the step's original (contract C1), and writes
only the edited file: the first edit creates it from what was loaded, with ``metadata`` kept
whole (step 1 reads an edited file only while ``metadata.format`` is ``threads.format``) plus
``edited_by`` / ``edited_at`` / ``thread_count``. The edited file is backed up the first time
this store overwrites it. Use it through a ``StoreHandle``: reads via ``handle.get()``, edits
inside ``handle.mutate()`` (the edited file's lock is held there).

A file in another format (the old splitter's output, the old tagging) is a state, not an
error: the store loads, ``problem`` says what the file is and what to do, and every view or
edit is refused with that sentence (``OldFormat``). Nothing is ever written to such a file.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from review_tool.fs import FileStamp, stamp
from review_tool.tools.threads.chat import ChatIndex, export_rank
from review_tool.tools.threads.gold_windows import GoldWindows
from review_tool.tools.threads.history import EditHistory, UndoEntry
from review_tool.tools.threads.operations import Edit, EditRefused, ThreadEditor
from review_tool.tools.threads.views import ThreadViews
from utils.config import section
from utils.json_io import write_json_file
from utils.reconcile import backup_file
from utils.timestamps import local_now

logger = logging.getLogger(__name__)

THREADS_CONFIG = section("threads")
THREADS_FORMAT = THREADS_CONFIG["format"]
EDITED_BY = "threads_reviewer"


class OldFormat(RuntimeError):
    """The threads file is not step 1's current format; the message says what to do."""


def format_problem(path: Path, payload: Any) -> str | None:
    """Why ``payload`` (read from ``path``) cannot be reviewed here, or ``None`` when it can."""
    found = payload.get("metadata", {}).get("format") if isinstance(payload, dict) else None
    if found == THREADS_FORMAT and isinstance(payload.get("threads"), list):
        return None
    edited = "edited copy of the conversations" if path.stem.endswith("_edited") else "conversations file"
    mark = f"its format mark is {found!r}" if found else "it has no format mark"
    return (
        f"The {edited} ({path.name}) was made by the old splitter or the old tagger ({mark}, "
        f"not {THREADS_FORMAT!r}), so this tool neither shows nor changes it. Move it out of the "
        "step folder and run step 1, which writes the conversations in the current format; the "
        "gold windows and their done marks are kept."
    )


class ThreadsStore:
    """The threads as loaded from disk, the views over them, and the edits."""

    def __init__(
        self, original: Path, edited: Path, export: Path, gold: GoldWindows, history: EditHistory
    ) -> None:
        self.original = original
        self.edited = edited
        self.source = edited if edited.exists() else original
        self.gold = gold
        self.history = history
        payload = json.loads(self.source.read_text(encoding="utf-8"))  # FileNotFoundError → 409
        self.problem = format_problem(self.source, payload)
        self.payload: dict[str, Any] = payload if self.problem is None else {"threads": []}
        self.chat = ChatIndex(self.threads, export_rank(export) if self.problem is None else {})
        self._views: ThreadViews | None = None
        self._backed_up = False

    @property
    def threads(self) -> list[dict[str, Any]]:
        return self.payload["threads"]

    @property
    def views(self) -> ThreadViews:
        self._require_current_format()
        if self._views is None:
            self._views = ThreadViews(self.threads, self.chat, self.gold)
        return self._views

    def summary(self) -> dict[str, Any]:
        """What the page needs before anything else: the file's state and the gold windows."""
        base = {
            "state": "old_format" if self.problem else "ready",
            "problem": self.problem,
            "file": self.source.name,
            "edited": self.source == self.edited,
            "undo": None,
            "windows": [],
            "thread_count": len(self.threads),
            "message_count": self.chat.size,
            "quiet_minutes": THREADS_CONFIG["split"]["quiet_minutes"],
            "context_step": THREADS_CONFIG["review"]["context_step"],
        }
        if self.problem is None:
            latest = self.history.latest(self._stamp())
            base["undo"] = {"label": latest.label, "at": latest.at} if latest else None
            base["windows"] = self.gold.summaries(self.threads)
        return base

    # ------------------------------------------------------------------ edits
    def editor(self) -> ThreadEditor:
        self._require_current_format()
        return ThreadEditor(self.threads, self.chat.seq)

    def apply(self, edit: Edit) -> dict[str, Any]:
        """Save the edit's threads and remember how to take it back."""
        self._save(edit.threads)
        self.history.record(
            UndoEntry(edit.label, edit.focus, local_now(), edit.before, edit.after_ids, self._stamp())
        )
        logger.info(f"Conversations tool: {edit.label}; saved {self.edited}")
        return {
            "label": edit.label,
            "focus": edit.focus,
            "created": edit.created,
            "removed": list(edit.removed),
            "summary": self.summary(),
        }

    def undo(self) -> dict[str, Any]:
        """Put back the threads as they were before the newest edit of this session."""
        latest = self.history.latest(self._stamp())
        if latest is None:
            raise EditRefused(
                "There is nothing to undo: no change was made in this tool since the server "
                "started, or the conversations file changed outside the tool (for example a "
                "step 1 run), so undoing is no longer safe."
            )
        self._save(self.editor().restore(latest.before, latest.after_ids))
        self.history.pop(self._stamp())
        logger.info(f"Conversations tool: undid {latest.label}")
        focus = latest.before[0]["thread_id"] if latest.before else latest.focus
        return {"label": latest.label, "focus": focus, "summary": self.summary()}

    # ---------------------------------------------------------------- helpers
    def _require_current_format(self) -> None:
        if self.problem is not None:
            raise OldFormat(self.problem)

    def _stamp(self) -> FileStamp | None:
        return stamp(self.edited)

    def _save(self, threads: list[dict[str, Any]]) -> None:
        if not self._backed_up:
            backup_file(self.edited)  # nothing to back up before the first edit ever
            self._backed_up = True
        metadata = {
            **self.payload.get("metadata", {}),
            "edited_by": EDITED_BY,
            "edited_at": local_now(),
            "thread_count": len(threads),
        }
        payload = {**self.payload, "threads": threads, "metadata": metadata}
        write_json_file(payload, self.edited)
        self.payload = payload
        self.source = self.edited
        self.chat.locate(threads)
        self._views = None
