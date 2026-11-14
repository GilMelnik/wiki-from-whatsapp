"""Which canonical entities each claim mentions (``claim_entities`` of ``entities.json``).

Step 4 stores the index with its entities; step 5 builds it again from the effective entities
file (the reviewer's edits included) to know the entities of each claim group.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Iterable, Mapping
from typing import Any

MemberClaims = Callable[[dict[str, Any]], Iterable[str]]


def explicit_member_claims(member: Mapping[str, Any]) -> list[str]:
    """A member's active claims: its ``claim_ids`` minus its excluded ones."""

    excluded = set(member.get("excluded_claim_ids") or [])
    return [c for c in member.get("claim_ids") or [] if c not in excluded]


def claim_entity_index(
    entities: Iterable[Mapping[str, Any]],
    *,
    deleted_claims: Collection[str] = (),
    member_claims: MemberClaims = explicit_member_claims,
) -> dict[str, list[str]]:
    """``claim_id -> [entity_id]``: the canonical entities each claim mentions (step 5)."""

    deleted = set(deleted_claims)
    index: dict[str, list[str]] = {}
    for entity in entities:
        entity_id = entity["entity_id"]
        for member in entity.get("members") or []:
            for claim_id in member_claims(member):
                if claim_id in deleted:
                    continue
                ids = index.setdefault(claim_id, [])
                if entity_id not in ids:
                    ids.append(entity_id)
    return {claim_id: sorted(ids) for claim_id, ids in sorted(index.items())}
