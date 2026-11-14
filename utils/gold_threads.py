"""The frozen gold file of thread splitting (``gold_threads.json``), as its readers see it.

A gold window is a half-open span ``[start, end)`` of naive local datetimes; the gold file
lists the frozen windows, each with the WhatsApp ids of its messages, and labels every
message with its cluster (step 1 freezes it: ``step_1_threads_split.gold``; the context part
is added by ``step_1_threads_split.gold_context``). ``load_gold`` reads and validates the
file; ``scored_ids`` and ``all_labels`` are what a score reads from it (step 1's evaluation,
step 3's split experiment).
"""

from __future__ import annotations

import bisect
import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True, order=True)
class Window:
    """Half-open time span ``[start, end)`` of naive local datetimes."""

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.start >= self.end:
            raise ValueError(f"gold window must end after it starts: {self}")

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Window:
        try:
            start = datetime.fromisoformat(raw["start"])
            end = datetime.fromisoformat(raw["end"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                f"bad gold window {raw!r}: expected "
                "{'start': 'YYYY-MM-DDTHH:MM', 'end': 'YYYY-MM-DDTHH:MM'}"
            ) from exc
        return cls(start, end)

    def to_dict(self) -> dict[str, str]:
        return {
            "start": self.start.isoformat(timespec="minutes"),
            "end": self.end.isoformat(timespec="minutes"),
        }

    def __str__(self) -> str:
        span = self.to_dict()
        return f"{span['start']} → {span['end']}"

    def contains(self, moment: datetime) -> bool:
        return self.start <= moment < self.end

    def overlaps(self, other: Window) -> bool:
        return self.start < other.end and other.start < self.end


class WindowSet:
    """Sorted, non-overlapping windows with a "which window holds this moment" lookup."""

    def __init__(self, windows: Iterable[Window]) -> None:
        self.windows = sorted(windows)
        for earlier, later in zip(self.windows, self.windows[1:]):
            if earlier.overlaps(later):
                raise ValueError(f"gold windows overlap: {earlier} and {later}")
        self._starts = [window.start for window in self.windows]

    def index_of(self, moment: datetime) -> int | None:
        index = bisect.bisect_right(self._starts, moment) - 1
        if index >= 0 and self.windows[index].contains(moment):
            return index
        return None


def load_gold(gold_path: Path) -> dict[str, Any]:
    """Read and validate the frozen gold file."""
    if not gold_path.is_file():
        raise FileNotFoundError(
            f"gold file {gold_path} not found; tag the gold windows, then run "
            "`python -m step_1_threads_split.evaluate --freeze-gold`"
        )
    gold = json.loads(gold_path.read_text(encoding="utf-8"))
    WindowSet(Window.from_dict(window) for window in gold["windows"])
    listed = [wa_id for window in gold["windows"] for wa_id in window["message_ids"]]
    if len(listed) != len(set(listed)) or set(listed) != set(gold["messages"]):
        raise ValueError(
            f"{gold_path} is inconsistent: its windows list {len(listed)} message ids "
            f"({len(set(listed))} distinct) but {len(gold['messages'])} messages are labelled"
        )
    if "context_messages" in gold:
        context = [wa_id for window in gold["windows"] for wa_id in window["context_message_ids"]]
        if (
            len(context) != len(set(context))
            or set(context) != set(gold["context_messages"])
            or set(context) & set(listed)
        ):
            raise ValueError(
                f"{gold_path} has an inconsistent context part: its windows list "
                f"{len(context)} context ids ({len(set(context))} distinct) but "
                f"{len(gold['context_messages'])} context messages are labelled"
            )
    return gold


def scored_ids(gold: dict[str, Any], window: dict[str, Any], with_context: bool) -> list[str]:
    """The ids a window scores: its own messages, plus its context when asked."""
    if not with_context:
        return list(window["message_ids"])
    if "context_message_ids" not in window:
        raise ValueError("the gold file has no context part; run --add-context first")
    return [*window["message_ids"], *window["context_message_ids"]]


def all_labels(gold: dict[str, Any]) -> dict[str, str]:
    return {**gold["messages"], **gold.get("context_messages", {})}
