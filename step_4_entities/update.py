"""Incremental core of step 4: fold new / changed / removed claims into the current entities.

The state is the CURRENT EDITED entities payload. Only the (name, claim) pairs of the affected
claims are touched, so every manual decision about other claims survives as it is:

* a pair that no longer holds (claim removed, or its text / entity list no longer has the
  name) is dropped from whichever member holds it; a pair that still holds stays where the
  reviewer put it (moved, copied or excluded claims included);
* a new pair for a name the state already knows goes to that name's member (the one with
  the most claims when the reviewer split the name across entities);
* a name nobody has seen becomes a member. With the model stages (``matcher``, see
  ``match`` and ``match_apply``) the model decides where it goes, and the names it could not
  decide follow the spelling rules below and are flagged ``ambiguous``. By spelling, it joins
  the one existing entity it confidently matches (similarity to EVERY member over the
  threshold, context guard included, the same complete linkage as a full clustering),
  otherwise it is clustered with the other new names into new entities; a name matching
  several entities is put in a new entity flagged ``ambiguous`` (``conflict_with`` names the
  candidates);
* with the model stages, the new claims of a known name that members of several entities
  hold (a name with two meanings, or one the reviewer split) are divided between them by the
  model, per claim, instead of all going to the member with the most claims;
* names of entities the reviewer deleted (``deleted_names``) and hard-deleted claims
  (``deleted_claims``) are never brought back;
* members emptied by this update are removed and entities left without members are dropped.

Entities are never re-clustered, renamed or re-statused here; ids of new entities come from
``allocate_entity_id`` (a persisted high-water mark), never from membership.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection
from typing import Any

from step_4_entities.cluster import EntityClusterer, flag_conflict
from step_4_entities.collect import ClaimCorpus, tagged_names
from step_4_entities.match import EntityMatcher
from step_4_entities.match_apply import ModelPlacement, flag_for_review
from step_4_entities.match_senses import SenseJob
from step_4_entities.records import (
    CONTACT_KINDS,
    allocate_entity_id,
    claim_entity_index,
    member_names,
    refresh_entity,
)


# (similarity, signals, matched member name) of a new string against one existing member.
Score = tuple[float, set[str], str]


def entity_score(scores: list[Score]) -> float:
    """How well a new string matches an entity: its WORST score against the members.

    Complete linkage, like the full clustering. In the 80 % / 20 % experiment on the reviewed
    backup, best-member (single) linkage attached 16 of 42 new strings to the wrong entity,
    average linkage 11 of 36, complete linkage 8 of 34, at the same recall.
    """

    return min(sim for sim, _, _ in scores)


class EntityUpdate:
    """One incremental update of an entities payload (mutated in place)."""

    def __init__(
        self,
        state: dict[str, Any],
        corpus: ClaimCorpus,
        clusterer: EntityClusterer,
        matcher: EntityMatcher | None = None,
    ) -> None:
        self.state = state
        self.matcher = matcher
        self.entities: list[dict[str, Any]] = state.setdefault("entities", [])
        self.corpus = corpus
        self.clusterer = clusterer
        self.deleted_claims = set(state.get("deleted_claims") or [])
        self.deleted_names = set(state.get("deleted_names") or [])
        self.touched: dict[int, dict[str, Any]] = {}
        self.stats: Counter[str] = Counter()

    # --- members ----------------------------------------------------------

    def _rebuild(
        self,
        entity: dict[str, Any],
        member: dict[str, Any],
        *,
        remove: set[str] = frozenset(),
        add: set[str] = frozenset(),
    ) -> None:
        """Recompute a member after removing / adding claims, keeping its manual state.

        Contacts are recomputed from the claims, but a value the member did not have before
        only appears when an added claim brings it (a contact the reviewer rejected stays
        rejected).
        """

        name = member["name"]
        excluded = set(member.get("excluded_claim_ids") or []) - remove
        claims = (set(member.get("claim_ids") or []) - remove) | add
        fresh = self.corpus.member(name, claims, excluded_claim_ids=excluded)
        brought = self.corpus.member(name, add) if add else {}
        for key in ("contacts", "contacts_uncertain"):
            allowed = {
                kind: set((member.get(key) or {}).get(kind) or [])
                | set(brought.get("contacts", {}).get(kind) or [])
                | set(brought.get("contacts_uncertain", {}).get(kind) or [])
                for kind in CONTACT_KINDS
            }
            fresh[key] = {
                kind: [v for v in fresh[key][kind] if v in allowed[kind]] for kind in CONTACT_KINDS
            }
        if "excluded_claim_ids" in member:
            fresh["excluded_claim_ids"] = sorted(excluded)
        member.update(fresh)
        self.touched[id(entity)] = entity

    def _make_explicit(self) -> None:
        """Legacy members with ``claim_ids: null`` (by-name) get their claims listed."""

        for entity in self.entities:
            for member in entity.get("members") or []:
                if member.get("claim_ids") is None:
                    claims = self.corpus.claims_for(member["name"]) - self.deleted_claims
                    member["claim_ids"] = sorted(claims)

    def _valid_pairs(self, present: set[str]) -> dict[str, set[str]]:
        """For every known or newly tagged name: the affected claims it now stands for."""

        names = member_names(self.state)
        for claim_id in present:
            names.update(tagged_names(self.corpus.claims_by_id[claim_id]))
        return {name: self.corpus.claims_for(name, within=present) for name in names}

    def _prune(self, affected: set[str], valid: dict[str, set[str]]) -> dict[str, set[str]]:
        """Drop pairs of affected claims that no longer hold; return the pairs still placed."""

        placed: dict[str, set[str]] = {}
        for entity in self.entities:
            for member in entity.get("members") or []:
                name = member["name"]
                held = affected & {
                    *member["claim_ids"],
                    *(member.get("excluded_claim_ids") or []),
                }
                keep = held & valid.get(name, set())
                placed.setdefault(name, set()).update(keep)
                if held - keep:
                    self.stats["pairs_removed"] += len(held - keep)
                    self._rebuild(entity, member, remove=held - keep)
        return placed

    def _place_known(self, valid: dict[str, set[str]], placed: dict[str, set[str]]) -> list[str]:
        """Add new pairs of known names to their member; return the names nobody has seen."""

        primary: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
        holders: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = {}
        for entity in self.entities:
            for member in entity.get("members") or []:
                best = primary.get(member["name"])
                if best is None or (member.get("count") or 0) > (best[1].get("count") or 0):
                    primary[member["name"]] = (entity, member)
                if entity.get("status") != "rejected":
                    holders.setdefault(member["name"], []).append((entity, member))
        unseen: list[str] = []
        shared: dict[str, set[str]] = {}
        for name in sorted(valid):
            missing = valid[name] - placed.get(name, set())
            if not missing:
                continue
            if self.matcher is not None and len(holders.get(name, [])) > 1:
                shared[name] = missing
            elif name in primary:
                entity, member = primary[name]
                self.stats["pairs_added_to_known_names"] += len(missing)
                self._rebuild(entity, member, add=missing)
            elif name in self.deleted_names:
                self.stats["deleted_names_skipped"] += 1
            else:
                unseen.append(name)
        self._divide_known(shared, holders)
        return unseen

    def _divide_known(
        self,
        missing: dict[str, set[str]],
        holders: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]],
    ) -> None:
        """The new claims of known names held in several entities, divided by the model.

        When two of those entities cannot be told apart by their label and names (for example
        two entities that both hold only that name), nobody is asked: the new claims go to the
        member with the most claims, and the entities are flagged."""

        if not missing or self.matcher is None:
            return
        ordered = {
            name: sorted(holders[name], key=lambda h: -(h[1].get("count") or 0))
            for name in missing
        }
        groups = {
            name: tuple(
                (self.matcher.record.group_label([m["name"] for m in e["members"]])
                 or e.get("canonical_name") or "", tuple(e.get("aliases") or []))
                for e, _ in ordered[name]
            )
            for name in missing
        }
        alike = {name for name in missing
                 if len({(label, frozenset(names)) for label, names in groups[name]})
                 < len(groups[name])}
        jobs = [SenseJob(name, tuple(sorted(claims)), groups[name])
                for name, claims in sorted(missing.items()) if name not in alike]
        ids = {name: [e["entity_id"] for e, _ in ordered[name]] for name in missing}
        picked = self.matcher.divide_known(jobs, ids)
        picked.update({name: {c: [0] for c in missing[name]} for name in alike})
        for name, claims in picked.items():
            for index, (entity, member) in enumerate(ordered[name]):
                add = {c for c, targets in claims.items() if index in targets}
                if add:
                    self.stats["pairs_added_to_known_names"] += len(add)
                    self._rebuild(entity, member, add=add)
            for one, _ in ordered[name]:
                for other, _ in ordered[name]:
                    if one is not other:
                        flag_conflict(one, other)
        self.stats["known_names_divided"] += len(picked) - len(alike)
        self.stats["known_names_not_told_apart"] += len(alike)

    def _drop_empty(self) -> None:
        for entity in list(self.touched.values()):
            members = entity.get("members") or []
            kept = [m for m in members if m["claim_ids"] or m.get("excluded_claim_ids")]
            self.stats["members_dropped"] += len(members) - len(kept)
            entity["members"] = kept
            if not kept:
                self.entities.remove(entity)
                del self.touched[id(entity)]
                self.stats["entities_dropped"] += 1

    # --- new names ----------------------------------------------------------

    def _existing(self) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        """Every (entity, member) a new name may join: entities not ``rejected``."""

        return [
            (entity, member)
            for entity in self.entities
            if entity.get("status") != "rejected"
            for member in entity.get("members") or []
        ]

    def _assign(self, new_members: list[dict[str, Any]]) -> None:
        """Place the new members: by the model when there is a ``matcher`` (the names it could
        not decide by spelling, flagged), otherwise by spelling."""

        if self.matcher is None or not new_members:
            self._assign_by_spelling(new_members)
            return
        existing = self._existing()
        result = self.matcher.match(new_members, existing)
        leftovers = ModelPlacement(self, result, existing).place(new_members)
        placed = self._assign_by_spelling(leftovers)
        for member in leftovers:
            flag_for_review(placed[member["name"]])
        self.stats["unjudged_names"] += len(leftovers)

    def _assign_by_spelling(self, new_members: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        """Attach each new member to the one entity it confidently matches, else cluster;
        returns the entity of every new member.

        A new member is compared with every existing member, then (repeatedly) with the new
        members attached in the previous pass, so ``Foo Agency`` can follow ``Foo`` into the
        entity ``Foo`` matched. Entities the reviewer marked ``rejected`` take no new members.
        """

        if not new_members:
            return {}
        existing = self._existing()
        by_key = {id(e): e for e in self.entities}
        index = self.clusterer.index([m for _, m in existing] + new_members)
        owner: dict[int, dict[str, Any]] = {j: e for j, (e, _) in enumerate(existing)}
        offset = len(existing)
        pending = set(range(offset, offset + len(new_members)))
        scores: dict[int, dict[int, list[Score]]] = {k: {} for k in pending}
        conflicts: dict[int, set[int]] = {k: set() for k in pending}
        candidates: dict[int, list[int]] = {k: [] for k in pending}
        threshold = self.clusterer.similarity_threshold
        compare_to = list(owner)
        while compare_to and pending:
            attached: list[int] = []
            for k in sorted(pending):
                for j in compare_to:
                    sim, signals, held = index.pair_signals(k, j)
                    key = id(owner[j])
                    if held:
                        conflicts[k].add(key)
                    scores[k].setdefault(key, []).append((sim, signals, index.raw[j]))
                candidates[k] = [
                    key for key, found in scores[k].items() if entity_score(found) >= threshold
                ]
                if len(candidates[k]) == 1:
                    key = candidates[k][0]
                    sim, signals, matched = max(scores[k][key], key=lambda s: s[0])
                    self._attach(by_key[key], new_members[k - offset], sim, signals, matched)
                    owner[k] = by_key[key]
                    attached.append(k)
            pending -= set(attached)
            self.stats["new_names_attached"] += len(attached)
            compare_to = attached

        leftovers = sorted(pending)
        created = self.clusterer.cluster(
            [new_members[k - offset] for k in leftovers],
            lambda: allocate_entity_id(self.state),
        )
        self.entities.extend(created)
        self.stats["new_entities"] += len(created)
        entity_of_name = {m["name"]: e for e in created for m in e["members"]}
        for k in leftovers:
            owner[k] = entity_of_name[new_members[k - offset]["name"]]
            if len(candidates[k]) > 1:
                self.stats["ambiguous_new_names"] += 1
                conflicts[k] |= set(candidates[k])
        for k in range(offset, offset + len(new_members)):
            for key in conflicts[k] - {id(owner[k])}:
                self.stats["conflicts_flagged"] += 1
                flag_conflict(owner[k], by_key[key])
        return {new_members[k - offset]["name"]: owner[k] for k in range(offset, offset + len(new_members))}

    def _attach(
        self,
        entity: dict[str, Any],
        member: dict[str, Any],
        similarity: float,
        signals: set[str],
        matched: str,
    ) -> None:
        member["attached_by"] = {
            "matched": matched,
            "similarity": round(similarity, 4),
            "signals": sorted(signals),
        }
        entity["members"].append(member)
        entity["merge_signals"] = sorted({*(entity.get("merge_signals") or []), *signals})
        self.touched[id(entity)] = entity

    # --- entry point ----------------------------------------------------------

    def apply(self, affected_claim_ids: Collection[str]) -> dict[str, int]:
        """Fold the affected claims (new, changed or removed ids) into the state."""

        affected = set(affected_claim_ids)
        present = {
            c
            for c in affected
            if c in self.corpus.claims_by_id and c not in self.deleted_claims
        }
        self.stats["claims_processed"] = len(affected)
        self._make_explicit()
        valid = self._valid_pairs(present)
        placed = self._prune(affected, valid)
        unseen = self._place_known(valid, placed)
        self._drop_empty()
        self.stats["new_names"] = len(unseen)
        new_members = [
            self.corpus.member(name, self.corpus.claims_for(name) - self.deleted_claims)
            for name in unseen
        ]
        self._assign([m for m in new_members if m["claim_ids"]])
        for entity in self.touched.values():
            refresh_entity(entity)
        self.state["claim_entities"] = claim_entity_index(
            self.entities, deleted_claims=self.deleted_claims
        )
        return dict(self.stats)

