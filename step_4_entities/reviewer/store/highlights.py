"""Which entities a claim mentions, for the highlighted claim texts of the entity detail."""

from __future__ import annotations

from typing import Any

from step_4_entities.mentions import name_words
from step_4_entities.reviewer.store.claims import ClaimLookup
from step_4_entities.reviewer.store.helpers import merge_highlights


class ClaimAnnotator:
    """Alias index of the current entities plus the claim lookup: built after every load or
    save, so names, colours and links always follow the entities as they are now."""

    def __init__(self, entities: list[dict[str, Any]], claims: ClaimLookup) -> None:
        self._claims = claims
        self.entity_of_name: dict[str, str] = {}
        self.colour: dict[str, int] = {}
        self.canonical: dict[str, str] = {}
        self.holders: dict[str, list[str]] = {}
        for index, entity in enumerate(entities):
            entity_id = entity["entity_id"]
            self.colour[entity_id] = index
            self.canonical[entity_id] = entity.get("canonical_name") or ""
            for member in entity.get("members") or []:
                self.holders.setdefault(member["name"], []).append(entity_id)
            for alias in [*(entity.get("aliases") or []), entity.get("canonical_name")]:
                if alias:
                    self.entity_of_name.setdefault(alias, entity_id)
        # Longest names first: a claim highlights the longest alias of each other entity.
        self._by_first_word: dict[str, list[str]] = {}
        for name in sorted(self.entity_of_name, key=len, reverse=True):
            words = name_words(name)
            if words:
                self._by_first_word.setdefault(words[0], []).append(name)

    def ref(self, name: str) -> dict[str, Any]:
        """A link to the entity holding ``name`` (``entity_id`` None when nobody does)."""

        entity_id = self.entity_of_name.get(name)
        return {
            "name": name,
            "entity_id": entity_id,
            "canonical_name": self.canonical.get(entity_id, name) if entity_id else name,
            "color_index": self.colour.get(entity_id) if entity_id else None,
        }

    def other_entities(
        self, claim_id: str, entity_id: str, own_names: set[str]
    ) -> list[dict[str, Any]]:
        """Other entities whose names the claim's text mentions, with the spans."""

        found: dict[str, dict[str, Any]] = {}
        for core in self._claims.cores(claim_id):
            for name in self._by_first_word.get(core, ()):
                other = self.entity_of_name[name]
                if name in own_names or other == entity_id:
                    continue
                if other in found and len(found[other]["name"]) >= len(name):
                    continue
                spans = self._claims.spans(claim_id, name)
                if spans:
                    found[other] = {**self.ref(name), "spans": spans}
        return sorted(found.values(), key=lambda o: o["spans"][0][0])

    def tagged_only(
        self, claim: dict[str, Any], entity_id: str, own_names: set[str], mentioned: set[str]
    ) -> list[dict[str, Any]]:
        """Entity strings the claim is tagged with but whose name its text does not contain."""

        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for name in claim.get("entities") or []:
            if not isinstance(name, str) or not name.strip() or name in own_names:
                continue
            if self._claims.spans(claim["claim_id"], name):
                continue
            ref = self.ref(name)
            if ref["entity_id"] in (entity_id, *mentioned):
                continue
            key = ref["entity_id"] or name
            if key not in seen:
                seen.add(key)
                out.append({**ref, "tagged_only": True})
        return out

    def annotate(
        self, claim_id: str, member_name: str, entity_id: str, own_names: set[str]
    ) -> dict[str, Any] | None:
        """The claim as the detail shows it under one member (None for an unknown claim)."""

        claim = self._claims.claim(claim_id)
        if claim is None:
            return None
        others = self.other_entities(claim_id, entity_id, own_names)
        segments = [
            {"start": start, "end": end, "kind": "self"}
            for start, end in self._claims.spans(claim_id, member_name)
        ]
        for other in others:
            fields = {k: other[k] for k in ("entity_id", "name", "canonical_name", "color_index")}
            segments.extend(
                {"start": start, "end": end, "kind": "other", **fields}
                for start, end in other["spans"]
            )
        return {
            "claim_id": claim_id,
            "claim_text": claim.get("claim_text", ""),
            "stance": claim.get("stance"),
            "thread_id": claim.get("thread_id"),
            "tagged": member_name in (claim.get("entities") or []),
            "highlights": merge_highlights(segments),
            "other_entities": others,
            "related_entities": self.tagged_only(
                claim, entity_id, own_names, {o["entity_id"] for o in others}
            ),
        }
