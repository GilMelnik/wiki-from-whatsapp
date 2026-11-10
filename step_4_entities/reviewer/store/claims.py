"""Claim attribution and highlighting for the entity store."""

from __future__ import annotations

from typing import Any

from step_4_entities.collect import _claim_contacts
from step_4_entities.mentions import Word, analyze_claims, find_mentions
from step_4_entities.reviewer.store.helpers import _merge_highlights


class ClaimsMixin:
    """Maps members to claims and builds mention highlights.

    Relies on ``EntityStore`` attributes: ``_analysis``, ``_analyzer``,
    ``_claims_by_id``, ``_original_by_id``, ``_claim_ids_by_name``,
    ``_deleted_claims``, ``_alias_to_entity``, ``_entity_color_index``
    and the ``entities`` property.
    """

    # --- member helpers -------------------------------------------------

    def _words_for(self, claim: dict[str, Any]) -> list[Word]:
        """Analyzed words for a claim, analyzing on demand for cache misses."""

        claim_id = claim.get("claim_id")
        if claim_id and claim_id in self._analysis:
            return self._analysis[claim_id]
        words = analyze_claims([claim], self._analyzer).get(claim_id, [])
        if claim_id:
            self._analysis[claim_id] = words
        return words

    def _mention_spans(self, claim: dict[str, Any], name: str) -> list[list[int]]:
        return find_mentions(self._words_for(claim), name)

    def _claim_mentions(self, claim: dict[str, Any], name: str) -> bool:
        return bool(self._mention_spans(claim, name))

    def _raw_claim_ids_for_member(self, member: dict[str, Any]) -> set[str]:
        if member.get("claim_ids"):
            return set(member["claim_ids"])
        name = member["name"]
        ids: set[str] = set(self._claim_ids_by_name.get(name, []))
        for claim_id, claim in self._claims_by_id.items():
            if self._claim_mentions(claim, name):
                ids.add(claim_id)
        return ids

    def _claim_ids_for_member(self, member: dict[str, Any]) -> list[str]:
        excluded = set(member.get("excluded_claim_ids") or [])
        return sorted(
            self._raw_claim_ids_for_member(member) - excluded - self._deleted_claims
        )

    def _member_from_claims(
        self,
        name: str,
        claim_ids: list[str],
        *,
        excluded_claim_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        emails: set[str] = set()
        phones: set[str] = set()
        websites: set[str] = set()
        topics: set[str] = set()
        for claim_id in claim_ids:
            claim = self._claims_by_id.get(claim_id, {})
            original = self._original_by_id.get(claim_id)
            topics.update(claim.get("topic_tags") or [])
            claim_emails, claim_phones, claim_sites = _claim_contacts(claim, original)
            emails.update(claim_emails)
            phones.update(claim_phones)
            websites.update(claim_sites)
        return {
            "name": name,
            "claim_ids": list(claim_ids),
            "count": len(claim_ids),
            "sample_claim_ids": list(claim_ids[:12]),
            "topics": sorted(topics),
            "contacts": {
                "email": sorted(emails),
                "phone": sorted(phones),
                "website": sorted(websites),
            },
            "excluded_claim_ids": list(excluded_claim_ids or []),
        }

    def _other_entities_in_claim(
        self,
        claim: dict[str, Any],
        *,
        current_entity_id: str,
        current_names: set[str],
    ) -> list[dict[str, Any]]:
        found: dict[str, dict[str, Any]] = {}
        names = sorted(self._alias_to_entity.keys(), key=len, reverse=True)
        for name in names:
            if name in current_names:
                continue
            entity_id = self._alias_to_entity[name]
            if entity_id == current_entity_id:
                continue
            spans = self._mention_spans(claim, name)
            if not spans:
                continue
            existing = found.get(entity_id)
            if existing is None or len(name) > len(existing["name"]):
                entity = next(
                    e for e in self.entities if e["entity_id"] == entity_id
                )
                found[entity_id] = {
                    "entity_id": entity_id,
                    "name": name,
                    "canonical_name": entity.get("canonical_name") or name,
                    "color_index": self._entity_color_index.get(entity_id, 0),
                    "spans": spans,
                }
        return list(found.values())

    def _related_entities_in_claim(
        self,
        claim: dict[str, Any],
        *,
        current_entity_id: str,
        current_names: set[str],
        mentioned_entity_ids: set[str],
    ) -> list[dict[str, Any]]:
        """Entity tags on the claim whose name does not appear in ``claim_text``."""

        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for name in claim.get("entities") or []:
            if not isinstance(name, str) or not name.strip():
                continue
            if name in current_names:
                continue
            if self._claim_mentions(claim, name):
                continue
            entity_id = self._alias_to_entity.get(name)
            if entity_id == current_entity_id:
                continue
            if entity_id and entity_id in mentioned_entity_ids:
                continue
            key = entity_id or name
            if key in seen:
                continue
            seen.add(key)
            entry: dict[str, Any] = {"name": name, "tagged_only": True}
            if entity_id:
                entity = next(
                    e for e in self.entities if e["entity_id"] == entity_id
                )
                entry["entity_id"] = entity_id
                entry["canonical_name"] = entity.get("canonical_name") or name
                entry["color_index"] = self._entity_color_index.get(entity_id, 0)
            else:
                entry["entity_id"] = None
                entry["canonical_name"] = name
                entry["color_index"] = None
            out.append(entry)
        return out

    def _claim_highlights(
        self,
        claim: dict[str, Any],
        member_name: str,
        *,
        current_entity_id: str,
        current_names: set[str],
    ) -> list[dict[str, Any]]:
        segments: list[dict[str, Any]] = []
        for start, end in self._mention_spans(claim, member_name):
            segments.append({"start": start, "end": end, "kind": "self"})
        for other in self._other_entities_in_claim(
            claim,
            current_entity_id=current_entity_id,
            current_names=current_names,
        ):
            for start, end in other["spans"]:
                segments.append(
                    {
                        "start": start,
                        "end": end,
                        "kind": "other",
                        "entity_id": other["entity_id"],
                        "name": other["name"],
                        "canonical_name": other["canonical_name"],
                        "color_index": other["color_index"],
                    }
                )
        return _merge_highlights(segments)
