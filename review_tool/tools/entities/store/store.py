"""Load, edit and persist step 4's entities for the owner's review.

The store is built from disk in one go (a ``StoreHandle`` factory) and reads the edited
entities when they exist, else the original; the first change creates the edited copy. It
writes ONLY ``entities_edited.json`` and the manual claim aggregations file, and every save
keeps what step 5 reads consistent: ``claim_entities`` (claim -> canonical entity ids), the id
high-water mark ``metadata.last_entity_number``, ``deleted_claims`` and ``deleted_names``.
New ids come from ``records.allocate_entity_id``; entities are refreshed with
``records.refresh_entity``, which keeps the manual flags (``canonical_manual``,
``contacts_manual``, ``contacts_rejected``).

Members whose ``claim_ids`` is ``null`` (old files: "every claim naming it") get their claims
listed on load, so every edit works on explicit claim lists.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from review_tool.tools.entities.store.aggregations import EDITOR, ClaimAggregations
from review_tool.tools.entities.store.claims import ClaimLookup
from review_tool.tools.entities.store.context import ContextMixin
from review_tool.tools.entities.store.edits import EditsMixin
from review_tool.tools.entities.store.helpers import EntityNotFound
from review_tool.tools.entities.store.highlights import ClaimAnnotator
from review_tool.tools.entities.store.members import MembersMixin
from review_tool.tools.entities.store.views import ViewsMixin
from step_4_entities.records import (
    claim_entity_index,
    entity_counts,
    highest_entity_number,
)
from utils.json_io import write_json_file
from utils.timestamps import local_now


class EntityStore(ViewsMixin, ContextMixin, EditsMixin, MembersMixin):
    def __init__(
        self,
        entities_original: Path,
        entities_edited: Path,
        claims_original: Path,
        claims_edited: Path,
        aggregations_path: Path,
        analysis_path: Path,
    ) -> None:
        self.edited_path = entities_edited
        self.source = entities_edited if entities_edited.is_file() else entities_original
        if not self.source.is_file():
            raise FileNotFoundError(f"{entities_original} does not exist: run step 4 first")
        self.payload: dict[str, Any] = json.loads(self.source.read_text(encoding="utf-8"))
        self.payload.setdefault("entities", [])
        claims_path = claims_edited if claims_edited.is_file() else claims_original
        self.claims = ClaimLookup(claims_path, claims_original, analysis_path)
        self.aggregations = ClaimAggregations(aggregations_path)
        self.deleted_claims: set[str] = set(self.payload.get("deleted_claims") or [])
        self.deleted_names: set[str] = set(self.payload.get("deleted_names") or [])
        for entity in self.entities:
            for member in entity.get("members") or []:
                if member.get("claim_ids") is None:
                    ids = self.claims.claims_for(member["name"]) - self.deleted_claims
                    member["claim_ids"] = sorted(ids)
        self._annotator: ClaimAnnotator | None = None

    @property
    def entities(self) -> list[dict[str, Any]]:
        return self.payload["entities"]

    @property
    def annotator(self) -> ClaimAnnotator:
        if self._annotator is None:
            self._annotator = ClaimAnnotator(self.entities, self.claims)
        return self._annotator

    def find(self, entity_id: str) -> dict[str, Any]:
        for entity in self.entities:
            if entity["entity_id"] == entity_id:
                return entity
        raise EntityNotFound(f"Entity {entity_id} does not exist (it may have been merged or deleted)")

    def member_of(self, entity: dict[str, Any], name: str) -> dict[str, Any]:
        for member in entity.get("members") or []:
            if member["name"] == name:
                return member
        raise EntityNotFound(f"The name “{name}” no longer belongs to entity {entity['entity_id']}")

    def text_of(self, claim_id: str) -> str:
        return (self.claims.claim(claim_id) or {}).get("claim_text", "")

    def claim_usable(self, claim_id: str) -> bool:
        return self.claims.claim(claim_id) is not None and claim_id not in self.deleted_claims

    def save(self) -> None:
        """Write the edited entities with everything step 5 reads brought up to date."""

        payload = self.payload
        meta = payload.setdefault("metadata", {})
        meta["last_entity_number"] = highest_entity_number(payload)
        payload["claim_entities"] = claim_entity_index(
            self.entities, deleted_claims=self.deleted_claims
        )
        for key, values in (
            ("deleted_claims", self.deleted_claims),
            ("deleted_names", self.deleted_names),
        ):
            if values:
                payload[key] = sorted(values)
            else:
                payload.pop(key, None)
        meta.update(
            entity_counts(payload),
            edited_by=EDITOR,
            edited_at=local_now(),
        )
        write_json_file(payload, self.edited_path)
        self.source = self.edited_path
        self._annotator = None
