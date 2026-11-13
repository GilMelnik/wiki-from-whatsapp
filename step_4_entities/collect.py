"""Which claims each entity string stands for, with the contacts seen in them."""

from __future__ import annotations

import json
from collections.abc import Collection, Iterable
from pathlib import Path
from typing import Any

from step_4_entities.mentions import MentionIndex, Word
from step_4_entities.records import member_record
from utils.paths import ORIGINAL_CLAIMS_PATH, resolve_claims_path


def load_claims_for_entities(
    claims_path: Path | str | None = None,
    original_path: Path = ORIGINAL_CLAIMS_PATH,
) -> tuple[list[dict[str, Any]], Path, dict[str, dict[str, Any]] | None]:
    """Load claims for entity collection, with the original contact trail when edited.

    ``claims_path`` defaults to the reviewed claims when they exist; ``original_path`` is the
    pipeline-original claims file whose redaction trail backs up the contacts.
    """

    resolved = Path(claims_path) if claims_path is not None else resolve_claims_path()
    with resolved.open(encoding="utf-8") as f:
        claims = json.load(f)["claims"]
    original_by_id: dict[str, dict[str, Any]] | None = None
    if resolved.resolve() != original_path.resolve() and original_path.is_file():
        with original_path.open(encoding="utf-8") as f:
            original_by_id = {
                c["claim_id"]: c for c in json.load(f).get("claims") or []
            }
    return claims, resolved, original_by_id


def tagged_names(claim: dict[str, Any]) -> list[str]:
    """The non-empty entity strings step 3 attached to a claim, in order, deduplicated."""

    names = [n for n in claim.get("entities") or [] if isinstance(n, str) and n.strip()]
    return list(dict.fromkeys(names))


class ClaimCorpus:
    """Claims, their morphology and contact trail: the claims a name stands for.

    A name stands for every claim that lists it in ``entities`` plus every claim whose text
    mentions it as a whole word (``MentionIndex``).
    """

    def __init__(
        self,
        claims: Iterable[dict[str, Any]],
        analysis: dict[str, list[Word]],
        original_by_id: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self.claims_by_id = {c["claim_id"]: c for c in claims if c.get("claim_id")}
        self.original_by_id = original_by_id or {}
        self._mentions = MentionIndex(analysis)
        self._tagged: dict[str, set[str]] = {}
        for claim_id, claim in self.claims_by_id.items():
            for name in tagged_names(claim):
                self._tagged.setdefault(name, set()).add(claim_id)

    def tagged_claims(self, name: str) -> set[str]:
        """Only the claims whose ``entities`` list this name (no text mentions)."""

        return set(self._tagged.get(name, set()))

    def claims_for(self, name: str, within: Collection[str] | None = None) -> set[str]:
        ids = self._tagged.get(name, set()) | self._mentions.claims_mentioning(name)
        return ids if within is None else ids & set(within)

    def member(
        self,
        name: str,
        claim_ids: Iterable[str],
        *,
        excluded_claim_ids: Collection[str] = (),
    ) -> dict[str, Any]:
        return member_record(
            name,
            claim_ids,
            self.claims_by_id,
            self.original_by_id,
            excluded_claim_ids=excluded_claim_ids,
        )

