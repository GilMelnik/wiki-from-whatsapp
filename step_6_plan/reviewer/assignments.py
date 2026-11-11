"""The assignments file (contract C8) as the plan tool reads and changes it.

The tool changes two things only, and only in the EDITED file (the original keeps what the
assignment step wrote): a group's ``pages`` / ``labels`` when the owner moves it, which pins
the record (``manual: true``, so the assignment step never re-decides it), and a proposal's
``status``. ``p``, ``label_p`` and ``metadata`` are the classifier's and are never written.
Un-pinning deletes ``manual`` and puts back what the classifier's stored probabilities decide
(what the next assignment run would do; that run asks the group again).

A move starts from the record as the next assignment run will see it: page and label ids
merged away in the plan are followed to their survivor, deleted ones dropped.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from step_6_plan.assign_rules import PlanChanges, follow, redecide, redirects
from step_6_plan.reviewer.history import AssignmentsBefore, Record
from utils.json_io import write_json_file
from utils.reconcile import backup_file, fingerprint

logger = logging.getLogger(__name__)

MANUAL = "manual"
PAGE_ACTIONS = ("set_primary", "add_page", "remove_page")
LABEL_ACTIONS = ("add_label", "remove_label")
MAX_PAGES = 2  # C8: a primary page and at most one second page


class AssignmentsConflict(Exception):
    """A change of the assignments that cannot be made (409); ``args[0]`` is Hebrew."""


class GroupChange(BaseModel):
    """One change applied to every selected group. ``set_primary``: the page becomes the
    group's primary page (a second page stays; if the page was the second, the two swap);
    ``add_page``: a second page (replaces the current second; the primary when the group had
    none); ``remove_page``; ``unplace``: off every page; ``add_label`` / ``remove_label``;
    ``unpin``: back to automatic."""

    action: Literal["set_primary", "add_page", "remove_page", "unplace", "add_label",
                    "remove_label", "unpin"]
    page_id: str | None = None
    label_id: str | None = None


class GroupEdit(BaseModel):
    group_ids: list[str] = Field(min_length=1)
    changes: list[GroupChange] = Field(min_length=1)


def followed(record: Record, plan: Mapping[str, Any]) -> Record:
    """``record`` as the next assignment run sees it: merged ids followed (pages, labels and
    probabilities), deleted ids dropped."""

    return follow(record, PlanChanges(redirects=redirects(plan)), plan)[0]


def _with_page(pages: list[str], change: GroupChange) -> list[str]:
    page = change.page_id
    if change.action == "set_primary":
        rest = [pid for pid in pages if pid != page]
        return [page, *(rest if page in pages else pages[1:])][:MAX_PAGES]
    if change.action == "add_page":
        return pages if page in pages else [*pages[:MAX_PAGES - 1], page]
    return [pid for pid in pages if pid != page]


def changed_record(record: Record, changes: Sequence[GroupChange], plan: Mapping[str, Any]
                   ) -> Record:
    """The record after ``changes`` (validated by ``check_changes``); ``p``, ``label_p`` and
    any other field stay exactly as stored. A change that moves nothing (a label the group
    already has) does not pin it; the record is then returned as it is."""

    current = followed(record, plan)
    pages, labels = list(current["pages"]), list(current["labels"])
    manual = bool(record.get(MANUAL))
    for change in changes:
        before = (pages, labels)
        if change.action in PAGE_ACTIONS:
            pages = _with_page(pages, change)
        elif change.action == "unplace":
            pages = []
        elif change.action in LABEL_ACTIONS:
            wanted = set(labels) - {change.label_id}
            if change.action == "add_label":
                wanted.add(change.label_id)
            labels = [lid for lid in plan["labels"] if lid in wanted]
        else:  # unpin: what the classifier's stored answers decide
            automatic = redecide({key: v for key, v in current.items() if key != MANUAL},
                                 plan["labels"])
            pages, labels, manual = list(automatic["pages"]), list(automatic["labels"]), False
            continue
        manual = manual or (pages, labels) != before
    if (pages, labels, manual) == (current["pages"], current["labels"], bool(record.get(MANUAL))):
        return record
    out = {key: value for key, value in record.items() if key != MANUAL}
    out.update(pages=pages, labels=labels)
    if manual:
        out[MANUAL] = True
    return out


def check_changes(changes: Sequence[GroupChange], plan: Mapping[str, Any]) -> None:
    """Every page / label a change names is one of the plan's live pages / labels."""

    for change in changes:
        if change.action in PAGE_ACTIONS and change.page_id not in plan["pages"]:
            raise AssignmentsConflict(f"אין בתוכנית דף עם המזהה {change.page_id}.")
        if change.action in LABEL_ACTIONS and change.label_id not in plan["labels"]:
            raise AssignmentsConflict(f"אין בתוכנית תווית עם המזהה {change.label_id}.")


RECORD_FIELDS = {"pages": list, "labels": list, "p": dict, "label_p": dict}


def _check_shape(data: Any) -> None:
    """``TypeError`` unless ``data`` has the C8 shape the views walk."""

    if not isinstance(data.get("assignments"), dict):
        raise TypeError("no 'assignments' object")
    if not isinstance(data.get("proposals", []), list) or not all(
            isinstance(p, dict) for p in data.get("proposals", [])):
        raise TypeError("'proposals' is not a list of objects")
    for group_id, record in data["assignments"].items():
        if not isinstance(record, dict) or not all(
                isinstance(record.get(key), kind) for key, kind in RECORD_FIELDS.items()):
            raise TypeError(f"record {group_id} lacks one of {list(RECORD_FIELDS)}")


class AssignmentsFile:
    """The effective assignments (edited copy, else the original), loaded once per store."""

    def __init__(self, original: Path, edited: Path) -> None:
        self.original = original
        self.edited = edited
        self.source = edited if edited.is_file() else original
        self.problem: str | None = None
        self.data: dict[str, Any] = {"assignments": {}, "proposals": [], "metadata": {}}
        if not self.source.is_file():
            self.fingerprint: str | None = None
            return
        try:
            data = json.loads(self.source.read_text(encoding="utf-8"))
            _check_shape(data)
            self.data = {"proposals": [], "metadata": {}, **data}
        except (json.JSONDecodeError, TypeError, AttributeError) as exc:
            logger.warning(f"{self.source} cannot be read as C8 assignments: {exc!r}")
            self.problem = f"הקובץ {self.source} אינו קובץ שיוך תקין ({exc})."
        self.fingerprint = fingerprint(self.data)

    @property
    def exists(self) -> bool:
        return self.fingerprint is not None

    @property
    def usable(self) -> bool:
        return self.exists and self.problem is None

    @property
    def records(self) -> dict[str, Record]:
        return self.data["assignments"]

    @property
    def proposals(self) -> list[dict[str, Any]]:
        return self.data["proposals"]

    def require(self) -> None:
        if not self.exists:
            raise AssignmentsConflict(
                "אין עדיין קובץ שיוך: הריצו את שלב השיוך (6ב, python -m step_6_plan.assign) "
                "אחרי בדיקת התוכנית."
            )
        if self.problem:
            raise AssignmentsConflict(self.problem)

    def save(self, records: Mapping[str, Record], proposals: list[dict[str, Any]] | None = None
             ) -> AssignmentsBefore:
        """Write ``records`` (replacing those group ids; file order kept) and, if given, the
        proposals to the edited file, after backing it up; returns what they replaced. The
        caller holds the lock (``StoreHandle.mutate``)."""

        before = AssignmentsBefore(
            {gid: self.records[gid] for gid in records},
            None if proposals is None else self.proposals,
        )
        payload = {**self.data, "assignments": {**self.records, **records}}
        if proposals is not None:
            payload["proposals"] = proposals
        backup_file(self.edited)
        write_json_file(payload, self.edited)
        self.data = payload
        self.source = self.edited
        self.fingerprint = fingerprint(payload)
        return before

    def restore(self, before: AssignmentsBefore) -> None:
        """Put back what ``save`` replaced (undo)."""

        self.save(before.records, before.proposals)


ACTION_PHRASES = {
    "set_primary": "העברה לדף {page}",
    "add_page": "הוספת הדף {page} כדף נוסף",
    "remove_page": "הסרה מהדף {page}",
    "unplace": "הורדה מכל הדפים",
    "add_label": "הוספת התווית {label}",
    "remove_label": "הסרת התווית {label}",
    "unpin": "החזרה לשיוך אוטומטי",
}


def describe_edit(edit: GroupEdit, title: Callable[[str], str]) -> str:
    """The edit in Hebrew, for the toast, the undo button and the log."""

    def quoted(item_id: str | None) -> str:
        return f"„{title(item_id or '')}”"

    actions = ", ".join(ACTION_PHRASES[change.action].format(page=quoted(change.page_id),
                                                             label=quoted(change.label_id))
                        for change in edit.changes)
    count = len(edit.group_ids)
    return f"{actions} ({'קבוצה אחת' if count == 1 else f'{count} קבוצות'})"
