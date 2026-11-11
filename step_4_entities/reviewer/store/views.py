"""Read views of the entity store: summary, list page, queue, entity detail, member claims."""

from __future__ import annotations

from collections import Counter
from typing import Any

from step_4_entities.kinds import known_kind
from step_4_entities.reviewer.store.helpers import (
    EntityView,
    attached_members,
    contact_count,
    member_total,
)
from utils.config import ENTITIES_CONFIG

PAGE_SIZE = ENTITIES_CONFIG["sample_claims_per_member"]

_SORT_KEYS = {
    "count": lambda item: item["total_count"],
    "size": lambda item: item["member_count"],
    "score": lambda item: item["score"] or 0,
    "id": lambda item: item["entity_id"],
    "name": lambda item: item["canonical_name"].casefold(),
}


def _matches(item: dict[str, Any], view: EntityView) -> bool:
    if view.status != "all" and item["status"] != view.status:
        return False
    if view.flag == "attached" and not item["attached_count"]:
        return False
    if view.flag == "conflict" and not item["conflict_count"]:
        return False
    if view.flag == "uncertain" and not item["uncertain_count"]:
        return False
    if view.flag == "multi" and item["member_count"] < 2:
        return False
    needle = view.query.strip().casefold()
    if not needle:
        return True
    names = [item["canonical_name"], *item["aliases"]]
    return item["entity_id"] == needle or any(needle in name.casefold() for name in names)


class ViewsMixin:
    """Relies on ``EntityStore``: ``entities``, ``claims``, ``aggregations``, ``annotator``,
    ``deleted_claims``, ``deleted_names``, ``source``, ``edited_path``, ``find``,
    ``member_of`` and ``text_of``."""

    def list_item(self, entity: dict[str, Any]) -> dict[str, Any]:
        return {
            "entity_id": entity["entity_id"],
            "canonical_name": entity.get("canonical_name") or "",
            "status": entity.get("status", "suggested"),
            "type": entity.get("type"),
            "kind": known_kind(entity),
            "member_count": len(entity.get("members") or []),
            "total_count": member_total(entity),
            "aliases": entity.get("aliases") or [],
            "score": entity.get("score"),
            "attached_count": len(attached_members(entity)),
            "conflict_count": len(entity.get("conflict_with") or []),
            "uncertain_count": contact_count(entity.get("contacts_uncertain")),
        }

    def _view_items(self, view: EntityView) -> list[dict[str, Any]]:
        """The list items of the view, in its order (ties by id, so pages are stable)."""

        items = [item for e in self.entities if _matches(item := self.list_item(e), view)]
        items.sort(key=lambda item: item["entity_id"])
        items.sort(key=_SORT_KEYS[view.sort], reverse=view.order == "desc")
        return items

    def view_ids(self, view: EntityView) -> list[str]:
        return [item["entity_id"] for item in self._view_items(view)]

    def page(self, view: EntityView, offset: int, limit: int) -> dict[str, Any]:
        items = self._view_items(view)
        return {
            "items": items[offset : offset + limit],
            "total": len(items),
            "offset": offset,
            "limit": limit,
        }

    def queue(self, entity_id: str, view: EntityView) -> dict[str, Any]:
        """Where the entity sits in the view (``position`` None when the view does not show it)."""

        ids = self.view_ids(view)
        if entity_id not in ids:
            return {"position": None, "total": len(ids), "prev_id": None, "next_id": None}
        pos = ids.index(entity_id)
        return {
            "position": pos,
            "total": len(ids),
            "prev_id": ids[pos - 1] if pos > 0 else None,
            "next_id": ids[pos + 1] if pos + 1 < len(ids) else None,
        }

    def next_after_removal(self, ids_before: list[str], entity_id: str) -> str | None:
        """The entity to show once ``entity_id`` left the view: the one now in its place."""

        if entity_id not in ids_before:
            return ids_before[0] if ids_before else None
        pos = ids_before.index(entity_id)
        existing = {e["entity_id"] for e in self.entities}
        rest = [i for i in ids_before if i != entity_id and i in existing]
        return rest[min(pos, len(rest) - 1)] if rest else None

    def summary(self) -> dict[str, Any]:
        statuses = Counter(e.get("status", "suggested") for e in self.entities)
        return {
            "file": self.source.name,
            "edited_exists": self.edited_path.is_file(),
            "entity_count": len(self.entities),
            "multi_member_count": sum(1 for e in self.entities if len(e.get("members") or []) > 1),
            "by_status": dict(statuses),
            "attached_members": sum(len(attached_members(e)) for e in self.entities),
            "attached_entities": sum(1 for e in self.entities if attached_members(e)),
            "conflict_entities": sum(1 for e in self.entities if e.get("conflict_with")),
            "uncertain_entities": sum(
                1 for e in self.entities if contact_count(e.get("contacts_uncertain"))
            ),
            "deleted_names": len(self.deleted_names),
            "deleted_claims": len(self.deleted_claims),
            "aggregations": len(self.aggregations.groups),
            "analysis": self.claims.analysis_counts,
        }

    # --- detail ------------------------------------------------------------

    def _visible_claims(self, member: dict[str, Any]) -> list[str]:
        excluded = set(member.get("excluded_claim_ids") or [])
        return [
            c
            for c in member.get("claim_ids") or []
            if c not in excluded
            and c not in self.deleted_claims
            and not self.aggregations.is_hidden(c)
        ]

    def _claims_page(
        self, entity: dict[str, Any], member: dict[str, Any], offset: int, limit: int
    ) -> dict[str, Any]:
        own = {*(entity.get("aliases") or []), entity.get("canonical_name") or ""}
        ids = self._visible_claims(member)
        claims = []
        for claim_id in ids[offset : offset + limit]:
            view = self.annotator.annotate(claim_id, member["name"], entity["entity_id"], own)
            if view is not None:
                view["aggregation"] = self.aggregations.view(claim_id, self.text_of)
                claims.append(view)
        return {"count": len(ids), "offset": offset, "limit": limit, "claims": claims}

    def _member_view(
        self, entity: dict[str, Any], member: dict[str, Any], index: int
    ) -> dict[str, Any]:
        entity_id = entity["entity_id"]
        also_in = [
            {"entity_id": other, "canonical_name": self.annotator.canonical.get(other, "")}
            for other in self.annotator.holders.get(member["name"], [])
            if other != entity_id
        ]
        return {
            "name": member["name"],
            "member_index": index,
            "is_canonical": member["name"] == entity.get("canonical_name"),
            "claim_count": len(member.get("claim_ids") or []),
            "excluded_claims": [
                {"claim_id": claim_id, "claim_text": self.text_of(claim_id)}
                for claim_id in member.get("excluded_claim_ids") or []
                if claim_id not in self.deleted_claims
            ],
            "contacts": member.get("contacts") or {},
            "contacts_uncertain": member.get("contacts_uncertain") or {},
            "attached_by": member.get("attached_by"),
            "also_in": also_in,
            "claims": self._claims_page(entity, member, 0, PAGE_SIZE),
        }

    def detail(self, entity_id: str, view: EntityView) -> dict[str, Any]:
        entity = self.find(entity_id)
        conflicts = [self.annotator.ref(name) for name in entity.get("conflict_with") or []]
        detail = {
            **self.list_item(entity),
            "canonical_manual": bool(entity.get("canonical_manual")),
            "kind_manual": bool(entity.get("kind_manual")),
            "kind_confidence": entity.get("kind_confidence"),
            "contacts": entity.get("contacts") or {},
            "contacts_uncertain": entity.get("contacts_uncertain") or {},
            "contacts_rejected": entity.get("contacts_rejected") or {},
            "contacts_manual": bool(entity.get("contacts_manual")),
            "merge_signals": entity.get("merge_signals") or [],
            "conflicts": conflicts,
            "members": [
                self._member_view(entity, member, index)
                for index, member in enumerate(entity.get("members") or [])
            ],
        }
        return {"entity": detail, "queue": self.queue(entity_id, view), "page_size": PAGE_SIZE}

    def member_claims(
        self, entity_id: str, name: str, offset: int, limit: int | None = None
    ) -> dict[str, Any]:
        """One page of a member's visible claims (default page size: the detail's)."""

        entity = self.find(entity_id)
        member = self.member_of(entity, name)
        return {"name": name, **self._claims_page(entity, member, offset, limit or PAGE_SIZE)}

    def deleted_names_view(self) -> dict[str, Any]:
        """Names of deleted entities (step 4 never brings them back) and their claim counts."""

        names = [
            {"name": name, "claim_count": len(self.claims.claims_for(name) - self.deleted_claims)}
            for name in sorted(self.deleted_names)
        ]
        return {"names": names}
