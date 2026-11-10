"""Synthetic step-5 inputs: claims, entities, audit and a deterministic fake embedder.

A claim's vector is a unit vector at ``angle`` radians inside plane ``plane`` of a 16-d space,
so two claims in one plane have cosine ``cos(angle difference)`` and claims in different
planes have cosine 0. ``angle_for(cos)`` gives the angle difference for a wanted cosine.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from step_4_entities.records import claim_entity_index
from utils.json_io import write_json_file
from utils.paths import Artifact

DIM = 16
_DIGITS_AS_LETTERS = str.maketrans("0123456789", "abcdefghij")


def angle_for(cosine: float) -> float:
    return math.acos(cosine)


@dataclass
class Corpus:
    """Builds the files one step-5 run reads, under ``root``."""

    root: Path
    claims: list[dict[str, Any]] = field(default_factory=list)
    vectors: dict[str, np.ndarray] = field(default_factory=dict)
    audit: dict[str, dict[str, list[dict[str, str]]]] = field(default_factory=dict)
    entity_of: dict[str, list[str]] = field(default_factory=dict)
    must_links: list[dict[str, Any]] = field(default_factory=list)
    legacy: dict[str, list[str]] = field(default_factory=dict)

    def add(
        self,
        claim_id: str,
        *,
        angle: float = 0.0,
        plane: int = 0,
        stance: str = "factual",
        entities: Sequence[str] = (),
        date: str = "2024-01-01",
        text: str | None = None,
        supporters: Sequence[tuple[str, str]] = (),
        opposers: Sequence[tuple[str, str]] = (),
    ) -> None:
        """One claim; ``supporters`` / ``opposers`` are ``(person, date)`` audit events."""

        # Digits are spelled as letters: quoted numbers would trigger the number rule.
        text = text or f"claim {claim_id.translate(_DIGITS_AS_LETTERS)}"
        self.claims.append(
            {
                "claim_id": claim_id,
                "thread_id": f"t-{claim_id}",
                "claim_text": text,
                "entities": list(entities),
                "stance": stance,
                "first_date": date,
                "last_date": date,
                "supporter_count": len({p for p, _ in supporters}),
                "opposer_count": len({p for p, _ in opposers}),
            }
        )
        vector = np.zeros(DIM, dtype=np.float32)
        vector[2 * plane], vector[2 * plane + 1] = math.cos(angle), math.sin(angle)
        self.vectors[text] = vector
        self.audit[claim_id] = {
            side: [{"person": p, "date": d, "via": "message", "message_id": "m"} for p, d in events]
            for side, events in (("supporters", supporters), ("opposers", opposers))
        }
        for entity in entities:
            self.entity_of.setdefault(entity, []).append(claim_id)

    def claim(self, claim_id: str) -> dict[str, Any]:
        return next(c for c in self.claims if c["claim_id"] == claim_id)

    def embed(self, texts: Sequence[str], kind: str) -> np.ndarray:
        return np.stack([self.vectors[t] for t in texts])

    @property
    def artifact(self) -> Artifact:
        return Artifact(
            self.root / "claims_aggregated.json", self.root / "claims_aggregated_edited.json"
        )

    def paths(self) -> dict[str, Path]:
        """Write every input file; returns the ``run`` keyword arguments."""

        claims_path = self.root / "claims.json"
        metadata = {"legacy_claim_ids": self.legacy} if self.legacy else {}
        write_json_file({"claims": self.claims, "metadata": metadata}, claims_path)
        entities = [
            {
                "entity_id": f"e{k:04d}",
                "canonical_name": name,
                "members": [{"name": name, "claim_ids": ids}],
            }
            for k, (name, ids) in enumerate(sorted(self.entity_of.items()))
        ]
        entities_path = self.root / "entities.json"
        write_json_file(
            {"entities": entities, "claim_entities": claim_entity_index(entities)}, entities_path
        )
        audit_path = self.root / "audit.json"
        write_json_file(
            {"audit": [{"claim_id": cid, **rec} for cid, rec in self.audit.items()]}, audit_path
        )
        must_links_path = self.root / "claim_aggregations.json"
        write_json_file({"aggregations": self.must_links}, must_links_path)
        return {
            "claims_path": claims_path,
            "entities_path": entities_path,
            "audit_path": audit_path,
            "must_links_path": must_links_path,
            "artifact": self.artifact,
        }
