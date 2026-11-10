"""Which claims each entity string stands for, with the contacts seen in them."""

from __future__ import annotations

import json
from collections.abc import Collection, Iterable
from pathlib import Path
from typing import Any

from step_4_entities.mentions import (
    Analyzer,
    MentionIndex,
    SimpleAnalyzer,
    Word,
    analyze_claims,
)
from step_4_entities.records import member_record
from utils.paths import ORIGINAL_CLAIMS_PATH, resolve_claims_path


def load_claims_for_entities(
    claims_path: Path | str | None = None,
) -> tuple[list[dict[str, Any]], Path, dict[str, dict[str, Any]] | None]:
    """Load claims for entity collection, with original contact trail when edited."""

    resolved = Path(claims_path) if claims_path is not None else resolve_claims_path()
    with resolved.open(encoding="utf-8") as f:
        claims = json.load(f)["claims"]
    original_by_id: dict[str, dict[str, Any]] | None = None
    if (
        resolved.resolve() != ORIGINAL_CLAIMS_PATH.resolve()
        and ORIGINAL_CLAIMS_PATH.is_file()
    ):
        with ORIGINAL_CLAIMS_PATH.open(encoding="utf-8") as f:
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

    def names(self) -> list[str]:
        """Every tagged name, most-tagged first (ties by name, for a stable order)."""

        return sorted(self._tagged, key=lambda n: (-len(self._tagged[n]), n))

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


def collect_entities(
    claims: list[dict[str, Any]],
    *,
    original_by_id: dict[str, dict[str, Any]] | None = None,
    analysis: dict[str, list[Word]] | None = None,
    analyzer: Analyzer | None = None,
) -> list[dict[str, Any]]:
    """One member record per distinct entity string, most claims first.

    ``analysis`` is a precomputed ``{claim_id: words}`` morphology map; when absent it is
    built with ``analyzer`` (default: the model-free ``SimpleAnalyzer``).
    """

    if analysis is None:
        analysis = analyze_claims(claims, analyzer or SimpleAnalyzer())
    corpus = ClaimCorpus(claims, analysis, original_by_id)
    members = [corpus.member(name, corpus.claims_for(name)) for name in corpus.names()]
    members.sort(key=lambda m: m["count"], reverse=True)
    return members
