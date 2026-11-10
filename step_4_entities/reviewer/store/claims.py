"""The claims behind the entities: texts, morphology and the claims a name stands for.

The morphology comes from the per-claim analysis cache step 4 writes (dictabert), so mention
matching and highlighting are exactly the pipeline's. The review server never loads a model:
a claim the cache does not cover (new or edited text since the last step-4 run) is analyzed
with the model-free ``SimpleAnalyzer`` (whole words, no prefix stripping) until step 4 runs.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable
from pathlib import Path
from typing import Any

from step_4_entities.collect import ClaimCorpus, load_claims_for_entities
from step_4_entities.mentions import (
    SimpleAnalyzer,
    analyze_claims,
    find_mentions,
    read_analysis_cache,
    split_cached,
)
from step_4_entities.normalize import normalize_token


class ClaimLookup:
    """Read-only claims (reviewed copy when it exists) with their mention analysis."""

    def __init__(self, claims_path: Path, original_path: Path, analysis_path: Path) -> None:
        if not claims_path.is_file():
            raise FileNotFoundError(f"{claims_path} does not exist: run step 3 first")
        claims, _, original_by_id = load_claims_for_entities(claims_path, original_path)
        cached, fingerprints = read_analysis_cache(analysis_path)
        analysis, missing = split_cached(claims, cached, fingerprints)
        analysis.update(analyze_claims(missing, SimpleAnalyzer()))
        self.analysis_counts = {"cached": len(analysis) - len(missing), "fallback": len(missing)}
        self._analysis = analysis
        self._cores = {
            claim_id: {normalize_token(word.get("core") or "") for word in words}
            for claim_id, words in analysis.items()
        }
        self.corpus = ClaimCorpus(claims, analysis, original_by_id)
        self.claims_by_id = self.corpus.claims_by_id

    def claim(self, claim_id: str) -> dict[str, Any] | None:
        return self.claims_by_id.get(claim_id)

    def spans(self, claim_id: str, name: str) -> list[list[int]]:
        """Character spans where ``name`` is mentioned in the claim's text."""

        return find_mentions(self._analysis.get(claim_id) or [], name)

    def cores(self, claim_id: str) -> set[str]:
        """The normalized word cores of a claim (to pre-select names that may occur in it)."""

        return self._cores.get(claim_id, set())

    def claims_for(self, name: str) -> set[str]:
        """Claims that tag the name or mention it in their text."""

        return self.corpus.claims_for(name)

    def tagged_claims(self, name: str) -> set[str]:
        return self.corpus.tagged_claims(name)

    def member(
        self, name: str, claim_ids: Iterable[str], excluded: Collection[str] = ()
    ) -> dict[str, Any]:
        """A fresh member record (claims, count, sample, contacts) for these claims."""

        return self.corpus.member(name, claim_ids, excluded_claim_ids=excluded)
