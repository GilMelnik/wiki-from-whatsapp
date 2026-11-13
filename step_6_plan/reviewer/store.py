"""The plan tool's store: a thin layer over ``step_6_plan.operations`` and the assignments.

It reads the effective plan (edited copy, else the original the planning step wrote), the
effective claim groups (for the texts), the coverage report of the planning run and, when
step 6b has run, the effective assignments (``assignments.AssignmentsFile``). A plan change
runs one operation; the operation validates the whole new plan, so an invalid plan is never
written. A group move or a proposal decision changes the assignments. Every change is
written to the edited files only, after backing up the previous edited file
(``guarded_write`` semantics; the caller already holds the locks). The original files are
never touched: the plan's keeps the model's proposal, which the views compare against, the
assignments' what the assignment step decided.

Saving a plan operation never rewrites the assignments: the next assignment run follows
merges, clears deleted ids and asks again what the change affects; until then the views read
the assignments through today's plan (``assignment_views``).

Undo: ``history.PlanHistory`` (owned by the tool, so it survives reloads) covers both files.
"""

from __future__ import annotations

import logging
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
from step_6_plan.reviewer import proposals as decisions
from step_6_plan.reviewer.assignment_views import REVIEW_CONFIG, AssignmentView
from step_6_plan.reviewer.assignments import (
    AssignmentsConflict,
    AssignmentsFile,
    GroupEdit,
    changed_record,
    check_changes,
    describe_edit,
)
from step_6_plan.reviewer.checks import plan_warnings
from step_6_plan.reviewer.history import PlanHistory, UndoEntry
from step_6_plan.reviewer.requests import Delete, Merge, Operation, quote, title_of
from step_6_plan.reviewer.views import (
    branches,
    budget,
    changes,
    coverage_view,
    flat_tree,
    group_summary,
    labels_by_kind,
)
from utils.json_io import read_json_file, write_json_file
from utils.reconcile import backup_file

logger = logging.getLogger(__name__)

EDITED_BY = "plan_tool"
KIND_NAMES = {"page": "page", "node": "category", "label": "label"}
METADATA_SHOWN = ("top_level_rationale", "model", "generated_at", "group_count", "edited_at")
# What a plan file that is not a walkable C7 plan raises while the views are built.
_UNREADABLE = (KeyError, TypeError, AttributeError, StopIteration)


class PlanConflict(Exception):
    """A change that cannot be made (409); ``args[0]`` is an English message followed by the
    rules the change would break, verbatim, one per line."""


class ItemNotFound(Exception):
    """An id the plan does not have (404)."""


def _conflict(lead: str, error: PlanError) -> PlanConflict:
    return PlanConflict("\n".join([lead, *(f"• {line}" for line in error.errors)]))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _edit_fields(edit: PlanEdit) -> dict[str, Any]:
    return {
        "created": list(edit.created),
        "removed_pages": list(edit.removed_pages),
        "removed_labels": list(edit.removed_labels),
        "redirects": dict(edit.redirects),
        "reassign": list(edit.reassign),
    }


class PlanStore:
    def __init__(
        self,
        plan_original: Path,
        plan_edited: Path,
        groups_original: Path,
        groups_edited: Path,
        assignments_original: Path,
        assignments_edited: Path,
        coverage_path: Path,
        history: PlanHistory,
    ) -> None:
        self.plan_edited = plan_edited
        self.source = plan_edited if plan_edited.is_file() else plan_original
        if not self.source.is_file():
            raise FileNotFoundError(
                f"{plan_original} (no wiki plan yet: run step 6, python -m step_6_plan.run)"
            )
        self.plan: dict[str, Any] = read_json_file(self.source)
        self.proposal = read_json_file(plan_original) if plan_original.is_file() else None
        groups_path = groups_edited if groups_edited.is_file() else groups_original
        self.groups: dict[str, dict[str, Any]] | None = (
            {group["group_id"]: group for group in read_json_file(groups_path)["groups"]}
            if groups_path.is_file() else None
        )
        self.coverage_report = read_json_file(coverage_path) if coverage_path.is_file() else None
        self.assignments = AssignmentsFile(assignments_original, assignments_edited)
        self.history = history
        self._view: AssignmentView | None = None

    # --- reads ------------------------------------------------------------------

    def _latest(self) -> UndoEntry | None:
        return self.history.latest(self.plan, self.assignments.fingerprint)

    @property
    def view(self) -> AssignmentView:
        """The assignments read through today's plan (``AssignmentsConflict`` without a
        usable assignments file)."""

        self.assignments.require()
        if self._view is None:
            self._view = AssignmentView(self.plan, self.assignments.data, self.groups)
        return self._view

    def overview(self) -> dict[str, Any]:
        """Everything the tool shows about the plan except the example texts and the lists
        of groups."""

        plan = self.plan
        latest = self._latest()
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
                    sizes=self.view.summary()["sizes"] if self.assignments.usable else None,
                ),
                "newer_groups": self._newer_groups(),
                "assignments": self._assignments_overview(),
            }
        except _UNREADABLE as exc:
            logger.warning(f"{self.source} cannot be shown as a C7 plan: {exc!r}")
            return {**answer, "unreadable": True}

    def _assignments_overview(self) -> dict[str, Any]:
        """Whether step 6b has run, and its numbers read against today's plan."""

        files = self.assignments
        answer = {"exists": files.exists, "file": str(files.source),
                  "edited_exists": files.edited.is_file(), "problem": files.problem}
        return {**answer, **self.view.summary()} if files.usable else answer

    def _newer_groups(self) -> dict[str, int] | None:
        """Groups the planning model never saw (newer than the plan) and how many of them the
        assignment step has placed already; ``None`` when the plan does not say."""

        planned = self.plan["metadata"].get("planned_group_ids")
        if planned is None or self.groups is None:
            return None
        planned_ids = set(planned)
        newer = [gid for gid in self.groups if gid not in planned_ids]
        records = self.assignments.records if self.assignments.usable else {}
        return {"count": len(newer), "assigned": sum(1 for gid in newer if gid in records)}

    def _readable_proposal(self) -> dict[str, Any] | None:
        if self.proposal is None or validation_errors(self.proposal):
            return None
        return self.proposal

    def _coverage(self) -> dict[str, Any] | None:
        return coverage_view(self.coverage_report, self.plan, self._readable_proposal())

    def page_detail(self, page_id: str) -> dict[str, Any]:
        """What judging a page's scope needs: once step 6b has run, its exact size, a sample of
        its groups (least certain first) and the closest groups it did not get; the model's
        example groups with their claim text, and the coverage numbers of the planning run."""

        page = self.plan["pages"].get(page_id)
        if page is None:
            raise ItemNotFound(f"The plan has no page with the id {page_id}.")
        coverage = self._coverage()
        answer = {
            "page_id": page_id,
            "examples": self._summaries(page["example_group_ids"]),
            "coverage": coverage["pages"][page_id] if coverage else None,
            "size": None,
            "sample": None,
            "near": None,
        }
        if not self.assignments.usable:
            return answer
        limit = REVIEW_CONFIG["scope_sample"]
        sample = self.view.groups_list(scope="page", item_id=page_id, role="primary",
                                       sort="uncertain", q="", offset=0, limit=limit)
        return {**answer, "size": self.view.summary()["sizes"][page_id],
                "sample": sample["items"], "near": self.view.near_groups(page_id, limit)}

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

    def groups_list(self, *, scope: str, item_id: str | None, role: str, sort: str, q: str,
                    offset: int, limit: int) -> dict[str, Any]:
        """One page of the groups on a page / with a label / on no page / waiting."""

        view = self.view
        if scope in ("page", "label"):
            items = self.plan["pages"] if scope == "page" else self.plan["labels"]
            if item_id not in items:
                raise ItemNotFound(f"The plan has no {scope} with the id {item_id}.")
        return view.groups_list(scope=scope, item_id=item_id, role=role, sort=sort, q=q,
                                offset=offset, limit=limit)

    def proposals(self) -> dict[str, Any]:
        return self.view.proposals_view(self.check_id)

    def check_id(self, item_id: str) -> dict[str, Any]:
        """Whether ``item_id`` can be given to a new item, with the reason in English."""

        problem = None
        if not item_id:
            problem = "Type an id."
        elif len(item_id) > PLAN_CONFIG["slug_max_length"]:
            problem = f"The id is longer than {PLAN_CONFIG['slug_max_length']} characters."
        elif slug_error(item_id):
            problem = ("An id is made of small Latin letters and digits, with words separated by "
                       "a single hyphen (e.g. surrogate-gifts).")
        elif item_id in live_ids(self.plan):
            kind = KIND_NAMES[kind_of(self.plan, item_id)]
            problem = f"The id is already in use: the {kind} {quote(title_of(self.plan, item_id))}."
        elif item_id in retired_ids(self.plan):
            problem = ("The id was retired (merged or deleted) and will not be used again, so that "
                       "old links or assignments do not point at something else.")
        return {"id": item_id, "ok": problem is None, "problem": problem}

    # --- plan changes -------------------------------------------------------------

    def _run(self, op: Operation, lead: str) -> PlanEdit:
        try:
            return op.apply(self.plan)
        except PlanError as exc:
            raise _conflict(lead, exc) from exc

    def preview(self, op: Operation) -> dict[str, Any]:
        """What ``op`` would do, without saving (``PlanConflict`` if it cannot be done), also
        to the assignments (``None`` before step 6b has run)."""

        edit = self._run(op, "This change is not possible, because the resulting plan breaks the rules:")
        effect = self.view.plan_edit_effect(edit) if self.assignments.usable else None
        return {"label": op.describe(self.plan), "edit": _edit_fields(edit), "assignments": effect}

    def apply(self, op: Operation) -> dict[str, Any]:
        """Run ``op`` and save the result; answers the new overview, what the change means
        downstream and which item to show next."""

        edit = self._run(op, "The change was not saved, because the resulting plan breaks the rules:")
        label = op.describe(self.plan)
        focus = self._focus(op, edit)
        before = self.plan
        after = self._save(edit.plan)
        self.history.record(UndoEntry(label, focus, _now(), after, self.assignments.fingerprint,
                                      plan_before=before))
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

    def _save(self, plan: dict[str, Any], *, stamp: bool = True) -> dict[str, Any]:
        if stamp:
            plan["metadata"] = {**plan["metadata"], "edited_by": EDITED_BY, "edited_at": _now()}
        backup_file(self.plan_edited)
        write_json_file(plan, self.plan_edited)
        self.plan = plan
        self.source = self.plan_edited
        self._view = None
        return plan

    # --- assignment changes -------------------------------------------------------

    def _save_assignments(self, label: str, records: dict[str, Any],
                          proposals: list[dict[str, Any]] | None = None, *,
                          plan_before: dict[str, Any] | None = None,
                          focus: str | None = None) -> None:
        before = self.assignments.save(records, proposals)
        self._view = None
        self.history.record(UndoEntry(label, focus, _now(), self.plan, self.assignments.fingerprint,
                                      plan_before=plan_before, assignments_before=before))
        logger.info(f"Plan tool: {label}; saved {self.assignments.edited}")

    def edit_groups(self, edit: GroupEdit) -> dict[str, Any]:
        """Move the given groups (pins them, see ``assignments``); answers the new overview,
        the change in words and how many records changed (0: nothing to save)."""

        self.view  # noqa: B018 - requires a usable assignments file
        check_changes(edit.changes, self.plan)
        records = self.assignments.records
        unknown = [gid for gid in edit.group_ids if gid not in records]
        if unknown:
            raise AssignmentsConflict(
                "These groups have no assignment yet (they are new since the last assignment run, "
                f"or no longer in the groups file); run the assignment step: {', '.join(unknown)}"
            )
        changed = {}
        for gid in dict.fromkeys(edit.group_ids):
            record = changed_record(records[gid], edit.changes, self.plan)
            if record != records[gid]:
                changed[gid] = record
        label = describe_edit(edit, lambda item_id: title_of(self.plan, item_id))
        if changed:
            self._save_assignments(label, changed)
        return {"overview": self.overview(), "label": label, "changed": len(changed)}

    def _proposal(self, proposal_id: str, wanted: str, action: str) -> dict[str, Any]:
        self.view  # noqa: B018 - requires a usable assignments file
        proposal = decisions.find(self.assignments.proposals, proposal_id)
        if proposal is None:
            raise ItemNotFound(f"There is no proposal with the id {proposal_id}.")
        decisions.check_decidable(proposal, wanted, action)
        return proposal

    def decide(self, proposal_id: str, status: str) -> dict[str, Any]:
        """Reject a pending proposal, or put a rejected one back to pending."""

        rejecting = status == decisions.REJECTED
        proposal = self._proposal(proposal_id, decisions.PENDING if rejecting else decisions.REJECTED,
                                  "be rejected" if rejecting else "be put back to pending")
        proposals = decisions.with_status(self.assignments.proposals, proposal_id, status)
        title = quote(proposal.get("title_he", ""))
        label = f"Rejected the proposal {title}" if rejecting else f"Put the proposal {title} back to pending"
        self._save_assignments(label, {}, proposals)
        return {"overview": self.overview(), "label": label}

    def accept(self, proposal_id: str, op: Operation) -> dict[str, Any]:
        """Save the plan operation the proposal stands for (as the owner corrected it) and
        mark the proposal accepted, as one change: everything is checked before either file
        is written, and undo puts back both."""

        proposal = self._proposal(proposal_id, decisions.PENDING, "be accepted")
        decisions.check_operation(proposal, op, self.plan)
        edit = self._run(op, "The proposal was not accepted, because the resulting plan breaks the rules:")
        label = f"Accepted the proposal {quote(proposal.get('title_he', ''))}: {op.describe(self.plan)}"
        focus = self._focus(op, edit)
        plan_before = self.plan
        proposals = decisions.with_status(self.assignments.proposals, proposal_id,
                                          decisions.ACCEPTED)
        self._save(edit.plan)
        try:
            self._save_assignments(label, {}, proposals, plan_before=plan_before, focus=focus)
        except OSError:
            self._save(plan_before, stamp=False)
            raise
        return {"overview": self.overview(), "label": label, "edit": _edit_fields(edit),
                "focus": focus}

    # --- undo -----------------------------------------------------------------------

    def undo(self) -> dict[str, Any]:
        """Put back the plan and / or the assignments as they were before the newest change
        of this session."""

        latest = self._latest()
        if latest is None:
            raise PlanConflict(
                "There is no change to undo: no change was made in the tool since the server "
                "started, or the plan or the assignments changed outside the tool (for example a "
                "re-plan or an assignment run), so undoing is no longer safe."
            )
        if latest.plan_before is not None:
            self._save(latest.plan_before, stamp=False)
        if latest.assignments_before is not None:
            self.assignments.restore(latest.assignments_before)
            self._view = None
        self.history.pop()
        logger.info(f"Plan tool: undid {latest.label}")
        focus = latest.focus if latest.focus in live_ids(self.plan) else None
        return {"overview": self.overview(), "label": latest.label, "focus": focus}
