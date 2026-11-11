"""Gold windows and the frozen gold file for thread splitting.

A gold window is a half-open span ``[start, end)`` of naive local datetimes listed in
``config.json`` ``threads.gold_windows``. Listing a window asserts that every message
sent inside it sits in the correct thread of ``threads_edited.json``. Threads may extend
outside the window; nothing outside it is asserted.

``freeze_gold`` copies that assertion into ``gold_threads.json``::

    {"windows": [{"start", "end", "frozen_at", "message_ids": [<wa_id>, ...]}],
     "messages": {<wa_id>: <cluster label>},
     "metadata": {"frozen_at", "source", "message_count", "cluster_count"}}

Everything is keyed on the WhatsApp message id (``messages[].id``), never on thread ids
or positional ``message_ids``, so the file stays valid when step 1 is re-run, when the
thread id format changes and when ``threads_edited.json`` no longer exists. A cluster
label is the WhatsApp id of the cluster's first message inside its window, so a cluster
never spans two windows.

``WindowSuggester`` proposes which windows to tag.
"""

from __future__ import annotations

import bisect
import json
import logging
import random
import shutil
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from utils.json_io import write_json_file

logger = logging.getLogger(__name__)


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


def message_moment(message: dict[str, Any]) -> datetime:
    return datetime.fromisoformat(message["datetime"])


# ------------------------------------------------------------------ gold file
def label_windows(
    threads: list[dict[str, Any]], window_set: WindowSet, frozen_at: str
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Return the gold-file window entries and ``{wa_id: cluster label}`` for ``threads``."""
    per_window: list[list[tuple[datetime, int, int, str]]] = [[] for _ in window_set.windows]
    for thread_index, thread in enumerate(threads):
        for position, message in enumerate(thread["messages"]):
            moment = message_moment(message)
            window_index = window_set.index_of(moment)
            if window_index is None:
                continue
            wa_id = message.get("id")
            if not wa_id:
                raise ValueError(
                    f"message at {message['datetime']} in thread {thread.get('thread_id')!r} "
                    "has no WhatsApp id; gold labels are keyed on messages[].id"
                )
            per_window[window_index].append((moment, thread_index, position, wa_id))

    entries: list[dict[str, Any]] = []
    labels: dict[str, str] = {}
    for window, rows in zip(window_set.windows, per_window):
        if not rows:
            raise ValueError(f"gold window {window} contains no messages; check its dates")
        rows.sort()
        first_of_thread: dict[int, str] = {}
        for _, thread_index, _, wa_id in rows:
            if wa_id in labels:
                raise ValueError(f"message {wa_id} appears more than once in the tagged threads")
            labels[wa_id] = first_of_thread.setdefault(thread_index, wa_id)
        entries.append(
            {
                **window.to_dict(),
                "frozen_at": frozen_at,
                "message_ids": [wa_id for _, _, _, wa_id in rows],
            }
        )
    return entries, labels


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


def freeze_gold(
    threads: list[dict[str, Any]],
    windows: Sequence[Window],
    gold_path: Path,
    backup_dir: Path,
    source: str,
    *,
    add: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """Write the gold file for ``windows`` from the tagged ``threads`` and return it.

    An existing file is manual work: without ``add`` or ``force`` this refuses to touch
    it. ``add`` keeps every window that is already frozen exactly as it is and appends
    only the windows the file does not have yet. ``force`` rebuilds the whole file. The
    previous file is copied to ``backup_dir`` before either rewrite.
    """
    if not windows:
        raise ValueError("no gold windows given; fill config.json threads.gold_windows first")
    existing: dict[str, Any] | None = None
    if gold_path.exists():
        if add:
            existing = load_gold(gold_path)
        elif not force:
            raise FileExistsError(
                f"{gold_path} already exists and holds manual tagging work. Use --add to "
                "merge new windows into it, or --force to rebuild it from scratch."
            )
    kept = existing["windows"] if existing else []
    if existing and "context_messages" in existing:
        # The context part depends on every window; it is rebuilt with --add-context.
        kept = [{k: v for k, v in w.items() if k != "context_message_ids"} for w in kept]
        logger.warning(f"{gold_path}: the context part is dropped; run --add-context again")
    kept_spans = [Window.from_dict(window) for window in kept]
    fresh_spans = WindowSet(window for window in windows if window not in kept_spans)
    # Raises when a new window overlaps one that is already frozen.
    WindowSet([*kept_spans, *fresh_spans.windows])
    if existing and not fresh_spans.windows:
        logger.info(f"{gold_path}: every configured window is already frozen; nothing to add")
        return existing

    frozen_at = datetime.now().isoformat(timespec="seconds")
    fresh, labels = label_windows(threads, fresh_spans, frozen_at)
    if existing:
        clash = existing["messages"].keys() & labels.keys()
        if clash:
            raise ValueError(
                f"{len(clash)} messages of the new windows are already labelled in "
                f"{gold_path}, e.g. {sorted(clash)[:3]}"
            )
        labels.update(existing["messages"])
    gold_windows = sorted([*kept, *fresh], key=lambda window: window["start"])
    messages = {wa_id: labels[wa_id] for window in gold_windows for wa_id in window["message_ids"]}
    gold = {
        "windows": gold_windows,
        "messages": messages,
        "metadata": {
            "frozen_at": frozen_at,
            "source": source,
            "message_count": len(messages),
            "cluster_count": len(set(messages.values())),
        },
    }
    if gold_path.exists():
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        backup_path = backup_dir / f"{gold_path.stem}_{stamp}{gold_path.suffix}"
        shutil.copy2(gold_path, backup_path)
        logger.info(f"previous gold file copied to {backup_path}")
    write_json_file(gold, gold_path)
    logger.info(
        f"froze {len(fresh)} window(s) into {gold_path}: {len(messages)} messages in "
        f"{gold['metadata']['cluster_count']} gold threads over {len(gold_windows)} window(s)"
    )
    return gold


# ------------------------------------------------------------------ suggester
class WindowSuggester:
    """Proposes gold windows from a predicted thread split.

    A candidate starts at the top of an hour in which a message was sent, lasts ``hours``
    and holds between ``min_messages`` and ``max_messages`` messages. A predicted thread
    is active in a window when it has a message pair there (pairs are what the metrics
    score); candidates with at least ``min_threads`` active threads are preferred. One
    candidate is drawn per equal slice of the timeline so the windows cover the whole
    chat history.
    """

    def __init__(
        self,
        threads: list[dict[str, Any]],
        hours: float,
        min_messages: int,
        max_messages: int,
        min_threads: int,
        exclude: Sequence[Window] = (),
    ) -> None:
        self.threads = threads
        self.span = timedelta(hours=hours)
        self.min_messages = min_messages
        self.max_messages = max_messages
        self.min_threads = min_threads
        self.exclude = exclude
        self.timeline = sorted(
            (message_moment(message), thread_index)
            for thread_index, thread in enumerate(threads)
            for message in thread["messages"]
        )
        self.moments = [moment for moment, _ in self.timeline]

    def _thread_indices(self, window: Window) -> list[int]:
        low = bisect.bisect_left(self.moments, window.start)
        high = bisect.bisect_left(self.moments, window.end)
        return [thread_index for _, thread_index in self.timeline[low:high]]

    def candidates(self) -> dict[Window, int]:
        """Eligible windows in time order, each with its number of active threads."""
        full_hours = {moment.replace(minute=0, second=0, microsecond=0) for moment in self.moments}
        result: dict[Window, int] = {}
        for start in sorted(full_hours):
            window = Window(start, start + self.span)
            thread_indices = self._thread_indices(window)
            if not self.min_messages <= len(thread_indices) <= self.max_messages:
                continue
            if any(window.overlaps(taken) for taken in self.exclude):
                continue
            in_window = Counter(thread_indices)
            result[window] = sum(1 for count in in_window.values() if count > 1)
        return result

    def describe(self, window: Window) -> dict[str, Any]:
        """A window with its message count and the predicted threads that overlap it."""
        in_window = Counter(self._thread_indices(window))
        overlapping = []
        for thread_index, count in in_window.items():
            thread = self.threads[thread_index]
            overlapping.append(
                {
                    "thread_id": thread.get("thread_id"),
                    "start_time": min(message["datetime"] for message in thread["messages"]),
                    "messages_in_window": count,
                    "num_messages": len(thread["messages"]),
                }
            )
        overlapping.sort(key=lambda item: item["start_time"])
        return {
            **window.to_dict(),
            "message_count": sum(in_window.values()),
            "thread_count": len(in_window),
            "threads": overlapping,
        }

    def suggest(self, count: int, seed: int) -> list[dict[str, Any]]:
        """Up to ``count`` non-overlapping windows, deterministic for ``seed``."""
        if count < 1:
            raise ValueError(f"cannot suggest {count} windows; ask for at least one")
        candidates = self.candidates()
        if not candidates:
            return []
        rng = random.Random(seed)
        first = self.moments[0]
        slice_seconds = (self.moments[-1] - first).total_seconds() / count
        slices: list[list[Window]] = [[] for _ in range(count)]
        for window in candidates:
            offset = (window.start - first).total_seconds()
            index = int(offset / slice_seconds) if slice_seconds else 0
            slices[min(count - 1, max(0, index))].append(window)

        chosen: list[Window] = []

        def draw(pool: Iterable[Window]) -> bool:
            free = [w for w in pool if not any(w.overlaps(taken) for taken in chosen)]
            preferred = [w for w in free if candidates[w] >= self.min_threads]
            if not free:
                return False
            chosen.append(rng.choice(preferred or free))
            return True

        for pool in slices:
            draw(pool)
        while len(chosen) < count and draw(candidates):
            pass
        return [self.describe(window) for window in sorted(chosen)]
