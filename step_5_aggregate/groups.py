"""The groups of one run and the edits a run makes to them: place claims, drop claims, apply
the manual must-links. Membership is only ever changed here or by a reviewer, never
re-derived, so a reviewer's merge, split or move survives every later run.

Groups are kept in creation order: "older" means earlier in the list. A group's id is
``aggregate.group_id_prefix + sha1(seed claim id)[:aggregate.group_id_hash_chars]``, where the
seed is the claim that founded it; the id is never recomputed. An id that was ever used
(also as a ``merged_from`` entry) is never handed out again: the seed is then hashed with a
``#<n>`` suffix.
"""

from __future__ import annotations

import hashlib
from collections.abc import Collection, Iterable, Mapping, Sequence
from typing import Any

import numpy as np

from step_5_aggregate.inputs import MustLink
from step_5_aggregate.similarity import AGGREGATE_CONFIG, ClaimScorer

Group = dict[str, Any]


def claim_order(claims: Mapping[str, Mapping[str, Any]], claim_ids: Iterable[str]) -> list[str]:
    """Claims oldest first (first date, then id): the order in which they are placed."""

    return sorted(claim_ids, key=lambda c: (str(claims[c].get("first_date") or ""), c))


def used_group_ids(groups: Iterable[Group]) -> set[str]:
    """Every id the groups hold or once held (``merged_from``): never to be handed out again."""

    return {gid for g in groups for gid in (g["group_id"], *(g.get("merged_from") or []))}


def allocate_group_id(seed: str, used: Collection[str]) -> str:
    """The id of a group founded by claim ``seed`` that is not in ``used`` (see the module
    docstring). A reviewer tool that splits a group calls this with the split-off part's
    oldest claim and ``used_group_ids(groups)``."""

    prefix = AGGREGATE_CONFIG["group_id_prefix"]
    chars = AGGREGATE_CONFIG["group_id_hash_chars"]
    key, attempt = seed, 0
    while True:
        group_id = prefix + hashlib.sha1(key.encode("utf-8")).hexdigest()[:chars]
        if group_id not in used:
            return group_id
        attempt += 1
        key = f"{seed}#{attempt}"


class GroupBook:
    """The current (edited) groups, with claim -> group lookups kept in step."""

    def __init__(
        self, groups: list[Group], claims: Mapping[str, Mapping[str, Any]], scorer: ClaimScorer
    ) -> None:
        self.groups = groups
        self.claims = claims
        self.scorer = scorer
        self.group_of: dict[str, Group] = {
            cid: group for group in groups for cid in group["member_claim_ids"]
        }
        self.used_ids = used_group_ids(groups)

    def _new_id(self, seed: str) -> str:
        group_id = allocate_group_id(seed, self.used_ids)
        self.used_ids.add(group_id)
        return group_id

    def _open(self, seed: str) -> Group:
        group: Group = {"group_id": self._new_id(seed), "member_claim_ids": [seed]}
        self.groups.append(group)
        self.group_of[seed] = group
        return group

    def _join(self, claim_id: str, group: Group) -> None:
        group["member_claim_ids"].append(claim_id)
        self.group_of[claim_id] = group

    def remove(self, claim_ids: Iterable[str]) -> int:
        """Take claims out of their groups; returns how many were grouped."""

        removed = 0
        for claim_id in claim_ids:
            group = self.group_of.pop(claim_id, None)
            if group is not None:
                group["member_claim_ids"].remove(claim_id)
                removed += 1
        return removed

    def eject_opposed(self, claim_ids: Iterable[str]) -> list[str]:
        """Changed claims whose stance now opposes a fellow member leave their group."""

        out: list[str] = []
        for claim_id in claim_ids:
            group = self.group_of.get(claim_id)
            if group is None:
                continue
            row = self.scorer.row[claim_id]
            if any(
                self.scorer.opposite(row, self.scorer.row[m])
                for m in group["member_claim_ids"]
                if m != claim_id
            ):
                out.append(claim_id)
        self.remove(out)
        return out

    def drop_empty(self) -> int:
        before = len(self.groups)
        self.groups[:] = [g for g in self.groups if g["member_claim_ids"]]
        return before - len(self.groups)

    def _position(self, group: Group) -> int:
        return next(i for i, g in enumerate(self.groups) if g is group)

    def best_group(
        self, claim_id: str, grouped: Sequence[str], position: Mapping[str, int]
    ) -> Group | None:
        """The group every member of which matches ``claim_id``, with the largest smallest
        margin (complete linkage); ``None`` when no group qualifies. Ties: the older group.
        ``grouped`` lists the grouped claims, ``position`` maps each to its index there."""

        if not grouped:
            return None
        margins = self.scorer.margins(claim_id, grouped)
        best: tuple[float, int] | None = None
        chosen: Group | None = None
        seen: set[int] = set()
        for k in np.flatnonzero(margins >= 0):
            group = self.group_of[grouped[k]]
            if id(group) in seen:
                continue
            seen.add(id(group))
            worst = min(margins[position[m]] for m in group["member_claim_ids"])
            if worst < 0:
                continue
            rank = (float(worst), -self._position(group))
            if best is None or rank > best:
                best, chosen = rank, group
        return chosen

    def place(
        self, claim_ids: Iterable[str], must_links: Sequence[MustLink] = ()
    ) -> dict[str, int]:
        """Put each claim, oldest first, into the best matching group or a new one of its own.

        A claim of a must-link goes straight to the (oldest) group that already holds another
        claim of the same must-link. Returns ``{joined, opened, must_linked}``.
        """

        partners: dict[str, set[str]] = {}
        for link in must_links:
            for cid in link.claim_ids:
                partners.setdefault(cid, set()).update(link.claim_ids)
        grouped = list(self.group_of)
        position = {cid: k for k, cid in enumerate(grouped)}
        stats = {"joined": 0, "opened": 0, "must_linked": 0}
        for claim_id in claim_order(self.claims, claim_ids):
            linked = [self.group_of[p] for p in partners.get(claim_id, ()) if p in self.group_of]
            if linked:
                target = min(linked, key=self._position)
                stats["must_linked"] += 1
            else:
                target = self.best_group(claim_id, grouped, position)
                stats["joined" if target is not None else "opened"] += 1
            if target is None:
                self._open(claim_id)
            else:
                self._join(claim_id, target)
            position[claim_id] = len(grouped)
            grouped.append(claim_id)
        return stats

    def apply_must_links(
        self, links: Sequence[MustLink], applied: Mapping[str, str]
    ) -> tuple[dict[str, str], int]:
        """Bring together the claims of every must-link that is new or changed since it was
        last applied (``applied``: link id -> fingerprint); an unchanged one is left alone, so
        a reviewer who split it later in the step-5 reviewer is not overruled.

        The claims move into the oldest group holding any of them; a group emptied by the
        move counts as merged into it (``merged_from``); the must-link's representative
        becomes the group's (manual) representative. Returns the new ``applied`` map and the
        number of must-links applied now.
        """

        out: dict[str, str] = {}
        count = 0
        for link in links:
            out[link.link_id] = link.fingerprint
            if applied.get(link.link_id) == link.fingerprint:
                continue
            members = [cid for cid in link.claim_ids if cid in self.group_of]
            if not members:
                continue
            count += 1
            target = min((self.group_of[c] for c in members), key=self._position)
            for claim_id in members:
                source = self.group_of[claim_id]
                if source is target:
                    continue
                self.remove([claim_id])
                self._join(claim_id, target)
                if not source["member_claim_ids"]:
                    merged = target.setdefault("merged_from", [])
                    merged.extend([source["group_id"], *source.get("merged_from", [])])
            if self.group_of.get(link.representative) is target:
                target["representative_claim_id"] = link.representative
                target["representative_manual"] = True
        return out, count


def group_from_scratch(
    claims: Mapping[str, Mapping[str, Any]],
    scorer: ClaimScorer,
    must_links: Sequence[MustLink] = (),
) -> list[list[str]]:
    """Member lists of a fresh grouping of every claim (what ``--rebuild`` would build)."""

    book = GroupBook([], claims, scorer)
    book.place(list(claims), must_links)
    book.apply_must_links(must_links, {})
    book.drop_empty()
    return [group["member_claim_ids"] for group in book.groups]
