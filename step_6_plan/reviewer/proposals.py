"""The owner's decisions on the assignment step's proposals (contract C8 ``proposals``).

A proposal is only ever changed in its ``status``. Rejecting sets ``rejected`` (the step
never proposes it again); a rejected proposal can be put back to ``pending``. Accepting is
the plan operation the proposal stands for, as the owner corrected it in the dialog, saved
together with ``status: accepted``: a new page is ``add_page`` (the owner picks the parent
and may fix the id), a new label ``add_label``, a wider scope the scope edit of the page the
proposal names (followed if that page was merged since). The groups are not moved here: the
next assignment run asks them again against the new page / label / scope.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from step_6_plan.reviewer.assignments import AssignmentsConflict
from step_6_plan.reviewer.requests import Operation
from step_6_plan.reviewer.views import redirect_map

PENDING, ACCEPTED, REJECTED = "pending", "accepted", "rejected"
KIND_NAMES = {"page": "דף חדש", "label": "תווית חדשה", "scope": "הרחבת התחום של דף"}
ACCEPTED_BY = {"page": "add_page", "label": "add_label", "scope": "edit"}
STATUS_TEXT = {PENDING: "ממתינה להחלטה", ACCEPTED: "כבר התקבלה", REJECTED: "כבר נדחתה"}


def find(proposals: list[dict[str, Any]], proposal_id: str) -> dict[str, Any] | None:
    return next((p for p in proposals if p.get("id") == proposal_id), None)


def status_of(proposal: Mapping[str, Any]) -> str:
    return proposal.get("status", PENDING)


def with_status(proposals: list[dict[str, Any]], proposal_id: str, status: str
                ) -> list[dict[str, Any]]:
    """The proposals list with one proposal's status changed (nothing else)."""

    return [{**p, "status": status} if p.get("id") == proposal_id else p for p in proposals]


def check_decidable(proposal: Mapping[str, Any], wanted: str, action: str) -> None:
    """The proposal has status ``wanted``; ``action`` (Hebrew) names what was refused."""

    status = status_of(proposal)
    if status != wanted:
        raise AssignmentsConflict(
            f"ההצעה „{proposal.get('title_he', '')}” {STATUS_TEXT.get(status, status)}, ולכן "
            f"אי אפשר {action}."
        )


def check_operation(proposal: Mapping[str, Any], op: Operation, plan: Mapping[str, Any]) -> None:
    """The operation is the kind of change the proposal stands for."""

    kind = proposal.get("kind")
    expected = ACCEPTED_BY.get(kind)
    if expected is None:
        raise AssignmentsConflict(f"להצעה סוג לא מוכר ({kind}); אפשר רק לדחות אותה.")
    if op.op != expected:
        raise AssignmentsConflict(
            f"ההצעה היא {KIND_NAMES[kind]}, ומקבלים אותה בפעולה {expected} (נשלחה {op.op})."
        )
    if kind == "scope":
        target = proposal.get("target_id") or ""
        page_id = redirect_map(dict(plan)).get(target, target)
        if op.item_id != page_id or set(op.changes) != {"scope"}:
            raise AssignmentsConflict(
                f"הצעה להרחבת תחום מתקבלת רק כשינוי התחום של הדף שהיא מציעה ({page_id})."
            )
