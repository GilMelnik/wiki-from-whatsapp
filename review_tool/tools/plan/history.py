"""Undo for the plan tool: the changes of this session, over the plan and the assignments.

A change writes the plan, the assignments, or both (accepting a proposal). Its entry keeps
what is needed to put back what it changed (the whole previous plan; the previous records
and proposals of the assignments) and what both files looked like right after it. An entry
is offered only while BOTH files are still exactly what the change left: a re-plan, an
assignment run or an outside edit of either makes the history void, so an undo never
overwrites work done outside the tool.

The history is owned by the tool, not the store, so it survives the store's reloads.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from step_6_plan.model import PLAN_CONFIG

Record = dict[str, Any]


@dataclass(frozen=True)
class AssignmentsBefore:
    """The assignments a change replaced: the records it touched, and the proposals list
    (``None`` = the change did not touch the proposals)."""

    records: dict[str, Record]
    proposals: list[dict[str, Any]] | None = None


@dataclass(frozen=True)
class UndoEntry:
    label: str
    focus: str | None
    at: str
    plan_after: dict[str, Any]
    assignments_after: str | None
    plan_before: dict[str, Any] | None = None
    assignments_before: AssignmentsBefore | None = None


class PlanHistory:
    """The changes of this session, newest last (at most ``plan.review.undo_depth``)."""

    def __init__(self, depth: int = PLAN_CONFIG["review"]["undo_depth"]) -> None:
        self.depth = depth
        self._entries: list[UndoEntry] = []

    def record(self, entry: UndoEntry) -> None:
        self._entries = [*self._entries, entry][-self.depth:]

    def latest(self, plan: dict[str, Any], assignments: str | None) -> UndoEntry | None:
        """The newest change, if the plan and the assignments (their fingerprint, ``None``
        without a file) are still exactly what it left; otherwise the history no longer
        applies and is forgotten."""

        if self._entries:
            newest = self._entries[-1]
            if newest.plan_after != plan or newest.assignments_after != assignments:
                self._entries = []
        return self._entries[-1] if self._entries else None

    def pop(self) -> None:
        self._entries.pop()
