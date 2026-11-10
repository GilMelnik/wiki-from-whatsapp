"""What the groups tool shows: the filtered, sorted list, one group in full, the suggestion
queue and the counters. Read-only views over a ``GroupsStore``; nothing here changes data.

The numbers shown are the group's own derived fields (C6, written by step 5 or recomputed by the
store after an edit); member claims are shown with their own text, stance, dates and counts
from the effective claims file.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from step_5_aggregate.groups import Group
from step_5_aggregate.reviewer.suggestions import REVIEW_CONFIG, Suggestion

if TYPE_CHECKING:
    from step_5_aggregate.reviewer.store import GroupsStore

SortKind = Literal["size", "supporters", "last_date", "created"]
SortOrder = Literal["asc", "desc"]


@dataclass(frozen=True)
class GroupQuery:
    """Which groups the list shows, in what order (the owner's current view)."""

    multi: bool = False
    contradictions: bool = False
    suggested: bool = False
    entity: str = ""
    stance: str = ""
    q: str = ""
    sort: SortKind = "size"
    order: SortOrder = "desc"


class GroupViews:
    def __init__(self, store: GroupsStore) -> None:
        self.store = store

    # ------------------------------------------------------------------ pieces

    def _text_of(self, group: Group) -> str:
        claims = self.store.inputs.claims
        parts = [group.get("claim_text") or "", *(group.get("entity_names") or [])]
        parts += [str(claims.get(c, {}).get("claim_text") or "") for c in group["member_claim_ids"]]
        return " ".join(parts).casefold()

    def card(self, group: Group) -> dict[str, Any]:
        """The group as one list row: text, size, people, dates, flags."""

        gid = group["group_id"]
        return {
            "group_id": gid,
            "claim_text": group.get("claim_text") or "",
            "stance": group.get("stance") or "",
            "size": len(group["member_claim_ids"]),
            "supporter_count": group.get("supporter_count", 0),
            "opposer_count": group.get("opposer_count", 0),
            "first_date": group.get("first_date"),
            "last_date": group.get("last_date"),
            "thread_count": group.get("thread_count", 0),
            "entity_names": group.get("entity_names") or [],
            "contradiction_count": len(group.get("contradicts") or []),
            "suggestion_count": len(self.store.partners.get(gid, [])),
            "merged_count": len(group.get("merged_from") or []),
            "representative_manual": bool(group.get("representative_manual")),
        }

    def member(self, claim_id: str, group: Group) -> dict[str, Any]:
        """One member claim with its own numbers (``missing``: gone from the claims file)."""

        inputs = self.store.inputs
        claim = inputs.claims.get(claim_id)
        if claim is None:
            return {"claim_id": claim_id, "missing": True}
        entity_ids = inputs.claim_entities.get(claim_id, [])
        return {
            "claim_id": claim_id,
            "missing": False,
            "claim_text": claim.get("claim_text") or "",
            "stance": claim.get("stance") or "",
            "thread_id": claim.get("thread_id"),
            "first_date": claim.get("first_date"),
            "last_date": claim.get("last_date"),
            "supporter_count": claim.get("supporter_count", 0),
            "opposer_count": claim.get("opposer_count", 0),
            "entity_names": [inputs.entity_names.get(e, e) for e in entity_ids],
            "is_representative": claim_id == group.get("representative_claim_id"),
        }

    def _link(self, group_id: str, *, manual: bool) -> dict[str, Any]:
        group = self.store.by_id.get(group_id)
        if group is None:
            return {"group_id": group_id, "missing": True, "manual": manual}
        return {**self.card(group), "missing": False, "manual": manual}

    def suggestion(self, suggestion: Suggestion) -> dict[str, Any]:
        """A suggested pair with both groups' texts and numbers, for a decision in seconds."""

        def side(group_id: str) -> dict[str, Any]:
            group = self.store.by_id[group_id]
            members = [self.member(c, group) for c in group["member_claim_ids"]]
            return {**self.card(group), "members": members}

        return {
            "key": suggestion.key,
            "a": side(suggestion.a),
            "b": side(suggestion.b),
            "margin": round(suggestion.margin, 4),
            "min_cosine": round(suggestion.min_cosine, 4),
            "max_cosine": round(suggestion.max_cosine, 4),
            "shared_entity": suggestion.shared_entity,
        }

    # ------------------------------------------------------------------ list

    def _matches(self, group: Group, query: GroupQuery, needle: str) -> bool:
        if query.multi and len(group["member_claim_ids"]) < 2:
            return False
        if query.contradictions and not group.get("contradicts"):
            return False
        if query.suggested and group["group_id"] not in self.store.partners:
            return False
        if query.entity and query.entity not in (group.get("entities") or []):
            return False
        if query.stance and group.get("stance") != query.stance:
            return False
        return not needle or needle in self._text_of(group)

    def ordered(self, query: GroupQuery) -> list[Group]:
        needle = query.q.strip().casefold()
        groups = [g for g in self.store.groups if self._matches(g, query, needle)]
        position = self.store.position
        keys = {
            "size": lambda g: (len(g["member_claim_ids"]), g.get("supporter_count", 0)),
            "supporters": lambda g: (g.get("supporter_count", 0), len(g["member_claim_ids"])),
            "last_date": lambda g: str(g.get("last_date") or ""),
            "created": lambda g: position[g["group_id"]],
        }
        key = keys[query.sort]
        # Ties keep creation order in both directions.
        groups.sort(key=lambda g: position[g["group_id"]])
        groups.sort(key=key, reverse=query.order == "desc")
        return groups

    def page(self, query: GroupQuery, offset: int, limit: int) -> dict[str, Any]:
        groups = self.ordered(query)
        return {
            "items": [self.card(g) for g in groups[offset : offset + limit]],
            "total": len(groups),
            "offset": offset,
            "limit": limit,
        }

    # ------------------------------------------------------------------ one group

    def group_detail(self, group_id: str) -> dict[str, Any]:
        group = self.store.group(group_id)
        manual = set(group.get("contradicts_manual") or [])
        return {
            **self.card(group),
            "representative_claim_id": group.get("representative_claim_id"),
            "members": [self.member(c, group) for c in group["member_claim_ids"]],
            "entities": [
                {"entity_id": e, "name": n}
                for e, n in zip(group.get("entities") or [], group.get("entity_names") or [])
            ],
            "timeline": [
                {"month": month, **counts} for month, counts in (group.get("by_month") or {}).items()
            ],
            "contradicts": [
                self._link(gid, manual=gid in manual) for gid in group.get("contradicts") or []
            ],
            "contradicts_rejected": [
                self._link(gid, manual=True)
                for gid in group.get("contradicts_rejected") or []
                if gid in self.store.by_id
            ],
            "merged_from": group.get("merged_from") or [],
            "partners": [
                self.suggestion(s)
                for s in self.store.partners.get(group_id, [])[: int(REVIEW_CONFIG["partner_suggestions"])]
            ],
        }

    def detail(self, group_id: str, query: GroupQuery) -> dict[str, Any]:
        """The group and its place in the current view (``position`` None: not in the view)."""

        group = self.group_detail(group_id)
        ids = [g["group_id"] for g in self.ordered(query)]
        index = ids.index(group_id) if group_id in ids else None
        return {
            "group": group,
            "queue": {
                "position": index,
                "total": len(ids),
                "prev_id": ids[index - 1] if index else None,
                "next_id": ids[index + 1] if index is not None and index + 1 < len(ids) else None,
            },
        }

    # ------------------------------------------------------------------ suggestions, counters

    def suggestions(self, offset: int, limit: int) -> dict[str, Any]:
        ranked: Sequence[Suggestion] = self.store.suggestions
        return {
            "items": [self.suggestion(s) for s in ranked[offset : offset + limit]],
            "total": len(ranked),
            "offset": offset,
            "limit": limit,
            **self._suggestion_status(),
        }

    def _suggestion_status(self) -> dict[str, Any]:
        scorer = self.store.scorer
        band = float(REVIEW_CONFIG["suggestion_band"])
        return {
            "available": scorer is not None,
            "missing_embeddings": self.store.missing_embeddings,
            "band": band,
            "bars": None
            if scorer is None
            else {"shared_entity": scorer.shared_bar, "no_shared_entity": scorer.other_bar},
        }

    def summary(self) -> dict[str, Any]:
        store = self.store
        groups = store.groups
        entity_groups = Counter(e for g in groups for e in g.get("entities") or [])
        names = store.inputs.entity_names
        return {
            "file": store.source.name,
            "edited_exists": store.edited.is_file(),
            "group_count": len(groups),
            "multi_count": sum(1 for g in groups if len(g["member_claim_ids"]) > 1),
            "claim_count": len(store.group_of),
            "ungrouped_claims": sum(1 for c in store.inputs.claims if c not in store.group_of),
            "contradiction_groups": sum(1 for g in groups if g.get("contradicts")),
            "not_same_count": len(store.not_same),
            "stances": dict(Counter(g.get("stance") or "" for g in groups)),
            "entities": [
                {"entity_id": e, "name": names.get(e, e), "groups": n}
                for e, n in sorted(entity_groups.items(), key=lambda item: (-item[1], names.get(item[0], item[0])))
            ],
            "suggestions": {"total": len(store.suggestions), **self._suggestion_status()},
        }
