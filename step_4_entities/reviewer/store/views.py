"""Read views, queue and stats for the entity store."""

from __future__ import annotations

from collections import Counter
from typing import Any

from utils.config import ENTITIES_CONFIG
from step_4_entities.reviewer.store.helpers import (
    SortKind,
    SortOrder,
    _member_total,
)


class ViewsMixin:
    """Read-only entity/member/claim views served to the reviewer UI.

    Relies on ``EntityStore`` attributes: ``entities``, ``_claims_by_id``,
    ``_agg_by_claim``, ``_agg_by_rep``, ``_find_entity``, plus the
    ``ClaimsMixin`` helpers (``_claim_ids_for_member``,
    ``_other_entities_in_claim``, ``_related_entities_in_claim``,
    ``_claim_highlights``).
    """

    # --- read views -----------------------------------------------------

    def _enrich(self, entity: dict[str, Any]) -> dict[str, Any]:
        return {
            "entity_id": entity["entity_id"],
            "canonical_name": entity.get("canonical_name", ""),
            "status": entity.get("status", "suggested"),
            "member_count": len(entity.get("members") or []),
            "total_count": _member_total(entity),
            "aliases": entity.get("aliases") or [],
            "contacts": entity.get("contacts") or {},
            "contacts_uncertain": entity.get("contacts_uncertain") or {},
            "contacts_manual": bool(entity.get("contacts_manual")),
            "merge_signals": entity.get("merge_signals") or [],
            "conflict_with": entity.get("conflict_with") or [],
            "score": entity.get("score"),
        }

    def _sorted_entities(
        self,
        *,
        status: str | None = None,
        size_min: int | None = None,
        query: str | None = None,
        sort: SortKind = "count",
        order: SortOrder = "desc",
    ) -> list[dict[str, Any]]:
        items = [self._enrich(e) for e in self.entities]
        if status:
            items = [i for i in items if i["status"] == status]
        if size_min is not None:
            items = [i for i in items if i["member_count"] >= size_min]
        if query:
            needle = query.strip().lower()
            items = [
                i
                for i in items
                if needle in i["canonical_name"].lower()
                or any(needle in a.lower() for a in i["aliases"])
            ]
        reverse = order == "desc"
        key = {
            "count": lambda i: i["total_count"],
            "size": lambda i: i["member_count"],
            "score": lambda i: i["score"] or 0,
        }[sort]
        items.sort(key=key, reverse=reverse)
        return items

    def list_entities(
        self,
        *,
        status: str | None = None,
        size_min: int | None = None,
        query: str | None = None,
        sort: SortKind = "count",
        order: SortOrder = "desc",
        offset: int = 0,
        limit: int = 100,
    ) -> tuple[list[dict[str, Any]], int]:
        items = self._sorted_entities(
            status=status, size_min=size_min, query=query, sort=sort, order=order
        )
        return items[offset : offset + limit], len(items)

    def _is_hidden_by_aggregation(self, claim_id: str) -> bool:
        """A non-representative member of an aggregation is folded away."""

        group = self._agg_by_claim.get(claim_id)
        return group is not None and group.get("representative") != claim_id

    def _visible_claim_ids(self, claim_ids: list[str]) -> list[str]:
        return [c for c in claim_ids if not self._is_hidden_by_aggregation(c)]

    def _aggregation_view(self, claim_id: str) -> dict[str, Any] | None:
        group = self._agg_by_rep.get(claim_id)
        if group is None:
            return None
        ids = group.get("claim_ids") or []
        return {
            "id": group["id"],
            "representative": group.get("representative"),
            "count": len(ids),
            "members": [
                {
                    "claim_id": cid,
                    "claim_text": (self._claims_by_id.get(cid) or {}).get(
                        "claim_text", ""
                    ),
                }
                for cid in ids
            ],
        }

    def _enrich_claim(
        self,
        claim_id: str,
        member_name: str,
        *,
        entity_id: str,
        entity_names: set[str],
    ) -> dict[str, Any] | None:
        claim = self._claims_by_id.get(claim_id)
        if claim is None:
            return None
        other_entities = self._other_entities_in_claim(
            claim,
            current_entity_id=entity_id,
            current_names=entity_names,
        )
        return {
            "claim_id": claim_id,
            "claim_text": claim.get("claim_text", ""),
            "stance": claim.get("stance"),
            "thread_id": claim.get("thread_id"),
            "highlights": self._claim_highlights(
                claim,
                member_name,
                current_entity_id=entity_id,
                current_names=entity_names,
            ),
            "other_entities": other_entities,
            "related_entities": self._related_entities_in_claim(
                claim,
                current_entity_id=entity_id,
                current_names=entity_names,
                mentioned_entity_ids={o["entity_id"] for o in other_entities},
            ),
            "aggregation": self._aggregation_view(claim_id),
        }

    def _member_view(
        self,
        member: dict[str, Any],
        member_index: int,
        *,
        entity_id: str,
        entity_names: set[str],
    ) -> dict[str, Any]:
        name = member["name"]
        claim_ids = self._visible_claim_ids(self._claim_ids_for_member(member))
        samples: list[dict[str, Any]] = []
        for claim_id in claim_ids[: ENTITIES_CONFIG["sample_claims_per_member"]]:
            enriched = self._enrich_claim(
                claim_id, name, entity_id=entity_id, entity_names=entity_names
            )
            if enriched is not None:
                samples.append(enriched)
        return {
            "name": name,
            "member_index": member_index,
            "count": len(claim_ids),
            "claim_ids": member.get("claim_ids"),
            "excluded_claim_ids": member.get("excluded_claim_ids") or [],
            "contacts": member.get("contacts") or {},
            "sample_claims": samples,
        }

    def member_claims(
        self,
        entity_id: str,
        name: str,
        *,
        offset: int = 0,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """One page of a member's visible claims (Req 1 pagination; default page size from
        ``entities.sample_claims_per_member``)."""

        if limit is None:
            limit = ENTITIES_CONFIG["sample_claims_per_member"]

        entity, _ = self._find_entity(entity_id)
        member = next(
            (m for m in entity.get("members") or [] if m["name"] == name), None
        )
        if member is None:
            raise ValueError(f"name not a member of this entity: {name}")
        entity_names = set(entity.get("aliases") or [])
        canonical = entity.get("canonical_name")
        if canonical:
            entity_names.add(canonical)
        claim_ids = self._visible_claim_ids(self._claim_ids_for_member(member))
        claims: list[dict[str, Any]] = []
        for claim_id in claim_ids[offset : offset + limit]:
            enriched = self._enrich_claim(
                claim_id, name, entity_id=entity_id, entity_names=entity_names
            )
            if enriched is not None:
                claims.append(enriched)
        return {
            "name": name,
            "count": len(claim_ids),
            "offset": offset,
            "limit": limit,
            "claims": claims,
        }

    def get_entity(
        self,
        entity_id: str,
        *,
        status: str | None = None,
        size_min: int | None = None,
        query: str | None = None,
        sort: SortKind = "count",
        order: SortOrder = "desc",
    ) -> dict[str, Any]:
        entity, _ = self._find_entity(entity_id)
        detail = self._enrich(entity)
        entity_names = set(entity.get("aliases") or [])
        canonical = entity.get("canonical_name")
        if canonical:
            entity_names.add(canonical)
        detail["members"] = [
            self._member_view(
                m, idx, entity_id=entity_id, entity_names=entity_names
            )
            for idx, m in enumerate(entity.get("members") or [])
        ]
        items = self._sorted_entities(
            status=status, size_min=size_min, query=query, sort=sort, order=order
        )
        ids = [i["entity_id"] for i in items]
        try:
            pos = ids.index(entity_id)
        except ValueError:
            pos = -1
        return {
            "entity": detail,
            "queue": {
                "prev_id": ids[pos - 1] if pos > 0 else None,
                "next_id": ids[pos + 1] if 0 <= pos < len(ids) - 1 else None,
                "position": pos + 1 if pos >= 0 else None,
                "total": len(ids),
            },
        }

    def stats(self) -> dict[str, Any]:
        statuses: Counter[str] = Counter()
        sizes: Counter[int] = Counter()
        for entity in self.entities:
            statuses[entity.get("status", "suggested")] += 1
            sizes[len(entity.get("members") or [])] += 1
        return {
            "entity_count": len(self.entities),
            "multi_member_count": sum(
                1 for e in self.entities if len(e.get("members") or []) > 1
            ),
            "by_status": dict(statuses),
            "size_histogram": [
                {"size": size, "count": sizes[size]} for size in sorted(sizes)
            ],
        }
