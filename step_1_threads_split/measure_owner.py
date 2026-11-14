"""The owner's conversations in the measure: corrections, the weaker set, folds, true order.

``step_1_threads_split.measure`` scores every split also on the conversations the owner
tagged as whole conversations (``OwnerConversations``):

- **corrected**: the gold file's ``conversations`` (``gold_conversations``): created or
  changed by the owner's edits, each asserted complete. The main number.
- **weaker** (only with ``--reviewed``): conversations the owner went through and left as they
  were: step 2 had marked them not knowledge-bearing on step 1's own split (the classified
  file given) and they have the same messages in the edited file. Reported apart.

Both are scored with B-cubed (``gold_conversations.conversation_counts``). The corrections were
made on the current rules' mistakes, so they favour any other rule set on those spots, and the
weaker set was produced by the current rules, so it favours them. Parameters are therefore
chosen on some of the conversations and scored on the others: the conversations (both sets
together) are cut into ``threads.measure.conversation_folds`` folds by time, and each fold is
scored with the grid row that is best on the other folds (pooled B-cubed F1; ties go to the
earlier grid row).

``changed_outside`` counts how many conversations of a reference split (step 1's own file)
that hold no tagged message a split does not reproduce exactly. ``true_order`` reorders the
messages inside each minute by the phone database's order (``message_times.json``:
``messages[wa_id] = {timestamp_ms, rank}``), a research input only: the export carries times
in minutes and its order inside a minute is not the real one.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from step_1_threads_split.evaluate import markdown_table
from step_1_threads_split.gold_conversations import (
    as_conversation,
    conversation_counts,
    pooled_bcubed,
)
from utils.config import section

MEASURE_CONFIG = section("threads")["measure"]
Rows = list[tuple[float, float, int]]


def reviewed_unchanged(
    original: Sequence[dict[str, Any]],
    edited: Sequence[dict[str, Any]],
    classified: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """The weaker set: not knowledge-bearing in ``classified`` and unchanged in ``edited``."""
    reviewed = {c["thread_id"] for c in classified if c.get("is_knowledge_bearing") is False}
    unchanged = {frozenset(m["id"] for m in t["messages"]) for t in edited}
    found = [
        as_conversation(thread)
        for thread in original
        if thread["thread_id"] in reviewed
        and frozenset(m["id"] for m in thread["messages"]) in unchanged
    ]
    return sorted(found, key=lambda c: (c["start"], c["thread_id"]))


def true_order(messages: Sequence[dict[str, Any]], times_path: Path) -> tuple[list[dict[str, Any]], set[str]]:
    """The messages with each minute in the database's order, and the ids the file covers.

    Messages the file does not cover keep their export place after the covered ones of
    their minute.
    """
    times = json.loads(times_path.read_text(encoding="utf-8"))["messages"]
    position = {m["id"]: index for index, m in enumerate(messages)}

    def key(message: dict[str, Any]) -> tuple[str, int, int]:
        known = times.get(message["id"])
        rank = known["rank"] if known else len(position) + position[message["id"]]
        return message["datetime"], rank, position[message["id"]]

    return sorted(messages, key=key), set(times) & set(position)


def changed_outside(
    reference: Sequence[dict[str, Any]], threads: Sequence[dict[str, Any]], tagged: set[str]
) -> int:
    """Reference conversations without a tagged message that ``threads`` does not reproduce."""
    produced = {frozenset(m["id"] for m in t["messages"]) for t in threads}
    return sum(
        1
        for thread in reference
        if not tagged & {m["id"] for m in thread["messages"]}
        and frozenset(m["id"] for m in thread["messages"]) not in produced
    )


class OwnerConversations:
    """The corrected and the weaker conversations, their folds and their scores."""

    def __init__(
        self, corrected: Sequence[dict[str, Any]], weaker: Sequence[dict[str, Any]]
    ) -> None:
        self.corrected = list(corrected)
        self.weaker = list(weaker)
        both = sorted(
            [(c["start"], "corrected", i) for i, c in enumerate(self.corrected)]
            + [(c["start"], "weaker", i) for i, c in enumerate(self.weaker)]
        )
        count = min(int(MEASURE_CONFIG["conversation_folds"]), len(both))
        self.folds = [both[len(both) * k // count : len(both) * (k + 1) // count] for k in range(count)]

    def ids(self) -> set[str]:
        return {wa_id for c in [*self.corrected, *self.weaker] for wa_id in c["message_ids"]}

    def covered(self, ids: set[str]) -> OwnerConversations:
        """Only the conversations whose messages are all in ``ids`` (folds rebuilt)."""
        return OwnerConversations(
            [c for c in self.corrected if set(c["message_ids"]) <= ids],
            [c for c in self.weaker if set(c["message_ids"]) <= ids],
        )

    def counts(self, threads: Sequence[dict[str, Any]]) -> dict[str, Rows]:
        """Per-conversation B-cubed rows of ``threads`` for both sets."""
        return {
            "corrected": conversation_counts(threads, self.corrected),
            "weaker": conversation_counts(threads, self.weaker),
        }

    def pooled(self, counts: dict[str, Rows], folds: Sequence[int] | None = None) -> dict[str, Any]:
        """Pooled B-cubed of the corrected, weaker and both sets (over ``folds`` if given)."""
        chosen = range(len(self.folds)) if folds is None else folds
        picked: dict[str, Rows] = {"corrected": [], "weaker": []}
        for fold in chosen:
            for _, part, index in self.folds[fold]:
                picked[part].append(counts[part][index])
        return {
            "corrected": pooled_bcubed(picked["corrected"]),
            "weaker": pooled_bcubed(picked["weaker"]),
            "all": pooled_bcubed([*picked["corrected"], *picked["weaker"]]),
        }

    def held_out(self, grid_counts: Sequence[dict[str, Rows]]) -> dict[str, Any]:
        """Each fold scored with the grid row that is best on the other folds."""
        folds = []
        held: dict[str, Rows] = {"corrected": [], "weaker": []}
        for fold in range(len(self.folds)):
            train = [k for k in range(len(self.folds)) if k != fold]
            row = max(
                range(len(grid_counts)),
                key=lambda r: (round(self.pooled(grid_counts[r], train)["all"]["bcubed_f1"], 9), -r),
            )
            for _, part, index in self.folds[fold]:
                held[part].append(grid_counts[row][part][index])
            folds.append(
                {
                    "first": self.folds[fold][0][0] if self.folds[fold] else None,
                    "conversations": len(self.folds[fold]),
                    "row": row,
                    "held_out": self.pooled(grid_counts[row], [fold]),
                }
            )
        return {
            "folds": folds,
            "corrected": pooled_bcubed(held["corrected"]),
            "weaker": pooled_bcubed(held["weaker"]),
            "all": pooled_bcubed([*held["corrected"], *held["weaker"]]),
        }


SUMMARY_HEADER = ["split", "corrected B3 P", "corrected B3 R", "corrected B3 F1", "weaker B3 F1",
                  "all B3 F1", "gold window F1", "gold +ctx F1", "changed outside"]  # fmt: skip


def summary_row(owner: OwnerConversations, name: str, scores: dict[str, Any]) -> list[str]:
    """One row of ``SUMMARY_HEADER`` for a split's ``Measurement.scores``."""
    pooled = owner.pooled(scores["owner"])
    corrected = pooled["corrected"]
    return [name, *(f"{corrected[k]:.3f}" for k in ("bcubed_precision", "bcubed_recall", "bcubed_f1")),
            f"{pooled['weaker']['bcubed_f1']:.3f}", f"{pooled['all']['bcubed_f1']:.3f}",
            f"{scores['window']['micro']['bcubed_f1']:.3f}",
            f"{scores['context']['micro']['bcubed_f1']:.3f}", str(scores["changed_outside"])]  # fmt: skip


def owner_section(
    owner: OwnerConversations,
    grid: Sequence[tuple[dict[str, Any], dict[str, Any]]],
    named: Sequence[tuple[str, dict[str, Any]]],
) -> tuple[list[str], dict[str, Any]]:
    """The report's tables on the owner's conversations and their numbers.

    ``grid`` is ``(params, scores)`` per grid row, ``named`` further ``(name, scores)`` rows
    (the old splitter, the configured parameters).
    """
    grid_counts = [scores["owner"] for _, scores in grid]
    best = max(range(len(grid)), key=lambda r: (round(owner.pooled(grid_counts[r])["all"]["bcubed_f1"], 9), -r))
    held = owner.held_out(grid_counts)
    rows = [summary_row(owner, name, scores) for name, scores in named]
    rows.append(summary_row(owner, f"best on all: {json.dumps(grid[best][0])}", grid[best][1]))
    fold_rows = [
        [fold["first"], fold["conversations"], json.dumps(grid[fold["row"]][0]),
         *(f"{fold['held_out'][part]['bcubed_f1']:.3f}" for part in ("corrected", "weaker", "all"))]
        for fold in held["folds"]
    ]  # fmt: skip
    parts = [
        f"owner's conversations: {len(owner.corrected)} corrected (asserted complete), "
        f"{len(owner.weaker)} reviewed and left as they were (weaker); changed outside = "
        "conversations of the reference split without a tagged message that the split does not "
        "reproduce",
        markdown_table(SUMMARY_HEADER, rows),
        markdown_table(["fold starts", "conversations", "params chosen on the other folds",
                        "held-out corrected F1", "held-out weaker F1", "held-out all F1"], fold_rows),
        f"held-out B3 F1 over all folds: corrected {held['corrected']['bcubed_f1']:.3f}, weaker "
        f"{held['weaker']['bcubed_f1']:.3f}, all {held['all']['bcubed_f1']:.3f}",
    ]  # fmt: skip
    data = {
        "rows": [dict(zip(SUMMARY_HEADER, row)) for row in rows],
        "held_out": {**held, "folds": [{**f, "params": grid[f["row"]][0]} for f in held["folds"]]},
    }
    return parts, data
