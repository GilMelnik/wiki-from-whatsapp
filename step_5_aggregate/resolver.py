"""Map raw entity strings to canonical names by the step-4 entity members.

Step 5 itself works from ``claim_entities`` (canonical ids); this name-level resolver is kept
for the old step 7 and the old step-5 reviewer until their rewrites.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from utils.paths import resolve_entities_path


class EntityResolver:
    """Map a claim's raw entity strings to canonical entity names.

    Default mapping is by name. A member with an explicit ``claim_ids`` list
    creates per-claim overrides (used when a single string, e.g. a first name,
    was split across two real people in the review tool).
    """

    def __init__(self, entities: list[dict[str, Any]]) -> None:
        self._by_name: dict[str, str] = {}
        self._by_name_claim: dict[tuple[str, str], str] = {}
        self._registry: dict[str, dict[str, Any]] = {}
        for entity in entities:
            canonical = entity["canonical_name"]
            self._registry[canonical] = {
                "entity_id": entity.get("entity_id"),
                "aliases": entity.get("aliases") or [],
                "contacts": entity.get("contacts") or {},
            }
            for member in entity.get("members") or []:
                name = member["name"]
                claim_ids = member.get("claim_ids")
                if claim_ids:
                    for claim_id in claim_ids:
                        self._by_name_claim[(name, claim_id)] = canonical
                else:
                    self._by_name[name] = canonical

    def canonical(self, name: str, claim_id: str | None = None) -> str:
        if claim_id is not None:
            override = self._by_name_claim.get((name, claim_id))
            if override is not None:
                return override
        return self._by_name.get(name, name)

    def resolve_claim(self, claim: dict[str, Any]) -> list[str]:
        claim_id = claim.get("claim_id")
        out: list[str] = []
        for name in claim.get("entities") or []:
            canonical = self.canonical(name, claim_id)
            if canonical not in out:
                out.append(canonical)
        return out

    def registry(self) -> dict[str, dict[str, Any]]:
        return self._registry


def load_entity_resolver(path: Path | str | None = None) -> EntityResolver | None:
    """Load the (edited) entity registry, or ``None`` when no file exists."""

    resolved = Path(path) if path is not None else resolve_entities_path()
    if not resolved.is_file():
        return None
    with resolved.open(encoding="utf-8") as f:
        return EntityResolver(json.load(f).get("entities") or [])


def load_deleted_claims(path: Path | str | None = None) -> set[str]:
    """Claim ids the reviewer hard-deleted (stored on the entities payload)."""

    resolved = Path(path) if path is not None else resolve_entities_path()
    if not resolved.is_file():
        return set()
    with resolved.open(encoding="utf-8") as f:
        return set(json.load(f).get("deleted_claims") or [])


def apply_entity_resolution(
    claims: list[dict[str, Any]], resolver: EntityResolver | None
) -> None:
    """Rewrite each claim's ``entities`` to canonical names, in place."""

    if resolver is None:
        return
    for claim in claims:
        claim["entities"] = resolver.resolve_claim(claim)
