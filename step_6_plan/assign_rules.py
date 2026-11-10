"""Assignment rules (contract C8): the questions asked about a claim group, how the answers
become pages and labels, and how a plan change moves existing assignments.

An assignment record (one per group in C8 ``assignments``)::

    {"pages": [page_id], "labels": [label_id], "p": {page_id: prob},
     "label_p": {label_id: prob}, "manual"?: true}

``pages[0]`` is the primary page; ``pages == []`` means unplaced. ``p`` and ``label_p`` keep
the classifier's probabilities of at least ``assign.kept_probability``, so the rules below can
be applied again (after a merge, or with other thresholds) without asking again. A record with
``manual: true`` was set by the owner: its pages and labels are never re-decided, only
followed through merges and cleared of deleted ids.

Questions (classifier stage ``assign.stage``): the coverage check's page question
(``coverage.page_question``: one choice over the page ids + "none", from titles and scopes)
and one yes/no question per label from its title and description, in the same request. After
a new page or a scope change, a yes/no "screen" question per changed page finds the groups
that might move to it, so only those are asked the page question again.

Decision rules (thresholds chosen on the dev corpus, task 24's report). With ``p1 >= p2`` the
two most probable pages:
- two pages ``[top, second]`` when the classifier splits between them: ``p2 >=
  second_page.min_probability`` and ``p1 + p2 >= second_page.min_pair_probability``;
- else one page when ``p1 >= confident_probability``;
- else unplaced (the group becomes a candidate for a proposal).
A label applies when its probability is at least ``label_probability``.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from step_6_plan.coverage import NONE
from step_6_plan.operations import PlanEdit
from utils.config import section
from utils.llm_client import Question
from utils.reconcile import fingerprint

ASSIGN_CONFIG = section("assign")
PREFIX = ASSIGN_CONFIG["question_prefix"]
SECOND_PAGE = ASSIGN_CONFIG["second_page"]
Record = dict[str, Any]


def label_question(label_id: str, label: Mapping[str, Any]) -> Question:
    """Yes/no: does the label apply to the claim (title + its one-sentence description)."""

    return Question(
        f"{PREFIX['label']}{label_id}",
        "noul",
        f'Does the label "{label["title_he"]}" apply to this claim? {label["description"]}',
    )


def screen_question(page_id: str, page: Mapping[str, Any]) -> Question:
    """Yes/no: could the claim belong on this page (title + scope)."""

    return Question(
        f"{PREFIX['screen']}{page_id}",
        "noul",
        f'Does this claim belong on the wiki page "{page["title_he"]}"? What belongs on the '
        f"page: {page['scope']}",
    )


def item_id(question: Question) -> str:
    """The page or label id a label / screen question is about."""

    for prefix in PREFIX.values():
        if question.id.startswith(prefix):
            return question.id[len(prefix):]
    raise ValueError(f"question {question.id!r} has none of the prefixes {list(PREFIX.values())}")


def kept(probabilities: Mapping[str, float], allowed: Collection[str]) -> dict[str, float]:
    """The probabilities worth storing: ids in ``allowed``, at least ``kept_probability``."""

    floor = ASSIGN_CONFIG["kept_probability"]
    return {key: value for key, value in probabilities.items() if key in allowed and value >= floor}


def decide_pages(p: Mapping[str, float]) -> list[str]:
    """The pages a group goes on (primary first) from its page probabilities; see the module."""

    ranked = sorted(((pid, v) for pid, v in p.items() if pid != NONE), key=lambda kv: -kv[1])
    if not ranked:
        return []
    top, p1 = ranked[0]
    if len(ranked) > 1:
        second, p2 = ranked[1]
        if p2 >= SECOND_PAGE["min_probability"] and p1 + p2 >= SECOND_PAGE["min_pair_probability"]:
            return [top, second]
    return [top] if p1 >= ASSIGN_CONFIG["confident_probability"] else []


def decide_labels(label_p: Mapping[str, float], label_ids: Iterable[str]) -> list[str]:
    """The labels that apply, in the plan's label order."""

    return [lid for lid in label_ids if label_p.get(lid, 0.0) >= ASSIGN_CONFIG["label_probability"]]


def redirects(plan: Mapping[str, Any]) -> dict[str, str]:
    """Every merged-away page / label id -> the id that absorbed it (``merged_from``)."""

    return {
        old: survivor
        for items in (plan["pages"], plan["labels"])
        for survivor, item in items.items()
        for old in item.get("merged_from") or []
    }


def plan_snapshot(plan: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    """Fingerprints of what the questions say about each page and label."""

    return {
        "pages": {pid: fingerprint([page["title_he"], page["scope"]])
                  for pid, page in plan["pages"].items()},
        "labels": {lid: fingerprint(label_question(lid, label).text)
                   for lid, label in plan["labels"].items()},
    }


@dataclass(frozen=True)
class PlanChanges:
    """What changed in the plan since the assignments were made."""

    created_pages: tuple[str, ...] = ()
    rescoped_pages: tuple[str, ...] = ()
    removed_pages: tuple[str, ...] = ()
    changed_labels: tuple[str, ...] = ()
    removed_labels: tuple[str, ...] = ()
    redirects: dict[str, str] = field(default_factory=dict)

    @property
    def screen_pages(self) -> tuple[str, ...]:
        """New or re-scoped pages: other groups are screened for them."""
        return self.created_pages + self.rescoped_pages

    @property
    def pages_changed(self) -> bool:
        return bool(self.screen_pages or self.removed_pages)


def plan_changes(snapshot: Mapping[str, Any] | None, plan: Mapping[str, Any]) -> PlanChanges:
    """Compare the snapshot stored with the assignments (``None`` = none) with ``plan``."""

    old = snapshot or {"pages": {}, "labels": {}}
    now = plan_snapshot(plan)

    def split(kind: str) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
        before, after = old[kind], now[kind]
        return (tuple(i for i in after if i not in before),
                tuple(i for i in after if i in before and before[i] != after[i]),
                tuple(i for i in before if i not in after))

    created_pages, rescoped_pages, removed_pages = split("pages")
    created_labels, changed_labels, removed_labels = split("labels")
    return PlanChanges(created_pages, rescoped_pages, removed_pages,
                       created_labels + changed_labels, removed_labels, redirects(plan))


def changes_from_edit(edit: PlanEdit) -> PlanChanges:
    """The ``PlanChanges`` of one plan operation's result."""

    plan = edit.plan
    return PlanChanges(
        created_pages=tuple(i for i in edit.created if i in plan["pages"]),
        rescoped_pages=tuple(i for i in edit.reassign if i in plan["pages"]),
        removed_pages=edit.removed_pages,
        changed_labels=tuple(i for i in edit.created if i in plan["labels"]),
        removed_labels=edit.removed_labels,
        redirects={**redirects(plan), **edit.redirects},
    )


def _follow_ids(ids: Iterable[str], moved: Mapping[str, str], live: Collection[str]
                ) -> tuple[list[str], list[str]]:
    out: list[str] = []
    cleared: list[str] = []
    for old in ids:
        new = moved.get(old, old)
        if new not in live:
            cleared.append(old)
        elif new not in out:
            out.append(new)
    return out, cleared


def _follow_probabilities(probabilities: Mapping[str, float], moved: Mapping[str, str],
                          live: Collection[str], combine: Callable[[float, float], float]
                          ) -> dict[str, float]:
    out: dict[str, float] = {}
    for old, value in probabilities.items():
        new = moved.get(old, old)
        if new in live:
            out[new] = min(1.0, combine(out[new], value)) if new in out else value
    return out


def follow(record: Record, changes: PlanChanges, plan: Mapping[str, Any]) -> tuple[Record, list[str]]:
    """``record`` with merged-away ids redirected and deleted ones dropped; also returns the
    ids that were cleared (deleted, not merged). Merged pages add up their probabilities (one
    choice); merged labels keep the higher one."""

    moved = changes.redirects
    pages, cleared_pages = _follow_ids(record["pages"], moved, plan["pages"])
    labels, cleared_labels = _follow_ids(record["labels"], moved, plan["labels"])
    followed = {
        **record,
        "pages": pages,
        "labels": labels,
        "p": _follow_probabilities(record["p"], moved, plan["pages"], lambda a, b: a + b),
        "label_p": _follow_probabilities(record["label_p"], moved, plan["labels"], max),
    }
    return followed, cleared_pages + cleared_labels


def affected(assignments: Mapping[str, Record], changes: PlanChanges) -> set[str]:
    """The automatic assignments a page change may move (before ``follow``): groups on a
    re-scoped or deleted page, groups that gave a re-scoped page some probability, and every
    unplaced group whenever pages were added, re-scoped or removed."""

    touched = set(changes.rescoped_pages) | (set(changes.removed_pages) - set(changes.redirects))
    out: set[str] = set()
    for group_id, record in assignments.items():
        if record.get("manual"):
            continue
        if (not record["pages"] and changes.pages_changed) or touched & set(record["pages"]) or \
                any(pid in record["p"] for pid in changes.rescoped_pages):
            out.add(group_id)
    return out


def redecide(record: Record, label_ids: Iterable[str]) -> Record:
    """An automatic record's pages and labels from its probabilities (manual: unchanged)."""

    if record.get("manual"):
        return record
    return {**record, "pages": decide_pages(record["p"]),
            "labels": decide_labels(record["label_p"], label_ids)}


@dataclass(frozen=True)
class AssignmentEdit:
    """``assignments`` after a plan change. ``reask``: groups whose page must be asked again
    (in assignment order); ``screen_pages``: new / re-scoped pages every other automatic group
    is screened for; ``cleared``: group -> deleted page / label ids taken off it."""

    assignments: dict[str, Record]
    reask: tuple[str, ...]
    screen_pages: tuple[str, ...]
    cleared: dict[str, list[str]]


def follow_all(assignments: Mapping[str, Record], changes: PlanChanges,
               plan: Mapping[str, Any]) -> AssignmentEdit:
    """Apply ``changes`` to every record. A manual record that loses all its pages to
    deletions stops being manual and is asked again."""

    reask = affected(assignments, changes)
    out: dict[str, Record] = {}
    cleared: dict[str, list[str]] = {}
    for group_id, record in assignments.items():
        followed, gone = follow(record, changes, plan)
        if gone:
            cleared[group_id] = gone
        if followed.get("manual") and record["pages"] and not followed["pages"]:
            followed = {key: value for key, value in followed.items() if key != "manual"}
            reask.add(group_id)
        out[group_id] = redecide(followed, plan["labels"])
    return AssignmentEdit(out, tuple(g for g in assignments if g in reask),
                          changes.screen_pages, cleared)


def apply_plan_edit(assignments: Mapping[str, Record], plan_edit: PlanEdit) -> AssignmentEdit:
    """Follow one plan operation (``step_6_plan.operations``) in C8 ``assignments``: redirect
    merged ids, drop deleted pages and labels, re-decide automatic records, and list what the
    next ``step_6_plan.assign.run()`` will ask again (it derives the same from the plan)."""

    return follow_all(assignments, changes_from_edit(plan_edit), plan_edit.plan)


def groups_on_page(assignments: Mapping[str, Record], page_id: str, *,
                   primary_only: bool = False) -> list[str]:
    """The groups assigned to ``page_id``, in assignment order (= the C6 group order)."""

    return [gid for gid, record in assignments.items()
            if page_id in (record["pages"][:1] if primary_only else record["pages"])]
