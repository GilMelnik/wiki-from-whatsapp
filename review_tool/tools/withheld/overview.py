"""What the Withheld page shows: the claims the same-complaint check releases, the ones the owner
released by hand, and the ones the withholding rule still keeps off every page, from the claim
records and the check's answers.

``ClaimsData`` is the slow part (the claim records and who backs each claim, about half a second
on the real files), loaded once per change of its files. ``WithheldView`` applies the owner's
marks ("keep withheld", "released") to it per request (``step_5_same_complaint.release.Check``,
whose ``withheld`` is what every step and page treats as withheld now), so a mark shows at once.
The backers are identities: they stay in memory here, only their counts are served.

A claim the rule withholds is filed under the most specific thing it names: of its entities, the
one the fewest claims of the whole chat name. The page lists the claims by that entity, the most
talked-about entity first, so the complaints about a country or a public body come before the
ones about a small provider, where one released complaint weighs the most.

Step 3 writes one idea per claim, so one message often became several claims. A withheld claim
whose message also carries published claims is one piece of what was said there: the wiki
would tell the rest without it. ``published_siblings`` counts those published claims.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from review_tool.artifacts import ArtifactNode
from review_tool.fs import stamp
from step_5_same_complaint.claim_records import ClaimSources, Record, claim_backers, load_claim_records
from step_5_same_complaint.release import (
    ANSWERED,
    MIN_SUPPORTERS,
    NO_CALL,
    UNUSABLE,
    WAITING,
    Check,
    Group,
    Marks,
    entity_name,
    load_answers,
)
from step_5_same_complaint.withhold import withholds
from utils.json_io import read_json_file

# Where a claim of a released group stands.
RELEASED = "released"
KEPT = "kept"  # the owner's mark: never released
BY_YOU = "by_you"  # the owner released it by hand
PUBLISHED = "published"  # the rule never withheld it
SHORT = "short"  # withheld: without the marked claims, fewer than two people back the group
# Withheld: the check found the same complaint in another claim, but one person made them all.
SAME_PERSON = "same_person"
# Why a claim stays withheld, in this order of precedence.
WHY = (KEPT, WAITING, UNUSABLE, SHORT, SAME_PERSON, "unmatched", "alone")


class ClaimsData:
    """The claim records, their backers and the check's stored answers."""

    def __init__(self, claims: ArtifactNode, entities: ArtifactNode, audit: Path,
                 answers: Path) -> None:
        sources = ClaimSources(claims=claims.effective, entities=entities.effective, audit=audit,
                               same_complaint=answers)
        self.records: list[Record] = load_claim_records(sources)
        self.backers = claim_backers(self.records, audit)
        # The messages each claim rests on, in the order of its conversation.
        self.message_ids: dict[str, list[str]] = {
            claim["claim_id"]: list(claim.get("supporting_message_ids") or [])
            for claim in read_json_file(sources.claims)["claims"]}
        # How many claims name each entity.
        self.named = Counter(e for record in self.records for e in record["entity_ids"])
        # The claims (short ids) that rest on each message.
        self.claims_of_message: dict[str, list[str]] = defaultdict(list)
        for record in self.records:
            for message_id in self.message_ids.get(record["claim_id"], []):
                self.claims_of_message[message_id].append(record["short_id"])
        self.answers = load_answers(sources.same_complaint)
        answers_stamp = stamp(answers)
        self.answered_at = answers_stamp.iso if answers_stamp else None


def _month(record: Mapping[str, Any]) -> str | None:
    date = record["last_date"] or record["first_date"]
    return date[:7] if date else None


@dataclass
class WithheldView:
    """The page's answers over one ``ClaimsData`` and the current marks."""

    data: ClaimsData
    marks: Marks

    def __post_init__(self) -> None:
        self.check = Check(self.data.records, self.data.backers, self.data.answers, self.marks)
        self.by_rule = [r for r in self.data.records if withholds(r)]

    def _claim(self, record: Mapping[str, Any]) -> dict[str, Any]:
        short_id = record["short_id"]
        return {"short_id": short_id, "thread_id": record["thread_id"],
                "text": record["claim_text"], "stance": record["stance"],
                "supporter_count": record["supporter_count"], "month": _month(record),
                "entity_names": list(record["entity_names"]),
                "message_ids": self.data.message_ids.get(record["claim_id"], []),
                "kept": short_id in self.marks.keep_withheld,
                "hand_released": short_id in self.marks.hand_released}

    def summary(self) -> dict[str, Any]:
        states = list(self.check.states.values())
        kept = sum(1 for r in self.by_rule if r["short_id"] in self.marks.keep_withheld)
        return {
            "min_supporters": MIN_SUPPORTERS,
            "counts": {"withheld": len(self.by_rule), "released": len(self.check.released),
                       "released_by_you": len(self.by_you()),
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
        if short_id in self.marks.keep_withheld:
            return KEPT
        if short_id in self.marks.hand_released and withholds(self.check.records[short_id]):
            return BY_YOU
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
        groups = [g for g in self.check.groups if short_id in g.claims]
        reasons = {KEPT: short_id in self.marks.keep_withheld, WAITING: WAITING in states,
                   UNUSABLE: UNUSABLE in states,
                   SHORT: any(g.people_unmarked >= MIN_SUPPORTERS for g in groups),
                   SAME_PERSON: bool(groups),
                   "unmatched": ANSWERED in states, "alone": states == {NO_CALL}}
        return next(why for why in WHY if reasons[why])

    def _filed_under(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """The most specific entity the claim names (the one the fewest claims name; on a tie the
        first by name), with how many claims name it."""

        claims, name = min((self.data.named[entity_id], name) for entity_id, name
                           in zip(record["entity_ids"], record["entity_names"], strict=True))
        return {"filed_under": name, "filed_under_claims": claims}

    def _published_siblings(self, record: Mapping[str, Any]) -> int:
        """How many claims that are not withheld now rest on a message this claim rests on."""

        siblings = {short_id for message_id in self.data.message_ids.get(record["claim_id"], [])
                    for short_id in self.data.claims_of_message[message_id]}
        return len(siblings - {record["short_id"]} - self.check.withheld)

    def by_you(self) -> list[dict[str, Any]]:
        """The claims the rule withholds that the owner released by hand, in the order of the
        claims file."""

        return [{**self._claim(r), "status": self._status(r["short_id"])} for r in self.by_rule
                if r["short_id"] in self.marks.hand_released]

    def rule_withheld(self, query: str) -> list[dict[str, Any]]:
        """The claims the rule withholds, matching ``query`` (text or entity name, any case), in
        the order of the claims file; each says whether it is ``withheld`` now and, when it
        is, ``why`` (else ``None``: the check or the owner released it), the entity it is
        ``filed_under`` with the number of claims that name that entity, and its
        ``published_siblings``."""

        words = query.casefold().split()
        rows = []
        for record in self.by_rule:
            haystack = " ".join([record["claim_text"], *record["entity_names"]]).casefold()
            if all(word in haystack for word in words):
                withheld = record["short_id"] in self.check.withheld
                rows.append({**self._claim(record), **self._filed_under(record),
                             "published_siblings": self._published_siblings(record),
                             "withheld": withheld,
                             "why": self._why(record) if withheld else None})
        return rows

    def release_marks(self, short_id: str, release: bool) -> Marks:
        """The marks after the owner released ``short_id`` (``release``) or withheld it again:
        the fewest marks that leave it published, or withheld, now. Both of its marks are taken
        away first; then it is marked "released" when it would still be withheld, or "keep
        withheld" when the check would still release it."""

        bare = Marks(self.marks.keep_withheld - {short_id}, self.marks.released - {short_id})
        withheld = short_id in Check(self.data.records, self.data.backers, self.data.answers,
                                     bare).withheld
        if release and withheld:
            return Marks(bare.keep_withheld, bare.released | {short_id})
        if not release and not withheld:
            return Marks(bare.keep_withheld | {short_id}, bare.released)
        return bare
