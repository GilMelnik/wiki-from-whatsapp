"""The plan tool's store: a thin layer over ``step_6_plan.operations``.

It reads the effective plan (edited copy, else the original the planning step wrote), the
effective claim groups (for the example texts) and the coverage report of the planning run.
A change runs one operation; the operation validates the whole new plan, so an invalid plan
is never written. A valid result is written to the edited file only, after backing up the
previous edited file (``guarded_write`` semantics; the caller already holds the lock). The
original file is never touched, so it always holds the model's proposal, which the views
compare against.

Undo: ``PlanHistory`` keeps the plan before and after each change of this session. It is
owned by the tool, not the store, so it survives reloads caused by other files (a step-5 run);
an entry is offered only while the plan on disk is still the one that change wrote, so a
re-plan or an outside edit can never be overwritten by an undo.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from step_6_plan.model import (
    PLAN_CONFIG,
    PlanError,
    live_ids,
    retired_ids,
    slug_error,
    validation_errors,
)
from step_6_plan.operations import PlanEdit, kind_of
from step_6_plan.reviewer.checks import plan_warnings
from step_6_plan.reviewer.requests import Delete, Merge, Operation, title_of
from step_6_plan.reviewer.views import (
    branches,
    budget,
    changes,
    coverage_view,
    flat_tree,
    group_summary,
    labels_by_kind,
)
from utils.json_io import write_json_file
from utils.reconcile import backup_file

logger = logging.getLogger(__name__)

EDITED_BY = "plan_tool"
KIND_NAMES = {"page": "דף", "node": "קטגוריה", "label": "תווית"}
METADATA_SHOWN = ("top_level_rationale", "model", "generated_at", "group_count", "edited_at")
# What a plan file that is not a walkable C7 plan raises while the views are built.
_UNREADABLE = (KeyError, TypeError, AttributeError, StopIteration)


class PlanConflict(Exception):
    """A change that cannot be made (409); ``args[0]`` is a Hebrew message followed by the
    rules the change would break, verbatim, one per line."""


class ItemNotFound(Exception):
    """An id the plan does not have (404)."""


def _conflict(lead: str, error: PlanError) -> PlanConflict:
    return PlanConflict("\n".join([lead, *(f"• {line}" for line in error.errors)]))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _edit_fields(edit: PlanEdit) -> dict[str, Any]:
    return {
        "created": list(edit.created),
        "removed_pages": list(edit.removed_pages),
        "removed_labels": list(edit.removed_labels),
        "redirects": dict(edit.redirects),
        "reassign": list(edit.reassign),
    }


@dataclass(frozen=True)
class UndoEntry:
    before: dict[str, Any]
    after: dict[str, Any]
    label: str
    focus: str | None
    at: str


class PlanHistory:
    """The changes of this session, newest last (at most ``plan.review.undo_depth``)."""

    def __init__(self, depth: int = PLAN_CONFIG["review"]["undo_depth"]) -> None:
        self.depth = depth
        self._entries: list[UndoEntry] = []

    def record(self, entry: UndoEntry) -> None:
        self._entries = [*self._entries, entry][-self.depth:]

    def latest(self, current: dict[str, Any]) -> UndoEntry | None:
        """The newest change, if the plan is still exactly what it wrote; otherwise the
        history no longer applies and is forgotten."""

        if self._entries and self._entries[-1].after != current:
            self._entries = []
        return self._entries[-1] if self._entries else None

    def pop(self) -> None:
        self._entries.pop()


class PlanStore:
    def __init__(
        self,
        plan_original: Path,
        plan_edited: Path,
        groups_original: Path,
        groups_edited: Path,
        coverage_path: Path,
        history: PlanHistory,
    ) -> None:
        self.plan_edited = plan_edited
        self.source = plan_edited if plan_edited.is_file() else plan_original
        if not self.source.is_file():
            raise FileNotFoundError(
                f"{plan_original} (no wiki plan yet: run step 6, python -m step_6_plan.run)"
            )
        self.plan: dict[str, Any] = _read(self.source)
        self.proposal = _read(plan_original) if plan_original.is_file() else None
        groups_path = groups_edited if groups_edited.is_file() else groups_original
        self.groups: dict[str, dict[str, Any]] | None = (
            {group["group_id"]: group for group in _read(groups_path)["groups"]}
            if groups_path.is_file() else None
        )
        self.coverage_report = _read(coverage_path) if coverage_path.is_file() else None
        self.history = history

    # --- reads ------------------------------------------------------------------

    def overview(self) -> dict[str, Any]:
        """Everything the tool shows about the plan except the example texts."""

        plan = self.plan
        latest = self.history.latest(plan)
        answer = {
            "file": str(self.source),
            "edited_exists": self.plan_edited.is_file(),
            "errors": validation_errors(plan),
            "undo": {"label": latest.label, "at": latest.at} if latest else None,
            "groups_available": self.groups is not None,
        }
        try:
            coverage = self._coverage()
            return {
                **answer,
                "metadata": {key: plan["metadata"].get(key) for key in METADATA_SHOWN},
                "nodes": flat_tree(plan),
                "pages": plan["pages"],
                "labels": plan["labels"],
                "label_kinds": labels_by_kind(plan),
                "budget": budget(plan),
                "branches": branches(plan),
                "retired_ids": sorted(retired_ids(plan)),
                "changes": changes(plan, self._readable_proposal()),
                "coverage": coverage,
                "warnings": plan_warnings(
                    plan,
                    group_ids=set(self.groups) if self.groups is not None else None,
                    coverage=coverage,
                ),
            }
        except _UNREADABLE as exc:
            logger.warning(f"{self.source} cannot be shown as a C7 plan: {exc!r}")
            return {**answer, "unreadable": True}

    def _readable_proposal(self) -> dict[str, Any] | None:
        if self.proposal is None or validation_errors(self.proposal):
            return None
        return self.proposal

    def _coverage(self) -> dict[str, Any] | None:
        return coverage_view(self.coverage_report, self.plan, self._readable_proposal())

    def page_detail(self, page_id: str) -> dict[str, Any]:
        """A page's example groups with their claim text, and its coverage numbers."""

        page = self.plan["pages"].get(page_id)
        if page is None:
            raise ItemNotFound(f"אין בתוכנית דף עם המזהה {page_id}.")
        coverage = self._coverage()
        return {
            "page_id": page_id,
            "examples": self._summaries(page["example_group_ids"]),
            "coverage": coverage["pages"][page_id] if coverage else None,
        }

    def _summaries(self, group_ids: list[str]) -> list[dict[str, Any]]:
        groups = self.groups or {}
        return [
            group_summary(groups[gid]) if gid in groups else {"group_id": gid, "missing": True}
            for gid in group_ids
        ]

    def unplaced(self) -> dict[str, Any]:
        """The sampled groups the coverage check could not place confidently, with text."""

        report = self.coverage_report or {}
        return {
            "none": self._summaries(report.get("none_group_ids") or []),
            "unsure": self._summaries(report.get("unsure_group_ids") or []),
        }

    def check_id(self, item_id: str) -> dict[str, Any]:
        """Whether ``item_id`` can be given to a new item, with the reason in Hebrew."""

        problem = None
        if not item_id:
            problem = "הקלידו מזהה."
        elif len(item_id) > PLAN_CONFIG["slug_max_length"]:
            problem = f"המזהה ארוך מ-{PLAN_CONFIG['slug_max_length']} תווים."
        elif slug_error(item_id):
            problem = ("מזהה מורכב מאותיות לטיניות קטנות וספרות, ומילים מופרדות במקף אחד "
                       "(למשל surrogate-gifts).")
        elif item_id in live_ids(self.plan):
            kind = KIND_NAMES[kind_of(self.plan, item_id)]
            problem = f"המזהה כבר בשימוש: {kind} „{title_of(self.plan, item_id)}”."
        elif item_id in retired_ids(self.plan):
            problem = ("המזהה הוצא משימוש (מוזג או נמחק) ולא יחזור לשימוש, כדי שקישור או שיוך "
                       "ישנים לא יצביעו על משהו אחר.")
        return {"id": item_id, "ok": problem is None, "problem": problem}

    # --- changes ----------------------------------------------------------------

    def _run(self, op: Operation, lead: str) -> PlanEdit:
        try:
            return op.apply(self.plan)
        except PlanError as exc:
            raise _conflict(lead, exc) from exc

    def preview(self, op: Operation) -> dict[str, Any]:
        """What ``op`` would do, without saving (``PlanConflict`` if it cannot be done)."""

        edit = self._run(op, "השינוי הזה אינו אפשרי, כי התוכנית שתתקבל שוברת את הכללים:")
        return {"label": op.describe(self.plan), "edit": _edit_fields(edit)}

    def apply(self, op: Operation) -> dict[str, Any]:
        """Run ``op`` and save the result; answers the new overview, what the change means
        downstream and which item to show next."""

        edit = self._run(op, "השינוי לא נשמר, כי התוכנית שתתקבל שוברת את הכללים:")
        label = op.describe(self.plan)
        focus = self._focus(op, edit)
        before = self.plan
        after = self._save(edit.plan)
        self.history.record(UndoEntry(before, after, label, focus, _now()))
        logger.info(f"Plan tool: {label} ({op.op}) {_edit_fields(edit)}; saved {self.plan_edited}")
        return {"overview": self.overview(), "label": label, "edit": _edit_fields(edit),
                "focus": focus}

    def _focus(self, op: Operation, edit: PlanEdit) -> str | None:
        """The item to show after ``op``: what it created, the survivor of a merge, the
        parent of a deleted node (``None`` after deleting a label), else the item itself."""

        if edit.created:
            return edit.created[0]
        if isinstance(op, Merge):
            return op.survivor_id
        if isinstance(op, Delete):
            if op.item_id in self.plan["labels"]:
                return None
            return {row["id"]: row["parent_id"] for row in flat_tree(self.plan)}[op.item_id]
        return getattr(op, "item_id", None)

    def undo(self) -> dict[str, Any]:
        """Put back the plan as it was before the newest change of this session."""

        latest = self.history.latest(self.plan)
        if latest is None:
            raise PlanConflict(
                "אין שינוי לבטל: לא נעשה שינוי בכלי מאז שהשרת עלה, או שהתוכנית השתנתה מחוץ "
                "לכלי (למשל תכנון מחדש) ולכן הביטול כבר לא בטוח."
            )
        self._save(latest.before, stamp=False)
        self.history.pop()
        logger.info(f"Plan tool: undid {latest.label}; saved {self.plan_edited}")
        focus = latest.focus if latest.focus in live_ids(self.plan) else None
        return {"overview": self.overview(), "label": latest.label, "focus": focus}

    def _save(self, plan: dict[str, Any], *, stamp: bool = True) -> dict[str, Any]:
        if stamp:
            plan["metadata"] = {**plan["metadata"], "edited_by": EDITED_BY, "edited_at": _now()}
        backup_file(self.plan_edited)
        write_json_file(plan, self.plan_edited)
        self.plan = plan
        self.source = self.plan_edited
        return plan
