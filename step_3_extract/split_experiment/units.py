"""The units of the split experiment (task 52): the same chat messages cut five ways.

The messages ("the set") are those of the gold file with its context part
(``step_1_threads_split.gold_context``): per gold window, the window's own messages and the
context messages the owner tagged with it. A unit is what one model call reads; it is made of
whole chat messages and belongs to one window. Per window, in chat order:

- A, new split: the new splitter's threads (``threads.json``) that hold a message of the set,
  each cut to the set;
- B, the owner's conversations: the gold labels (the reference);
- C, old split: the old splitter's threads (``threads_baseline_old_algorithm.json``), cut to
  the set;
- D, new split + background: A's units, each shown after the ``context_messages`` chat
  messages right before its first message, as read-only background;
- E, packed: A's units, several per call while their rendered text stays within
  ``pack_max_chars`` (a longer unit goes alone).

Every variant covers each message of the set exactly once (``check_cover`` raises otherwise),
so the variants differ only in how the messages are grouped. Unit ids are
``<variant><window number>-<unit number>`` (``A3-02``); D keeps the number of its A unit.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from step_1_threads_split.gold_context import all_labels, scored_ids
from utils.config import section
from utils.threads_io import render_thread_for_llm, with_export

EXPERIMENT_CONFIG = section("extract")["split_experiment"]
VARIANTS = tuple(EXPERIMENT_CONFIG["variants"])


@dataclass(frozen=True)
class Unit:
    """What one model call reads. ``conversations`` holds the message ids it may cite, per
    conversation (several only when packed); ``background`` the read-only messages shown
    before them. ``source`` is the thread id or gold label it comes from (packed: the units)."""

    unit_id: str
    variant: str
    window: int
    source: str
    conversations: tuple[tuple[str, ...], ...]
    background: tuple[str, ...] = ()

    @property
    def message_ids(self) -> tuple[str, ...]:
        return tuple(wa_id for conversation in self.conversations for wa_id in conversation)

    def to_json(self) -> dict[str, Any]:
        return {"unit_id": self.unit_id, "variant": self.variant, "window": self.window,
                "source": self.source, "conversations": [list(c) for c in self.conversations],
                "background": list(self.background)}


class MessageSet:
    """The chat in order, the set's message ids per window (chat order), the owner's labels.

    ``chat`` is every chat message with content as a C1 record, in chat order
    (``step_1_threads_split.run.load_export_messages``); ``windows`` limits the set to these
    window indices (0-based).
    """

    def __init__(self, gold: dict[str, Any], chat: Sequence[dict[str, Any]],
                 windows: Collection[int] | None = None) -> None:
        self.records = {str(m["id"]): m for m in chat}
        self.chat_order = [str(m["id"]) for m in chat]
        self.position = {wa_id: index for index, wa_id in enumerate(self.chat_order)}
        self.labels = all_labels(gold)
        self.spans = [(w["start"], w["end"]) for w in gold["windows"]]
        chosen = range(len(gold["windows"])) if windows is None else sorted(windows)
        unknown = [w for w in chosen if not 0 <= w < len(gold["windows"])]
        if unknown:
            raise ValueError(f"no gold window {[w + 1 for w in unknown]}; the gold file has "
                             f"{len(gold['windows'])} (numbered from 1)")
        self.ids: dict[int, list[str]] = {}
        for window in chosen:
            ids = scored_ids(gold, gold["windows"][window], with_context=True)
            missing = [wa_id for wa_id in ids if wa_id not in self.position]
            if missing:
                raise ValueError(f"{len(missing)} messages of gold window {window + 1} are not in "
                                 f"the chat export, e.g. {missing[:3]}: is it the same chat?")
            self.ids[window] = sorted(ids, key=self.position.__getitem__)

    @property
    def windows(self) -> list[int]:
        return list(self.ids)

    def size(self) -> int:
        return sum(len(ids) for ids in self.ids.values())

    def background(self, first: str, count: int) -> tuple[str, ...]:
        """The ``count`` chat messages right before ``first``."""
        at = self.position[first]
        return tuple(self.chat_order[max(0, at - count):at])

    def thread(self, thread_id: str, ids: Sequence[str]) -> dict[str, Any]:
        """A thread of these messages, quotes and reactions normalized as step 3 reads them."""
        return with_export({"thread_id": thread_id, "messages": [self.records[i] for i in ids]},
                           None)

    def rendered_chars(self, ids: Sequence[str]) -> int:
        return len(render_thread_for_llm(self.thread("", ids))[0])


def thread_owner(threads: Sequence[dict[str, Any]]) -> dict[str, str]:
    """WhatsApp id -> id of the thread that holds it."""
    return {str(m["id"]): t["thread_id"] for t in threads for m in t["messages"]}


def _unit_id(variant: str, window: int, number: int) -> str:
    return f"{variant}{window + 1}-{number:02d}"


def cut(variant: str, owner: Mapping[str, str], messages: MessageSet) -> list[Unit]:
    """One unit per group of ``owner`` (message -> thread or label) within each window."""
    units: list[Unit] = []
    for window in messages.windows:
        groups: dict[str, list[str]] = {}
        for wa_id in messages.ids[window]:
            if wa_id not in owner:
                raise ValueError(f"variant {variant}: message {wa_id} of gold window "
                                 f"{window + 1} is in no thread: is it the split of the same chat?")
            groups.setdefault(owner[wa_id], []).append(wa_id)
        units += [Unit(_unit_id(variant, window, number), variant, window, source, (tuple(ids),))
                  for number, (source, ids) in enumerate(groups.items(), start=1)]
    return units


def with_background(units: Sequence[Unit], messages: MessageSet, count: int) -> list[Unit]:
    """D: each unit with the ``count`` chat messages before its first one as background."""
    return [Unit(f"D{unit.unit_id[1:]}", "D", unit.window, unit.source, unit.conversations,
                 messages.background(unit.message_ids[0], count)) for unit in units]


def pack(units: Sequence[Unit], messages: MessageSet, max_chars: int) -> list[Unit]:
    """E: per window, consecutive units in one call while their rendered text fits."""
    packs: list[Unit] = []
    for window in messages.windows:
        groups: list[list[Unit]] = []
        size = 0
        for unit in (u for u in units if u.window == window):
            chars = messages.rendered_chars(unit.message_ids)
            if not groups or size + chars > max_chars:
                groups.append([])
                size = 0
            groups[-1].append(unit)
            size += chars
        packs += [Unit(_unit_id("E", window, number), "E", window,
                       " ".join(u.unit_id for u in group),
                       tuple(u.message_ids for u in group))
                  for number, group in enumerate(groups, start=1)]
    return packs


def check_cover(units: Sequence[Unit], messages: MessageSet) -> None:
    """Raise unless the units hold every message of the set exactly once, window by window."""
    for window in messages.windows:
        held = Counter(i for u in units if u.window == window for i in u.message_ids)
        expected = Counter(messages.ids[window])
        if held != expected:
            extra, lost = held - expected, expected - held
            raise ValueError(f"the units of window {window + 1} do not cover its messages once: "
                             f"{sum(extra.values())} extra, {sum(lost.values())} missing")


def build_units(messages: MessageSet, new_split: Sequence[dict[str, Any]],
                old_split: Sequence[dict[str, Any]],
                variants: Sequence[str] = VARIANTS) -> dict[str, list[Unit]]:
    """The units of the requested variants (all five are built and checked)."""
    new_units = cut("A", thread_owner(new_split), messages)
    built = {
        "A": new_units,
        "B": cut("B", messages.labels, messages),
        "C": cut("C", thread_owner(old_split), messages),
        "D": with_background(new_units, messages, int(EXPERIMENT_CONFIG["context_messages"])),
        "E": pack(new_units, messages, int(EXPERIMENT_CONFIG["pack_max_chars"])),
    }
    for units in built.values():
        check_cover(units, messages)
    return {variant: built[variant] for variant in variants}
