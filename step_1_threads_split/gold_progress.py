"""The gold-window "done" flags of the conversations tool, in ``gold_progress.json``::

    {"windows": [{"start", "end", "done", "updated_at"}], "metadata": {"updated_at"}}

An entry is identified by its window's start and end, never by its index, so re-ordering or
trimming the config cannot move a flag to another window. Entries of windows that are no
longer configured are kept. The tool sets the flags (``review_tool.tools.threads``); step 1's
evaluation reads which windows are done.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from utils.gold_threads import Window
from utils.json_io import write_json_file
from utils.timestamps import local_now


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
