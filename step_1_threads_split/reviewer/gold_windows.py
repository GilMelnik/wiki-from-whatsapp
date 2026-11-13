"""Gold-window mode of the conversations tool: which threads a window holds, which are done.

The windows are ``config.json`` ``threads.gold_windows`` (see ``step_1_threads_split.gold``),
indexed in chronological order. Window membership is computed live from the threads the
tool currently holds, so a thread that was just split off shows up at once.

The "done" flags live in ``gold_progress.json``::

    {"windows": [{"start", "end", "done", "updated_at"}], "metadata": {"updated_at"}}

An entry is identified by its window's start and end, never by its index, so re-ordering or
trimming the config cannot move a flag to another window. Entries of windows that are no
longer configured are kept.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from step_1_threads_split.gold import Window, WindowSet, message_moment
from utils.config import section
from utils.json_io import write_json_file
from utils.paths import GOLD_PROGRESS_PATH
from utils.timestamps import local_now

THREADS_CONFIG = section("threads")


class GoldProgress:
    """Read and atomically rewrite the per-window "done" flags in ``path``."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def _entries(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            entries = payload["windows"]
            for entry in entries:
                Window.from_dict(entry)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                f"{self.path} is not a gold progress file "
                '({"windows": [{"start", "end", "done", "updated_at"}]}): ' + str(exc)
            ) from exc
        return entries

    def done_windows(self) -> set[Window]:
        return {Window.from_dict(entry) for entry in self._entries() if entry.get("done")}

    def set_done(self, window: Window, done: bool) -> None:
        with self._lock:
            entries = self._entries()
            now = local_now()
            entry = next((e for e in entries if Window.from_dict(e) == window), None)
            if entry is None:
                entry = window.to_dict()
                entries.append(entry)
            entry.update(done=done, updated_at=now)
            entries.sort(key=lambda e: (e["start"], e["end"]))
            write_json_file({"windows": entries, "metadata": {"updated_at": now}}, self.path)


class GoldWindows:
    """The configured gold windows, measured against a list of threads."""

    def __init__(self, windows: Sequence[Window], progress: GoldProgress) -> None:
        self.window_set = WindowSet(windows)
        self.progress = progress

    @classmethod
    def from_config(cls) -> GoldWindows:
        windows = [Window.from_dict(raw) for raw in THREADS_CONFIG["gold_windows"]]
        return cls(windows, GoldProgress(GOLD_PROGRESS_PATH))

    @property
    def windows(self) -> list[Window]:
        return self.window_set.windows

    def window(self, index: int) -> Window:
        if not 0 <= index < len(self.windows):
            raise IndexError(
                f"gold window {index} does not exist; {len(self.windows)} windows are "
                "configured in config.json threads.gold_windows"
            )
        return self.windows[index]

    def members(self, threads: list[dict[str, Any]]) -> list[list[tuple[str, int]]]:
        """Per window: ``(thread_id, messages inside)`` ordered by first in-window message."""
        per_window: list[dict[str, list[Any]]] = [{} for _ in self.windows]
        for thread in threads:
            for message in thread["messages"]:
                moment = message_moment(message)
                index = self.window_set.index_of(moment)
                if index is None:
                    continue
                entry = per_window[index].setdefault(thread["thread_id"], [moment, 0])
                entry[0] = min(entry[0], moment)
                entry[1] += 1
        return [
            [
                (thread_id, count)
                for thread_id, (_, count) in sorted(
                    found.items(), key=lambda item: (item[1][0], item[0])
                )
            ]
            for found in per_window
        ]

    def summaries(self, threads: list[dict[str, Any]]) -> list[dict[str, Any]]:
        done = self.progress.done_windows()
        return [
            {
                "index": index,
                **window.to_dict(),
                "message_count": sum(count for _, count in found),
                "thread_ids": [thread_id for thread_id, _ in found],
                "done": window in done,
            }
            for index, (window, found) in enumerate(zip(self.windows, self.members(threads)))
        ]

    def in_window_ids(self, threads: Sequence[dict[str, Any]], index: int) -> list[str]:
        """WhatsApp ids of the messages of ``threads`` that lie inside window ``index``."""
        window = self.window(index)
        return [
            message["id"]
            for thread in threads
            for message in thread["messages"]
            if message.get("id") and window.contains(message_moment(message))
        ]

    def set_done(self, index: int, done: bool) -> None:
        self.progress.set_done(self.window(index), done)
