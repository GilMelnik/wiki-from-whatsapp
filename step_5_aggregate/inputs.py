"""What step 5 reads: the reviewed claims (C3), their canonical entities (step 4), the private
audit of who backed or disputed each claim, and the manual must-link groupings.

Claims the entity reviewer deleted (``deleted_claims`` on the entities payload) are left out,
as if step 3 had never produced them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from step_4_entities.records import claim_entity_index
from utils.paths import (
    AUDIT_PATH,
    MANUAL_AGGREGATIONS_PATH,
    resolve_claims_path,
    resolve_entities_path,
)
from utils.reconcile import fingerprint


def _read(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


@dataclass(frozen=True)
class MustLink:
    """Claims a reviewer said belong together (entity reviewer, ``claim_aggregations.json``)."""

    link_id: str
    claim_ids: tuple[str, ...]
    representative: str

    @property
    def fingerprint(self) -> str:
        return fingerprint({"claim_ids": sorted(self.claim_ids), "rep": self.representative})


@dataclass(frozen=True)
class StepInputs:
    """Everything one run works from. ``claims`` keeps the claims file's order."""

    claims: dict[str, dict[str, Any]]
    claim_entities: dict[str, list[str]]
    entity_names: dict[str, str]
    audit: dict[str, dict[str, Any]]
    legacy_ids: dict[str, str]
    source: Path

    def fingerprint(self, claim_id: str) -> str:
        """What grouping reads from a claim: its text, stance and canonical entity ids."""

        claim = self.claims[claim_id]
        return fingerprint(
            {
                "claim_text": claim.get("claim_text") or "",
                "stance": claim.get("stance") or "",
                "entities": self.claim_entities.get(claim_id, []),
            }
        )


def load_inputs(
    claims_path: Path | None = None,
    entities_path: Path | None = None,
    audit_path: Path = AUDIT_PATH,
) -> StepInputs:
    """Load claims (edited when present), entities (edited when present) and the audit.

    The claim -> entity map is recomputed from the entity members, so a hand-edited entities
    file is read correctly. A missing entities file means no claim has an entity; a missing
    audit is an error (step 5 counts people from it).
    """

    source = claims_path or resolve_claims_path()
    payload = _read(source)
    entities_file = entities_path or resolve_entities_path()
    entities = _read(entities_file) if entities_file.is_file() else {}
    deleted = set(entities.get("deleted_claims") or [])
    claims = {c["claim_id"]: c for c in payload.get("claims") or [] if c["claim_id"] not in deleted}
    if not audit_path.is_file():
        raise FileNotFoundError(
            f"step 5 needs the private audit {audit_path} (who backed / disputed each claim); "
            f"run step 3 first"
        )
    audit = {r["claim_id"]: r for r in _read(audit_path).get("audit") or []}
    index = claim_entity_index(entities.get("entities") or [], deleted_claims=deleted)
    legacy = {
        old: new
        for new, olds in (payload.get("metadata", {}).get("legacy_claim_ids") or {}).items()
        for old in olds
    }
    return StepInputs(
        claims=claims,
        claim_entities={cid: ids for cid, ids in index.items() if cid in claims},
        entity_names={e["entity_id"]: e["canonical_name"] for e in entities.get("entities") or []},
        audit=audit,
        legacy_ids=legacy,
        source=source,
    )


def load_must_links(inputs: StepInputs, path: Path = MANUAL_AGGREGATIONS_PATH) -> list[MustLink]:
    """Manual groupings whose claims still exist (at least two), ids mapped to current claims.

    Ids unknown to the claims file are looked up in ``metadata.legacy_claim_ids`` (the dev
    corpus migrated from v1); a representative that is gone falls back to the first member.
    """

    if not path.is_file():
        return []
    links: list[MustLink] = []
    for raw in _read(path).get("aggregations") or []:
        mapped = (c if c in inputs.claims else inputs.legacy_ids.get(c, "") for c in raw["claim_ids"])
        ids = list(dict.fromkeys(cid for cid in mapped if cid in inputs.claims))
        if len(ids) < 2:
            continue
        rep = raw.get("representative")
        rep = rep if rep in inputs.claims else inputs.legacy_ids.get(rep or "", "")
        links.append(MustLink(raw["id"], tuple(ids), rep if rep in ids else ids[0]))
    return links
