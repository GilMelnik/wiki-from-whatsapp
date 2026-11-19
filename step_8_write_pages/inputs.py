"""What step 7 reads, and what one page's text depends on.

The step reads the claim records (C6, ``step_5_same_complaint.claim_records.load_claim_records``: one
record per claim, joined on read from the effective claims and entities, step 3's private
audit and the owner's contradiction pairs), the plan (C7) and the assignment of claims to
pages (C8), each in its effective (edited-if-present) form. Only the record fields listed in
``record`` reach the model: never a claim or thread id (WhatsApp message ids), an identity or
the participant count. A record already counts the distinct people per claim and per month,
with neutral reactions (a heart, praying hands) left out. A claim is referred to by its
``short_id`` only.

Claims withheld from the wiki now (``same_complaint_release.withheld_ids``: the owner's rule
of ``step_5_same_complaint.withhold``, a negative statement about a named entity that fewer than
two members reported, minus what the same-complaint check released) are treated as placed on
no page:
they are in no page's claims, in no other claim's ``contradicts``, in no front matter and in
no label count. Each is accounted for in its primary page's ``left_out`` (``withheld_on``)
and listed in the run's metadata (``withheld_list``).

A page's claims are ``claims_on_page(assignments, page_id)`` in C6 order. A claim whose
``pages[0]`` is the page is a ``main`` claim (written there in full); a claim that has the
page second is a ``mention`` (mentioned there, with a link to its primary page). Unplaced
claims are on no page; a claim with no assignment record is skipped and counted (the
assignment step is behind).

Each claim is turned into a compact ``record`` (the fields the model sees). The page's
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

from step_7_assign.rules import claims_on_page
from step_5_same_complaint.claim_records import ClaimSources, load_claim_records
from step_6_plan.model import find_node, validation_errors, PlanError
from step_5_same_complaint.release import withheld_ids
from step_5_same_complaint.withhold import REASON as WITHHELD
from utils.config import section
from utils.paths import ASSIGNMENTS, PLAN, resolve
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
    """The latest month in which the claim had supporters, and how many (None if never)."""
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
    withheld: frozenset[str] = frozenset()  # withheld short ids (any page; never shown)

    @property
    def ids(self) -> set[str]:
        return {record["id"] for record in self.records}

    @property
    def main_ids(self) -> list[str]:
        return [record["id"] for record in self.records if record["role"] == MAIN]

    def required_ids(self, among: Sequence[str] | None = None) -> list[str]:
        """Claims that must be cited (the "both sides" floor): disputed or contradicted."""
        wanted = None if among is None else set(among)
        return [record["id"] for record in self.records
                if (wanted is None or record["id"] in wanted)
                and (record["opposers"] > 0 or record["contradicts"])]


@dataclass
class Corpus:
    """The C6 claim records (by short id), the effective C7 plan and C8 assignments of one
    run, the claims withheld from the wiki now, and the files they were read from (by role:
    ``claims``, ``entities``, ``audit``, ``contradictions`` when it exists, ``plan``,
    ``assignments``)."""

    claims: dict[str, Record]
    plan: dict[str, Any]
    assignments: dict[str, dict[str, Any]]
    sources: dict[str, Path]
    skipped: list[str] = field(default_factory=list)
    withheld: frozenset[str] = frozenset()

    @classmethod
    def load(cls, sources: ClaimSources = ClaimSources(), plan_path: Path | None = None,
             assignments_path: Path | None = None) -> Corpus:
        plan_path = plan_path or resolve(PLAN)
        assignments_path = assignments_path or resolve(ASSIGNMENTS)
        claims = load_claim_records(sources)
        plan = _read(plan_path, "plan (step 6)")
        errors = validation_errors(plan)
        if errors:
            raise PlanError([f"{plan_path} is not a valid C7 plan; fix it in the plan tool", *errors])
        assignments = _read(assignments_path, "assignments (step 6b)")["assignments"]
        files = {**sources.files(), "plan": plan_path, "assignments": assignments_path}
        corpus = cls({claim["short_id"]: claim for claim in claims}, plan, assignments, files,
                     withheld=withheld_ids(claims, sources))
        corpus.skipped = [sid for sid in corpus.claims if sid not in assignments]
        return corpus

    @property
    def as_of(self) -> str:
        return max((claim["last_date"] for claim in self.claims.values()), default="")

    def placed(self) -> dict[str, dict[str, Any]]:
        """Assignment records of current claims only (stale ids in C8 and withheld claims are
        ignored)."""
        return {sid: rec for sid, rec in self.assignments.items()
                if sid in self.claims and sid not in self.withheld}

    def _assigned_page(self, short_id: str) -> str | None:
        record = self.assignments.get(short_id)
        return record["pages"][0] if record and record["pages"] else None

    def primary_page(self, short_id: str) -> str | None:
        """The page a claim is written on (``None``: unplaced or withheld)."""
        return None if short_id in self.withheld else self._assigned_page(short_id)

    def withheld_on(self, page_id: str) -> list[str]:
        """Withheld claims that would be ``main`` claims of the page, in C6 order."""
        return [sid for sid in self.claims
                if sid in self.withheld and self._assigned_page(sid) == page_id]

    def accounted_left_out(self, page_id: str, left_out: Sequence[Mapping[str, str]]
                           ) -> list[dict[str, str]]:
        """A page's ``left_out`` with its withheld claims accounted for (code adds them, with
        reason ``withheld``; the writer never saw them)."""
        return [dict(entry) for entry in left_out if entry["reason"] != WITHHELD] + [
            {"claim": sid, "reason": WITHHELD} for sid in self.withheld_on(page_id)]

    def withheld_list(self) -> list[dict[str, Any]]:
        """Every withheld claim placed on a page: ids and numbers only, never text."""
        return [{"claim": sid, "page_id": page_id,
                 "entity_ids": list(self.claims[sid]["entity_ids"]),
                 "supporter_count": self.claims[sid]["supporter_count"]}
                for sid in self.claims
                if sid in self.withheld and (page_id := self._assigned_page(sid)) is not None]

    def page_claims(self, page_id: str) -> list[str]:
        """The short ids of the claims on the page (main and mention), in C6 order."""
        return claims_on_page(self.placed(), page_id)

    def catalogue(self) -> str:
        """Every page (``id | title | description``) and label (``label:id | title | kind``)."""
        pages = [f"{pid} | {p['title_he']} | {' '.join(p['description'].split())}"
                 for pid, p in self.plan["pages"].items()]
        labels = [f"label:{lid} | {lab['title_he']} | {lab['kind']}"
                  for lid, lab in self.plan["labels"].items()]
        return "\n".join(["<pages>", *pages, "</pages>", "<labels>", *labels, "</labels>"])

    def record(self, short_id: str, page_id: str) -> Record:
        """The compact form of one claim the model reads (and the page fingerprints)."""
        claim = self.claims[short_id]
        primary = self.primary_page(short_id)
        backed = last_backed(claim["by_month"])
        contradicts = []
        for other_id in claim["contradicts"]:
            other_page = self.primary_page(other_id)
            if other_id not in self.claims or other_page is None:
                continue  # unplaced and withheld claims appear nowhere
            on_page = page_id in self.assignments[other_id]["pages"]
            contradicts.append({"id": other_id} if on_page else {
                "id": other_id, "page": other_page,
                "text": " ".join(self.claims[other_id]["claim_text"].split())})
        record: Record = {
            "id": short_id,
            "role": MAIN if primary == page_id else MENTION,
            "primary": primary,
            "stance": claim["stance"],
            "text": " ".join(claim["claim_text"].split()),
            "entities": list(claim["entity_names"]),
            "supporters": claim["supporter_count"],
            "opposers": claim["opposer_count"],
            "first": claim["first_date"],
            "last": claim["last_date"],
            "last_backed": list(backed) if backed else None,
            "contradicts": contradicts,
        }
        if len(claim["by_month"]) > 1:
            record["by_month"] = {m: [c["supporters"], c["opposers"]]
                                  for m, c in sorted(claim["by_month"].items())}
        return record

    def context(self, page_id: str, catalogue: str) -> PageContext | None:
        """The page's context, or None when no claim is on it."""
        ids = self.page_claims(page_id)
        if not ids:
            return None
        page = self.plan["pages"][page_id]
        brief = {"id": page_id, "title": page["title_he"], "description": page["description"],
                 "scope": page["scope"]}
        breadcrumb = tuple(find_node(self.plan, node_id)[0]["title_he"] for node_id in page["path"])
        return PageContext(page_id, brief, breadcrumb,
                           tuple(self.record(sid, page_id) for sid in ids), catalogue, self.as_of,
                           self.withheld)

    def label_shares(self) -> Counter[str]:
        """How many placed claims carry each label (for the label rule's lift)."""
        return Counter(lab for rec in self.placed().values() for lab in rec["labels"])


def writer_fingerprint(model: str) -> str:
    """Who writes the text: a new model or prompt version rewrites the page in full."""
    return fingerprint({"model": model, "prompt_version": PAGES_CONFIG["prompt_version"]})


def page_state(context: PageContext, writer: str) -> dict[str, Any]:
    """What a run stores per page in ``metadata.pages`` to diff the next run against."""
    return {
        "writer": writer,
        "brief": fingerprint(context.brief),
        "claims": {record["id"]: fingerprint(record) for record in context.records},
    }


def input_fingerprint(state: Mapping[str, Any]) -> str:
    """The C9 ``input_fingerprint``: the writer, the page's plan fields and its claim records
    (keyed by short id)."""
    return fingerprint({key: state[key] for key in ("writer", "brief", "claims")})
