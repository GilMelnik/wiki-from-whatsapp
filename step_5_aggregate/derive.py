"""The numbers a group carries, recomputed from its members on every run.

People come from the private audit (``supporters`` / ``opposers`` events of step 3, one per
person, message and way of backing: own message or reaction). A person is counted once per
group, even when they backed several member claims. A person found on both sides of one group
is counted on the side of their latest event (they changed their mind); on the same day the
opposing side wins, so a dispute is never hidden. ``by_month`` applies the same rule inside
each month (``YYYY-MM`` of the event date). Reactions the owner classed as neutral (❤️, 🙏 …)
never reach the audit, so they count for nobody. ``participant_count`` is the number of
distinct people who wrote in the conversations the members come from (the audit's
``participants`` of each member, united: a person in two of them counts once); it is ``None``
when a member's audit record predates that field. The public file carries counts only.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np

from step_5_aggregate.groups import Group, claim_order
from step_5_aggregate.inputs import StepInputs
from step_5_aggregate.similarity import AGGREGATE_CONFIG, ClaimScorer

_SIDES = ("supporters", "opposers")
# What a reviewer sets by hand and a run keeps (besides id and members); see ``refresh`` and
# ``link_contradictions``.
MANUAL_KEYS = ("merged_from", "representative_manual", "contradicts_manual", "contradicts_rejected")


def _sides(events: Iterable[tuple[str, str, str]]) -> dict[str, int]:
    """``{supporters, opposers}`` distinct-people counts from ``(person, date, side)`` events."""

    latest: dict[str, tuple[str, bool]] = {}
    for person, date, side in events:
        key = (date, side == "opposers")
        if person not in latest or key > latest[person]:
            latest[person] = key
    opposers = sum(1 for _, is_opposer in latest.values() if is_opposer)
    return {"supporters": len(latest) - opposers, "opposers": opposers}


def people(member_ids: Iterable[str], audit: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """``supporter_count``, ``opposer_count`` and ``by_month`` over all member claims."""

    events = [
        (str(event["person"]), str(event.get("date") or ""), side)
        for claim_id in member_ids
        for side in _SIDES
        for event in (audit.get(claim_id) or {}).get(side) or []
        if event.get("person")
    ]
    months: dict[str, list[tuple[str, str, str]]] = {}
    for event in events:
        if event[1]:
            months.setdefault(event[1][:7], []).append(event)
    totals = _sides(events)
    return {
        "supporter_count": totals["supporters"],
        "opposer_count": totals["opposers"],
        "by_month": {month: _sides(months[month]) for month in sorted(months)},
    }


def participant_count(member_ids: Iterable[str], audit: Mapping[str, Mapping[str, Any]]) -> int | None:
    """Distinct people who wrote in the members' threads, or ``None`` when one is unknown."""

    people_seen: set[str] = set()
    for claim_id in member_ids:
        participants = (audit.get(claim_id) or {}).get("participants")
        if participants is None:
            return None
        people_seen.update(participants)
    return len(people_seen)


class GroupDeriver:
    """Fills every derived field of a group from the run's inputs."""

    def __init__(self, inputs: StepInputs, scorer: ClaimScorer) -> None:
        self.inputs = inputs
        self.scorer = scorer

    def representative(self, group: Group) -> str:
        """A manual choice that is still a member stays. Otherwise: the member with the most
        supporters, then the most central one (mean cosine to the others), then the oldest."""

        members = group["member_claim_ids"]
        chosen = group.get("representative_claim_id")
        if group.get("representative_manual") and chosen in members:
            return chosen
        group.pop("representative_manual", None)
        claims = self.inputs.claims
        rank = {cid: k for k, cid in enumerate(claim_order(claims, members))}

        def key(claim_id: str) -> tuple[int, float, int]:
            others = [m for m in members if m != claim_id]
            centrality = float(np.mean(self.scorer.cosines(claim_id, others))) if others else 0.0
            return (int(claims[claim_id].get("supporter_count") or 0), centrality, -rank[claim_id])

        return max(members, key=key)

    def refresh(self, group: Group) -> Group:
        """A new dict with the group's kept fields (id, members, ``MANUAL_KEYS``) and freshly
        derived ones, in the C6 key order (``contradicts`` is left
        empty for ``link_contradictions``)."""

        claims = self.inputs.claims
        members = claim_order(claims, group["member_claim_ids"])
        group["member_claim_ids"] = members
        rep = self.representative(group)
        entity_ids = sorted({e for m in members for e in self.inputs.claim_entities.get(m, [])})
        first = [str(claims[m]["first_date"]) for m in members if claims[m].get("first_date")]
        last = [str(claims[m]["last_date"]) for m in members if claims[m].get("last_date")]
        counts = people(members, self.inputs.audit)
        out: Group = {
            "group_id": group["group_id"],
            "claim_text": claims[rep].get("claim_text") or "",
            "representative_claim_id": rep,
            "stance": claims[rep].get("stance") or "",
            "member_claim_ids": members,
            "entities": entity_ids,
            "entity_names": [self.inputs.entity_names.get(e, e) for e in entity_ids],
            "supporter_count": counts["supporter_count"],
            "opposer_count": counts["opposer_count"],
            "first_date": min(first) if first else None,
            "last_date": max(last) if last else None,
            "by_month": counts["by_month"],
            "thread_count": len({claims[m].get("thread_id") for m in members}),
            "participant_count": participant_count(members, self.inputs.audit),
            "contradicts": [],
        }
        for key in MANUAL_KEYS:
            if group.get(key):
                out[key] = group[key]
        return out


@dataclass(frozen=True)
class CounterLinks:
    """What the claims' ``counters`` (step 3: a member disputed another) mean for the groups."""

    group_pairs: tuple[tuple[str, str], ...]  # groups whose members dispute each other
    # Claim pairs that dispute each other inside one group: only a reviewer's merge puts them
    # there (the grouping never does), and the merge wins: no link.
    within_group: tuple[tuple[str, str], ...]


def counter_links(groups: list[Group], scorer: ClaimScorer) -> CounterLinks:
    """The group pairs linked by the claims' counter links (claims in no group are ignored)."""

    group_of = {cid: g["group_id"] for g in groups for cid in g["member_claim_ids"]}
    pairs: set[tuple[str, str]] = set()
    within: list[tuple[str, str]] = []
    for a, b in scorer.counter_pairs():
        group_a, group_b = group_of.get(a), group_of.get(b)
        if group_a is None or group_b is None:
            continue
        if group_a == group_b:
            within.append((a, b))
        else:
            pairs.add((min(group_a, group_b), max(group_a, group_b)))
    return CounterLinks(tuple(sorted(pairs)), tuple(within))


def link_contradictions(groups: list[Group], scorer: ClaimScorer) -> int:
    """Set ``contradicts`` on every group (symmetric); returns the number of links.

    Two groups contradict when a member of one disputes a member of the other (C3
    ``counters``, recorded by step 3 where one member answered another; see
    ``counter_links``), or when a member of one and a member of the other have opposite
    stances, mention a common canonical entity and reach ``aggregate.contradiction_threshold``
    cosine. Embeddings cannot tell "X is great" from "X is bad" well, so the second rule is
    kept to stance-opposed pairs only, and conservative.

    A reviewer's overrides then apply, both symmetric: ``contradicts_manual`` (group ids) adds
    links, ``contradicts_rejected`` removes links. An id that was merged away stands for the
    group that holds it in ``merged_from``; an id no group holds any more is ignored.
    """

    bar = float(AGGREGATE_CONFIG["contradiction_threshold"])
    group_of = {cid: g["group_id"] for g in groups for cid in g["member_claim_ids"]}
    pos_stance, neg_stance = AGGREGATE_CONFIG["opposing_stances"]
    side_a = [c for c in group_of if scorer.stances[scorer.row[c]] == pos_stance]
    side_b = [c for c in group_of if scorer.stances[scorer.row[c]] == neg_stance]
    links: dict[str, set[str]] = {g["group_id"]: set() for g in groups}
    for claim_id in side_a:
        if not side_b:
            break
        row = scorer.row[claim_id]
        cos = scorer.cosines(claim_id, side_b)
        for k in np.flatnonzero(cos >= bar):
            other = side_b[k]
            same_group = group_of[claim_id] == group_of[other]
            if not same_group and scorer.shares_entity(row, scorer.row[other]):
                links[group_of[claim_id]].add(group_of[other])
                links[group_of[other]].add(group_of[claim_id])
    for group_a, group_b in counter_links(groups, scorer).group_pairs:
        links[group_a].add(group_b)
        links[group_b].add(group_a)
    _apply_overrides(groups, links)
    for group in groups:
        group["contradicts"] = sorted(links[group["group_id"]])
    return sum(len(v) for v in links.values()) // 2


def _apply_overrides(groups: list[Group], links: dict[str, set[str]]) -> None:
    """Add the manual links, then drop the rejected ones (see ``link_contradictions``)."""

    holder = {
        gid: g["group_id"] for g in groups for gid in (g["group_id"], *(g.get("merged_from") or []))
    }
    for key, add in (("contradicts_manual", True), ("contradicts_rejected", False)):
        for group in groups:
            own = group["group_id"]
            for other in group.get(key) or []:
                target = holder.get(other)
                if target is None or target == own:
                    continue
                for a, b in ((own, target), (target, own)):
                    if add:
                        links[a].add(b)
                    else:
                        links[a].discard(b)
