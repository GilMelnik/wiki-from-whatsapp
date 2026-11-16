"""Apply the model's decisions to the entities, in the order of authority: the reviewer's edits
(the current entities, never changed here) > the seed file > the model's groups > spelling.

``ModelPlacement.place`` gets the new members of an ``EntityUpdate`` and its ``MatchResult``:

* names the model put in one group become one entity (signal ``model`` in ``merge_signals``
  when it holds two or more of them); a group that holds an existing name attaches its new
  names to the entity holding that name NOW (``attached_by`` with ``signals: ["model"]`` and
  the matched name), also when that entity has other names; when the group's existing names
  live in several entities, the new names go to the one with the most claims and the others
  are flagged (``flag_conflict``). Only a name ONE entity holds can decide this: new names
  judged the same as only a name several entities hold go to an entity of their own, flagged
  against each of them and recorded as unsure (``shared_name``);
* names the model kept apart stay apart: they never meet the spelling rules;
* a name with two meanings becomes one member per group, with the claims of that meaning (a
  claim about several meanings is in each); every entity of its groups is flagged against the
  others, also when all its claims ended in one of them (a lone one is flagged for review);
  when the per-claim call failed or placed no claim, the claims stay in its seed entry's
  group (else the group whose other names have the most claims) and that entity is flagged;
* an unsure pair (the judge's, a pair cut from an oversized bundle, a pair the screen could not
  answer) stays apart and both entities are flagged;
* a seed entry is kept whole by ``resolve_groups``; the seed's canonical name, type and
  ``status`` are adopted as before (status only when every member comes from that entry, and
  not when the model took claims of one of its names away: then it is ``ambiguous``), and a
  seed alias's second meaning does not count as a member of the entry;
* the names the model did not decide are returned: the caller places them by spelling and
  flags their entities (``status: ambiguous``).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from step_4_entities.cluster import flag_conflict
from step_4_entities.match import MatchResult
from step_4_entities.normalize import normalize_name
from step_4_entities.records import allocate_entity_id
from utils.config import ENTITIES_CONFIG

if TYPE_CHECKING:
    from step_4_entities.update import EntityUpdate

SIGNAL_MODEL = ENTITIES_CONFIG["signals"]["model"]
SOURCE_SHARED_NAME = "shared_name"
Existing = Sequence[tuple[dict[str, Any], dict[str, Any]]]


def flag_for_review(entity: dict[str, Any]) -> None:
    """Show an entity in the page's "needs review" section (a suggested one turns ambiguous)."""

    if entity.get("status", "suggested") == "suggested":
        entity["status"] = "ambiguous"


class ModelPlacement:
    """Places the judged new names of one update (mutates the update's state)."""

    def __init__(self, update: EntityUpdate, result: MatchResult, existing: Existing) -> None:
        self.update = update
        self.result = result
        self.clusterer = update.clusterer
        self.placed: dict[str, list[dict[str, Any]]] = {}
        best: dict[str, tuple[dict[str, Any], int]] = {}
        self.holders: dict[str, list[dict[str, Any]]] = {}
        for entity, member in existing:
            name, count = member["name"], member.get("count") or 0
            if name not in best or count > best[name][1]:
                best[name] = (entity, count)
            self.holders.setdefault(name, []).append(entity)
        self.holder = {name: entity for name, (entity, _) in best.items()}
        self.claims = {name: count for name, (_, count) in best.items()}
        self.group_count = Counter(n for g in result.groups for n in g["names"])
        self.entity_of_group: dict[str, dict[str, Any]] = {}
        self.unsure: list[dict[str, Any]] = []

    def _member(self, members: Mapping[str, dict[str, Any]], name: str,
                group_id: str) -> dict[str, Any] | None:
        """The member of ``name`` in this group: all its claims, or the claims of this meaning."""

        senses = self.result.senses.get(name)
        if senses is None:
            return members[name]
        claims = [c for c, groups in senses.items() if group_id in groups]
        return self.update.corpus.member(name, claims) if claims else None

    def _seed_of(self, name: str, names: Sequence[str]) -> str | None:
        """The seed entry of ``name`` in this group: a shared name counts for its entry only
        where the group holds another name of that entry."""

        seed_id = self.clusterer.norm_to_seed.get(normalize_name(name))
        if seed_id is None or self.group_count[name] == 1:
            return seed_id
        siblings = [n for n in names if n != name
                    and self.clusterer.norm_to_seed.get(normalize_name(n)) == seed_id]
        return seed_id if siblings else None

    def _new_entity(self, members: list[dict[str, Any]], names: Sequence[str],
                    signals: Sequence[str]) -> dict[str, Any]:
        """A new entity for a group. A seed entry the model took claims from (one of its
        names has two meanings) is never adopted as ``accepted``: the owner checks it."""

        entity = self.clusterer.group_entity(
            members, lambda: allocate_entity_id(self.update.state), signals=signals,
            member_seeds=[self._seed_of(m["name"], names) for m in members])
        if entity.get("status") == "accepted" and any(n in self.result.senses for n in names):
            entity["status"] = "ambiguous"
        self.update.entities.append(entity)
        self.update.touched[id(entity)] = entity
        self.update.stats["new_entities"] += 1
        return entity

    def _attach(self, entity: dict[str, Any], member: dict[str, Any], matched: str) -> None:
        index = self.clusterer.index([member, {"name": matched, "claim_ids": []}])
        similarity, _ = index._base_similarity(0, 1)
        self.update._attach(entity, member, similarity, {SIGNAL_MODEL}, matched)
        self.update.stats["new_names_attached"] += 1

    def _place_group(self, group: Mapping[str, Any], members: Mapping[str, dict[str, Any]]) -> None:
        """Place a group's new names. Only an existing name that ONE entity holds and the judge
        listed once tells where they go (an anchor): a name several entities hold (a known
        name with two meanings) never chooses an entity by size."""

        names = group["names"]
        placed = [m for n in names if n in members
                  if (m := self._member(members, n, group["group_id"])) is not None]
        anchors = [n for n in names if n in self.holder and self.group_count[n] == 1
                   and len(self.holders[n]) == 1]
        targets = sorted({id(self.holder[n]): self.holder[n] for n in anchors}.values(),
                         key=lambda e: -sum(m.get("count") or 0 for m in e.get("members") or []))
        if targets:
            self.entity_of_group[group["group_id"]] = targets[0]
        if not placed:
            return
        if not anchors:
            entity = self._new_entity(placed, names, [SIGNAL_MODEL] if len(placed) > 1 else [])
            self.entity_of_group[group["group_id"]] = entity
            for member in placed:
                self.placed.setdefault(member["name"], []).append(entity)
            self._flag_shared_existing(entity, names)
            self._unsure_by_shared_name(entity, placed, names)
            return
        target = targets[0]
        matched = max((n for n in anchors if self.holder[n] is target),
                      key=lambda n: (self.claims[n], n))
        for member in placed:
            self._attach(target, member, matched)
            self.placed.setdefault(member["name"], []).append(target)
        for other in targets[1:]:
            self.update.stats["conflicts_flagged"] += 1
            flag_conflict(target, other)

    def _flag_shared_existing(self, entity: dict[str, Any], names: Sequence[str]) -> None:
        """An existing name the judge gave a second meaning: its entities may be confused."""

        for name in names:
            for other in self.holders.get(name, []) if self.group_count[name] > 1 else []:
                if other is not entity:
                    self.update.stats["conflicts_flagged"] += 1
                    flag_conflict(entity, other)

    def _unsure_by_shared_name(self, entity: dict[str, Any], placed: Sequence[dict[str, Any]],
                               names: Sequence[str]) -> None:
        """New names judged the same as only a name several entities hold: nothing tells which
        entity, so they stay apart, flagged against each, and the pair is recorded as unsure."""

        for name in names:
            holders = self.holders.get(name, [])
            if len(holders) < 2:
                continue
            for other in holders:
                self.update.stats["conflicts_flagged"] += 1
                flag_conflict(entity, other)
            self.unsure += [{"names": sorted([member["name"], name]), "reason": "",
                             "source": SOURCE_SHARED_NAME} for member in placed]

    def _entities_of(self, name: str) -> list[dict[str, Any]]:
        return self.placed.get(name) or self.holders.get(name) or []

    def _flag(self, entities: Sequence[dict[str, Any]]) -> None:
        """Flag every pair of distinct ``entities``; a lone one is flagged for review."""

        unique = list({id(e): e for e in entities}.values())
        if len(unique) == 1:
            flag_for_review(unique[0])
        for i, one in enumerate(unique):
            for other in unique[i + 1:]:
                self.update.stats["conflicts_flagged"] += 1
                flag_conflict(one, other)

    def _flag_pairs(self) -> None:
        for unsure in self.result.unsure:
            a, b = unsure["names"]
            if a in self.placed or b in self.placed:
                involved = [*self._entities_of(a), *self._entities_of(b)]
                if len({id(e) for e in involved}) > 1:
                    self._flag(involved)
        # A name the judge gave two meanings: every entity of its groups, wherever its
        # claims ended up (also all in one of them).
        for name in self.result.senses:
            groups = [g["group_id"] for g in self.result.groups if name in g["names"]]
            self._flag([*(self.entity_of_group[g] for g in groups if g in self.entity_of_group),
                        *self._entities_of(name)])

    def place(self, new_members: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        """Place every judged new member; return the members the model did not decide."""

        members = {m["name"]: m for m in new_members if m["name"] in self.result.judged}
        for group in self.result.groups:
            if any(n in members for n in group["names"]):
                self._place_group(group, members)
        for name in sorted(set(members) - set(self.placed)):
            if name not in self.result.senses:
                entity = self._new_entity([members[name]], [name], [])
                self.placed[name] = [entity]
        self._flag_pairs()
        if self.unsure and self.update.matcher is not None:
            self.update.matcher.record.add_unsure(self.unsure)
        for name in self.result.senses_failed & set(self.placed):
            for entity in self.placed[name]:
                flag_for_review(entity)
        stats = self.update.stats
        stats["model_names"] += len(members)
        stats["two_meaning_names"] += sum(1 for n in members if n in self.result.senses)
        return [m for m in new_members if m["name"] not in members]
