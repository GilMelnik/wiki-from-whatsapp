"""What step 7 reads, and what one page's text depends on.

The step reads four upstream artifacts, each in its effective (edited-if-present) form: the
claim groups (C6), the plan (C7), the assignment of groups to pages (C8) and the entities
(step 4, only for their kinds). Nothing else: no claims, no private audit, no identities, no
reaction summaries. C6 already counts the distinct people per group and per month, with
neutral reactions (a heart, praying hands) left out.

Groups the owner's rule withholds (``step_7_write_pages.withhold``: a negative statement about
a named individual that fewer than two members reported) are treated as placed on no page:
they are in no page's groups, in no other group's ``contradicts``, in no front matter and in
no label count. Each is accounted for in its primary page's ``left_out`` (``withheld_on``)
and listed in the run's metadata (``withheld_list``).

A page's groups are ``groups_on_page(assignments, page_id)`` in C6 order. A group whose
``pages[0]`` is the page is a ``main`` group (written there in full); a group that has the
page second is a ``mention`` (mentioned there, with a link to its primary page). Unplaced
groups are on no page; a group with no assignment record is skipped and counted (the
assignment step is behind).

Each group is turned into a compact ``record`` (the fields the model sees). The page's
``input_fingerprint`` covers the page's own plan fields, its records (with their role) and
the writer (write-stage model + ``pages.prompt_version``). It does not cover the link
catalogue, the breadcrumb, the labels or the corpus-wide "as of" date: renaming a page or a
category must not rewrite the wiki (step 9 follows merged ids and unlinks unpublished
targets; front matter is rebuilt in code).
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from step_6_plan.assign_rules import groups_on_page
from step_6_plan.model import find_node, validation_errors, PlanError
from step_7_write_pages.withhold import REASON as WITHHELD, WithholdRule
from utils.config import section
from utils.paths import ASSIGNMENTS, PLAN, resolve, resolve_aggregated_path, resolve_entities_path
from utils.reconcile import fingerprint

PAGES_CONFIG = section("pages")
MAIN = "main"
MENTION = "mention"
Record = dict[str, Any]


def hebrew_month(month: str) -> str:
    """``"2024-11"`` (or a full date) -> ``"נובמבר 2024"``."""
    year, number = month[:4], int(month[5:7])
    return f"{PAGES_CONFIG['hebrew_months'][number - 1]} {year}"


def last_backed(by_month: Mapping[str, Mapping[str, int]]) -> tuple[str, int] | None:
    """The latest month in which the group had supporters, and how many (None if never)."""
    months = [month for month, counts in by_month.items() if counts.get("supporters", 0) > 0]
    if not months:
        return None
    month = max(months)
    return month, by_month[month]["supporters"]


def _read(path: Path, what: str) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"step 7 needs the {what} at {path}; run the earlier steps first")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} is not valid JSON: {exc}") from exc


@dataclass(frozen=True)
class PageContext:
    """Everything one page's prompt is built from."""

    page_id: str
    brief: dict[str, Any]          # id, title, description, scope (fingerprinted)
    breadcrumb: tuple[str, ...]    # ancestor titles, top first (not fingerprinted)
    records: tuple[Record, ...]    # C6 order
    catalogue: str                 # every page and label, the link vocabulary
    as_of: str                     # newest last_date of the corpus, YYYY-MM-DD
    withheld: frozenset[str] = frozenset()  # withheld group ids (any page; never shown)

    @property
    def ids(self) -> set[str]:
        return {record["id"] for record in self.records}

    @property
    def main_ids(self) -> list[str]:
        return [record["id"] for record in self.records if record["role"] == MAIN]

    def required_ids(self, among: Sequence[str] | None = None) -> list[str]:
        """Groups that must be cited (the "both sides" floor): disputed or contradicted."""
        wanted = None if among is None else set(among)
        return [record["id"] for record in self.records
                if (wanted is None or record["id"] in wanted)
                and (record["opposers"] > 0 or record["contradicts"])]


@dataclass
class Corpus:
    """The effective C6 groups, C7 plan and C8 assignments of one run, and the groups the
    owner's rule withholds."""

    groups: dict[str, dict[str, Any]]
    plan: dict[str, Any]
    assignments: dict[str, dict[str, Any]]
    sources: tuple[Path, ...]
    skipped: list[str] = field(default_factory=list)
    withheld: frozenset[str] = frozenset()

    @classmethod
    def load(cls, aggregated_path: Path | None = None, plan_path: Path | None = None,
             assignments_path: Path | None = None, entities_path: Path | None = None) -> Corpus:
        paths = (aggregated_path or resolve_aggregated_path(), plan_path or resolve(PLAN),
                 assignments_path or resolve(ASSIGNMENTS), entities_path or resolve_entities_path())
        groups = _read(paths[0], "claim groups (step 5)")["groups"]
        plan = _read(paths[1], "plan (step 6)")
        errors = validation_errors(plan)
        if errors:
            raise PlanError([f"{paths[1]} is not a valid C7 plan; fix it in the plan tool", *errors])
        assignments = _read(paths[2], "assignments (step 6b)")["assignments"]
        withheld = WithholdRule.load(paths[3]).withheld_ids(groups)
        corpus = cls({g["group_id"]: g for g in groups}, plan, assignments, paths,
                     withheld=withheld)
        corpus.skipped = [gid for gid in corpus.groups if gid not in assignments]
        return corpus

    @property
    def as_of(self) -> str:
        return max((g["last_date"] for g in self.groups.values()), default="")

    def placed(self) -> dict[str, dict[str, Any]]:
        """Assignment records of current groups only (stale ids in C8 and withheld groups are
        ignored)."""
        return {gid: rec for gid, rec in self.assignments.items()
                if gid in self.groups and gid not in self.withheld}

    def _assigned_page(self, group_id: str) -> str | None:
        record = self.assignments.get(group_id)
        return record["pages"][0] if record and record["pages"] else None

    def primary_page(self, group_id: str) -> str | None:
        """The page a group is written on (``None``: unplaced or withheld)."""
        return None if group_id in self.withheld else self._assigned_page(group_id)

    def withheld_on(self, page_id: str) -> list[str]:
        """Withheld groups that would be ``main`` groups of the page, in C6 order."""
        return [gid for gid in self.groups
                if gid in self.withheld and self._assigned_page(gid) == page_id]

    def accounted_left_out(self, page_id: str, left_out: Sequence[Mapping[str, str]]
                           ) -> list[dict[str, str]]:
        """A page's ``left_out`` with its withheld groups accounted for (code adds them, with
        reason ``withheld``; the writer never saw them)."""
        return [dict(entry) for entry in left_out if entry["reason"] != WITHHELD] + [
            {"group": gid, "reason": WITHHELD} for gid in self.withheld_on(page_id)]

    def withheld_list(self) -> list[dict[str, Any]]:
        """Every withheld group placed on a page: ids and numbers only, never text."""
        return [{"group_id": gid, "page_id": page_id,
                 "entity_ids": list(self.groups[gid].get("entities") or []),
                 "supporter_count": self.groups[gid].get("supporter_count")}
                for gid in self.groups
                if gid in self.withheld and (page_id := self._assigned_page(gid)) is not None]

    def page_group_ids(self, page_id: str) -> list[str]:
        return groups_on_page(self.placed(), page_id)

    def catalogue(self) -> str:
        """Every page (``id | title | description``) and label (``label:id | title | kind``)."""
        pages = [f"{pid} | {p['title_he']} | {' '.join(p['description'].split())}"
                 for pid, p in self.plan["pages"].items()]
        labels = [f"label:{lid} | {lab['title_he']} | {lab['kind']}"
                  for lid, lab in self.plan["labels"].items()]
        return "\n".join(["<pages>", *pages, "</pages>", "<labels>", *labels, "</labels>"])

    def record(self, group_id: str, page_id: str) -> Record:
        """The compact form of one group the model reads (and the page fingerprints)."""
        group = self.groups[group_id]
        primary = self.primary_page(group_id)
        backed = last_backed(group.get("by_month") or {})
        contradicts = []
        for other_id in group.get("contradicts") or []:
            other_page = self.primary_page(other_id)
            if other_id not in self.groups or other_page is None:
                continue  # unplaced groups appear nowhere
            on_page = page_id in self.assignments[other_id]["pages"]
            contradicts.append({"id": other_id} if on_page else {
                "id": other_id, "page": other_page,
                "text": " ".join(self.groups[other_id]["claim_text"].split())})
        record: Record = {
            "id": group_id,
            "role": MAIN if primary == page_id else MENTION,
            "primary": primary,
            "stance": group["stance"],
            "text": " ".join(group["claim_text"].split()),
            "entities": list(group.get("entity_names") or []),
            "supporters": group["supporter_count"],
            "opposers": group["opposer_count"],
            "first": group["first_date"],
            "last": group["last_date"],
            "last_backed": list(backed) if backed else None,
            "contradicts": contradicts,
        }
        by_month = group.get("by_month") or {}
        if len(by_month) > 1:
            record["by_month"] = {m: [c["supporters"], c["opposers"]] for m, c in sorted(by_month.items())}
        return record

    def context(self, page_id: str, catalogue: str) -> PageContext | None:
        """The page's context, or None when no group is on it."""
        ids = self.page_group_ids(page_id)
        if not ids:
            return None
        page = self.plan["pages"][page_id]
        brief = {"id": page_id, "title": page["title_he"], "description": page["description"],
                 "scope": page["scope"]}
        breadcrumb = tuple(find_node(self.plan, node_id)[0]["title_he"] for node_id in page["path"])
        return PageContext(page_id, brief, breadcrumb,
                           tuple(self.record(gid, page_id) for gid in ids), catalogue, self.as_of,
                           self.withheld)

    def label_shares(self) -> Counter[str]:
        """How many placed groups carry each label (for the label rule's lift)."""
        return Counter(lab for rec in self.placed().values() for lab in rec["labels"])


def writer_fingerprint(model: str) -> str:
    """Who writes the text: a new model or prompt version rewrites the page in full."""
    return fingerprint({"model": model, "prompt_version": PAGES_CONFIG["prompt_version"]})


def page_state(context: PageContext, writer: str) -> dict[str, Any]:
    """What a run stores per page in ``metadata.pages`` to diff the next run against."""
    return {
        "writer": writer,
        "brief": fingerprint(context.brief),
        "groups": {record["id"]: fingerprint(record) for record in context.records},
    }


def input_fingerprint(state: Mapping[str, Any]) -> str:
    """The C9 ``input_fingerprint``: the writer, the page's plan fields and its group records."""
    return fingerprint({key: state[key] for key in ("writer", "brief", "groups")})
