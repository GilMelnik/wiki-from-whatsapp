"""What the Withheld page shows: the claims the same-complaint check releases, and the ones the
withholding rule still keeps off every page, from the claim records and the check's answers.

``ClaimsData`` is the slow part (the claim records and who backs each claim, about half a second
on the real files), loaded once per change of its files. ``WithheldView`` applies the owner's
"keep withheld" marks to it per request (``same_complaint_release.Check``, whose ``withheld``
is what every step and page treats as withheld now), so a mark shows at once. The backers are identities: they stay in memory here, only their counts are served.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from review_tool.artifacts import ArtifactNode
from review_tool.fs import stamp
from step_6_plan.claim_records import ClaimSources, Record, claim_backers, load_claim_records
from step_7_write_pages.same_complaint_release import (
    ANSWERED,
    MIN_SUPPORTERS,
    NO_CALL,
    UNUSABLE,
    WAITING,
    Check,
    Group,
    entity_name,
    load_answers,
)
from step_7_write_pages.withhold import withholds

# Where a claim of a released group stands.
RELEASED = "released"
KEPT = "kept"  # the owner's mark: never released
PUBLISHED = "published"  # the rule never withheld it
SHORT = "short"  # withheld: without the marked claims, fewer than two people back the group
# Why a claim stays withheld, in this order of precedence.
WHY = (KEPT, WAITING, UNUSABLE, SHORT, "unmatched", "alone")


class ClaimsData:
    """The claim records, their backers and the check's stored answers."""

    def __init__(self, claims: ArtifactNode, entities: ArtifactNode, audit: Path,
                 answers: Path) -> None:
        sources = ClaimSources(claims=claims.effective, entities=entities.effective, audit=audit,
                               same_complaint=answers)
        self.records: list[Record] = load_claim_records(sources)
        self.backers = claim_backers(self.records, audit)
        self.answers = load_answers(sources.same_complaint)
        answers_stamp = stamp(answers)
        self.answered_at = answers_stamp.iso if answers_stamp else None


def _month(record: Mapping[str, Any]) -> str | None:
    date = record["last_date"] or record["first_date"]
    return date[:7] if date else None


@dataclass
class WithheldView:
    """The page's three answers over one ``ClaimsData`` and the current marks."""

    data: ClaimsData
    vetoes: frozenset[str]

    def __post_init__(self) -> None:
        self.check = Check(self.data.records, self.data.backers, self.data.answers, self.vetoes)
        self.by_rule = [r for r in self.data.records if withholds(r)]

    def _claim(self, record: Mapping[str, Any]) -> dict[str, Any]:
        return {"short_id": record["short_id"], "text": record["claim_text"],
                "stance": record["stance"], "supporter_count": record["supporter_count"],
                "month": _month(record), "entity_names": list(record["entity_names"]),
                "kept": record["short_id"] in self.vetoes}

    def summary(self) -> dict[str, Any]:
        states = list(self.check.states.values())
        kept = sum(1 for r in self.by_rule if r["short_id"] in self.vetoes)
        return {
            "min_supporters": MIN_SUPPORTERS,
            "counts": {"withheld": len(self.by_rule), "released": len(self.check.released),
                       "still_withheld": len(self.check.withheld),
                       "kept": kept, "released_groups": len(self.shown_groups())},
            "lists": {state: states.count(state) for state in (ANSWERED, WAITING, UNUSABLE)},
            "answered_at": self.data.answered_at,
        }

    def shown_groups(self) -> list[Group]:
        """The groups that release a claim, or would without the owner's marks."""

        return [g for g in self.check.groups if g.people_unmarked >= MIN_SUPPORTERS
                and any(withholds(self.check.records[s]) for s in g.claims)]

    def _status(self, short_id: str) -> str:
        if short_id in self.check.released:
            return RELEASED
        if short_id in self.vetoes:
            return KEPT
        return SHORT if withholds(self.check.records[short_id]) else PUBLISHED

    def released_entities(self) -> list[dict[str, Any]]:
        """Per entity, its shown groups with their claims; the most released first."""

        entities: dict[str, dict[str, Any]] = {}
        for group in self.shown_groups():
            claims = [{**self._claim(self.check.records[s]), "status": self._status(s)}
                      for s in group.claims]
            entity = entities.setdefault(group.entity_id, {
                "entity_id": group.entity_id,
                "entity_name": entity_name(group.entity_id, self.check.lists[group.entity_id]),
                "released": 0, "groups": []})
            entity["groups"].append({"label": group.label, "people": group.people,
                                     "people_unmarked": group.people_unmarked,
                                     "claims": claims})
            entity["released"] += len(group.released)
        return sorted(entities.values(), key=lambda e: (-e["released"], e["entity_name"]))

    def _why(self, record: Mapping[str, Any]) -> str:
        short_id = record["short_id"]
        states = {self.check.states[e] for e in record["entity_ids"]}
        grouped = any(short_id in g.claims for g in self.check.groups)
        reasons = {KEPT: short_id in self.vetoes, WAITING: WAITING in states,
                   UNUSABLE: UNUSABLE in states, SHORT: grouped,
                   "unmatched": ANSWERED in states, "alone": states == {NO_CALL}}
        return next(why for why in WHY if reasons[why])

    def still_withheld(self, query: str) -> list[dict[str, Any]]:
        """The claims withheld now (the rule's, minus what the check releases), matching
        ``query`` (text or entity name, any case), in the order of the claims file."""

        words = query.casefold().split()
        rows = []
        for record in self.by_rule:
            if record["short_id"] not in self.check.withheld:
                continue
            haystack = " ".join([record["claim_text"], *record["entity_names"]]).casefold()
            if all(word in haystack for word in words):
                rows.append({**self._claim(record), "why": self._why(record)})
        return rows
