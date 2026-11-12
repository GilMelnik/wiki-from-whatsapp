"""The claim groups under review: loaded from disk, edited by hand, saved to the edited file.

What may be edited follows step 5's rules (C6, task-22 "For task 34"): only membership and the
manual fields. ``groups`` is never reordered (list order = creation order). A merge keeps the
older group (earlier in the list), unions the members and records the other id and its own
``merged_from`` in the survivor's ``merged_from``. A split appends a new group whose id is
``allocate_group_id(oldest claim of the part, used_group_ids(groups))``. A move moves one claim
id between ``member_claim_ids`` lists (a source left empty counts as merged into the target).
A representative choice sets ``representative_claim_id`` + ``representative_manual``. Manual
contradiction links live in ``contradicts_manual`` / ``contradicts_rejected``.

After every edit the derived fields of the touched groups are recomputed with step 5's own
``GroupDeriver``, and every group's ``contradicts`` with ``link_contradictions``, so the numbers
shown are those the next run would write. "Not the same" decisions are kept in the top-level
``not_same`` list of the edited file (claim ids of both sides; a split records one too, so the
parts are not suggested again); step 5 re-runs carry top-level keys over untouched.

Nothing here calls a paid API: vectors come from the embedding cache (``suggestions``). Edits
need every touched member's claim and vector; when step 5 has not run on the current claims
yet, an edit is refused with a hint to run it.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from review_tool.artifacts import ArtifactNode
from step_5_aggregate.derive import GroupDeriver, link_contradictions
from step_5_aggregate.groups import Group, allocate_group_id, claim_order, used_group_ids
from step_5_aggregate.inputs import load_inputs
from step_5_aggregate.reviewer.disputes import Disputes
from step_5_aggregate.reviewer.placement import Placement
from step_5_aggregate.reviewer.suggestions import (
    CandidatePairs,
    EmbeddingsMissing,
    Suggestion,
    cached_vectors,
    rank_suggestions,
    rejected_pairs,
)
from step_5_aggregate.reviewer.views import GroupViews
from step_5_aggregate.similarity import ClaimScorer
from step_7_write_pages.withhold import withheld_ids
from utils.json_io import write_json_file

NOT_SAME_KEY = "not_same"
LINK_KEYS = ("contradicts_manual", "contradicts_rejected")


class GroupNotFound(Exception):
    """No group (or decision) has this id (any more)."""


class EditConflict(Exception):
    """The edit does not fit the current groups; the message says why (English, for the UI)."""


def _read(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _set_or_drop(group: Group, key: str, ids: Iterable[str]) -> None:
    """Store ``ids`` (deduplicated, in order) under ``key``; an empty list removes the key."""

    kept = list(dict.fromkeys(ids))
    if kept:
        group[key] = kept
    else:
        group.pop(key, None)


class GroupsStore:
    """The groups file (edited copy if present), step 5's inputs, vectors and suggestions,
    and (optional, read only) the plan and assignments that say on which page a group is."""

    def __init__(
        self,
        groups: ArtifactNode,
        claims: ArtifactNode,
        entities: ArtifactNode,
        audit_path: Path,
        *,
        plan: ArtifactNode | None = None,
        assignments: ArtifactNode | None = None,
    ) -> None:
        self.edited = groups.edited
        self.source = groups.effective
        if not self.source.is_file():
            raise FileNotFoundError(
                f"There are no claim groups yet ({self.source.name}): run step 5, then reload"
            )
        claims_file = claims.effective
        if not claims_file.is_file():
            raise FileNotFoundError(
                f"The claims file {claims_file.name} is missing: run step 3, then step 5"
            )
        self.payload = _read(self.source)
        self.groups: list[Group] = self.payload.setdefault("groups", [])
        self.not_same: list[dict[str, Any]] = self.payload.setdefault(NOT_SAME_KEY, [])
        self.inputs = load_inputs(claims_file, entities.effective, audit_path)
        texts = [str(c.get("claim_text") or "") for c in self.inputs.claims.values()]
        self.scorer: ClaimScorer | None = None
        self.candidates: CandidatePairs | None = None
        self.missing_embeddings = 0
        try:
            vectors = cached_vectors(texts)
        except EmbeddingsMissing as exc:
            self.missing_embeddings = exc.count
        else:
            self.scorer = ClaimScorer(self.inputs.claims, self.inputs.claim_entities, vectors)
            self.candidates = CandidatePairs(self.inputs, self.scorer)
        self.placement = Placement(
            plan.effective if plan else None, assignments.effective if assignments else None
        )
        self.views = GroupViews(self)
        self._reindex()

    # ------------------------------------------------------------------ indices

    def _reindex(self) -> None:
        self.by_id: dict[str, Group] = {g["group_id"]: g for g in self.groups}
        self.position = {g["group_id"]: k for k, g in enumerate(self.groups)}
        self.group_of = {c: g["group_id"] for g in self.groups for c in g["member_claim_ids"]}
        self.holder = {
            old: g["group_id"] for g in self.groups for old in g.get("merged_from") or []
        }
        self.suggestions: list[Suggestion] = (
            rank_suggestions(self.groups, self.candidates, rejected_pairs(self.not_same))
            if self.candidates is not None
            else []
        )
        self.partners: dict[str, list[Suggestion]] = {}
        for suggestion in self.suggestions:
            for gid in (suggestion.a, suggestion.b):
                self.partners.setdefault(gid, []).append(suggestion)
        # Kept off every page by the owner's rule, decided live from the groups alone.
        self.withheld = withheld_ids(self.groups)
        self.disputes = Disputes(self.groups, self.scorer)

    def group(self, group_id: str) -> Group:
        group = self.by_id.get(group_id)
        if group is not None:
            return group
        if group_id in self.holder:
            raise GroupNotFound(f"The group {group_id} was merged into {self.holder[group_id]}")
        raise GroupNotFound(f"There is no group {group_id}")

    # ------------------------------------------------------------------ saving

    def _require_editable(self, groups: Iterable[Group]) -> ClaimScorer:
        if self.scorer is None:
            raise EditConflict(
                f"Claims without a vector yet: {self.missing_embeddings}. Run step 5 before editing"
            )
        unknown = [
            c for g in groups for c in g["member_claim_ids"] if c not in self.inputs.claims
        ]
        if unknown:
            raise EditConflict(
                f"The group has a claim that is no longer in the claims file ({unknown[0]}): "
                "run step 5 before editing"
            )
        return self.scorer

    def _save(self, touched: Sequence[str], scorer: ClaimScorer) -> None:
        """Recompute the touched groups and every contradiction link, then write the edited
        file (created by the first edit) and rebuild the indices and suggestions."""

        deriver = GroupDeriver(self.inputs, scorer)
        position = {g["group_id"]: k for k, g in enumerate(self.groups)}
        for gid in dict.fromkeys(touched):
            if gid in position:
                self.groups[position[gid]] = deriver.refresh(self.groups[position[gid]])
        link_contradictions(self.groups, scorer)
        if not self.not_same:
            self.payload.pop(NOT_SAME_KEY, None)
        write_json_file(self.payload, self.edited)
        self.payload.setdefault(NOT_SAME_KEY, self.not_same)
        self.source = self.edited
        self._reindex()

    @staticmethod
    def _absorb(survivor: Group, other: Group) -> None:
        """``other``'s merge history and manual links move to ``survivor`` (not its members)."""

        survivor["merged_from"] = [
            *(survivor.get("merged_from") or []),
            other["group_id"],
            *(other.get("merged_from") or []),
        ]
        own = {survivor["group_id"], *survivor["merged_from"]}
        for key in LINK_KEYS:
            ids = [*(survivor.get(key) or []), *(other.get(key) or [])]
            _set_or_drop(survivor, key, (gid for gid in ids if gid not in own))

    # ------------------------------------------------------------------ edits

    def merge(self, group_ids: Sequence[str]) -> dict[str, Any]:
        """Merge the groups into the oldest of them; returns its detail and the ids merged."""

        ids = list(dict.fromkeys(group_ids))
        if len(ids) < 2:
            raise EditConflict("A merge needs at least two different groups")
        groups = sorted((self.group(gid) for gid in ids), key=lambda g: self.position[g["group_id"]])
        scorer = self._require_editable(groups)
        survivor, others = groups[0], groups[1:]
        for other in others:
            survivor["member_claim_ids"] = [*survivor["member_claim_ids"], *other["member_claim_ids"]]
            self._absorb(survivor, other)
        gone = {id(g) for g in others}
        self.groups[:] = [g for g in self.groups if id(g) not in gone]
        self._save([survivor["group_id"]], scorer)
        return {
            "group_id": survivor["group_id"],
            "merged": [g["group_id"] for g in others],
            "group": self.views.group_detail(survivor["group_id"]),
        }

    def split(self, group_id: str, claim_ids: Sequence[str]) -> dict[str, Any]:
        """Move ``claim_ids`` out of the group into a new group appended at the end."""

        group = self.group(group_id)
        members = group["member_claim_ids"]
        part_set = set(claim_ids)
        if not part_set:
            raise EditConflict("Pick the claims that move to the new group")
        if not part_set <= set(members):
            raise EditConflict(f"Not every picked claim belongs to the group {group_id}")
        if part_set == set(members):
            raise EditConflict("A split must leave at least one claim in the original group")
        scorer = self._require_editable([group])
        part = claim_order(self.inputs.claims, part_set)
        new_id = allocate_group_id(part[0], used_group_ids(self.groups))
        group["member_claim_ids"] = [c for c in members if c not in part_set]
        self.groups.append({"group_id": new_id, "member_claim_ids": part})
        self._record_not_same(group["member_claim_ids"], part, [group_id, new_id], "split")
        self._save([group_id, new_id], scorer)
        return {
            "group_id": group_id,
            "new_group_id": new_id,
            "group": self.views.group_detail(group_id),
        }

    def move(self, group_id: str, claim_id: str, target_id: str) -> dict[str, Any]:
        """Move one claim into another group; a group left empty counts as merged into it."""

        source, target = self.group(group_id), self.group(target_id)
        if source is target:
            raise EditConflict("The claim is already in this group")
        if claim_id not in source["member_claim_ids"]:
            raise EditConflict(f"The claim {claim_id} is not in the group {group_id}")
        scorer = self._require_editable([source, target])
        source["member_claim_ids"] = [c for c in source["member_claim_ids"] if c != claim_id]
        target["member_claim_ids"] = [*target["member_claim_ids"], claim_id]
        emptied = not source["member_claim_ids"]
        if emptied:
            self._absorb(target, source)
            self.groups.remove(source)
        self._save([target_id] if emptied else [group_id, target_id], scorer)
        return {
            "group_id": None if emptied else group_id,
            "target_id": target_id,
            "group": self.views.group_detail(target_id),
        }

    def set_representative(self, group_id: str, claim_id: str | None) -> dict[str, Any]:
        """Choose the claim whose text represents the group; ``None`` = automatic again."""

        group = self.group(group_id)
        if claim_id is not None and claim_id not in group["member_claim_ids"]:
            raise EditConflict(f"The claim {claim_id} is not in the group {group_id}")
        scorer = self._require_editable([group])
        if claim_id is None:
            group.pop("representative_manual", None)
        else:
            group["representative_claim_id"] = claim_id
            group["representative_manual"] = True
        self._save([group_id], scorer)
        return {"group_id": group_id, "group": self.views.group_detail(group_id)}

    def set_contradiction(self, group_id: str, other_id: str, linked: bool) -> dict[str, Any]:
        """Add a missing contradiction link or remove a wrong one (both groups, symmetric)."""

        group, other = self.group(group_id), self.group(other_id)
        if group is other:
            raise EditConflict("A group cannot contradict itself")
        scorer = self._require_editable([])
        add, drop = LINK_KEYS if linked else reversed(LINK_KEYS)
        for one, two in ((group, other), (other, group)):
            _set_or_drop(one, drop, (gid for gid in one.get(drop) or [] if gid != two["group_id"]))
            _set_or_drop(one, add, [*(one.get(add) or []), two["group_id"]])
        self._save([], scorer)
        return {"group_id": group_id, "group": self.views.group_detail(group_id)}

    def _record_not_same(
        self, claims_a: Sequence[str], claims_b: Sequence[str], group_ids: list[str], source: str
    ) -> dict[str, Any]:
        record = {
            "id": max((int(r.get("id") or 0) for r in self.not_same), default=0) + 1,
            "claims_a": list(claims_a),
            "claims_b": list(claims_b),
            "group_ids": group_ids,
            "source": source,
            "at": _now(),
        }
        self.not_same.append(record)
        return record

    def mark_not_same(self, a: str, b: str) -> dict[str, Any]:
        """Remember that two groups say different things: never suggest them together again."""

        first, second = self.group(a), self.group(b)
        if first is second:
            raise EditConflict("These are the same group")
        scorer = self._require_editable([])
        record = self._record_not_same(
            first["member_claim_ids"], second["member_claim_ids"], [a, b], "suggestion"
        )
        self._save([], scorer)
        return {"record": record}

    def remove_not_same(self, record_id: int) -> dict[str, Any]:
        """Forget one "not the same" decision (undo)."""

        record = next((r for r in self.not_same if r.get("id") == record_id), None)
        if record is None:
            raise GroupNotFound(f"There is no “not the same” decision number {record_id}")
        scorer = self._require_editable([])
        self.not_same.remove(record)
        self._save([], scorer)
        return {"record": record}
