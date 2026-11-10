"""When two claims say the same thing: the pair test that grouping and contradictions share.

Each claim text is embedded once with the hosted embedder (``utils.embedder``, cached on disk,
kind ``aggregate.embedding_kind``). Two claims *match* when

1. their stances are not opposite (``aggregate.opposing_stances``: a recommendation and a
   warning never count as one statement),
2. they do not quote different numbers (both texts contain numbers and share none: "form 300"
   vs "form 302", "until 2024" vs "until 2025" read alike to an embedder but are different
   facts), and
3. their cosine similarity reaches ``aggregate.merge_threshold.shared_entity`` when they mention
   at least one common canonical entity from step 4, otherwise the higher
   ``aggregate.merge_threshold.no_shared_entity``.

The *margin* of a pair is its cosine minus the bar that applies (``-inf`` when rule 1 or 2
fails), so "match" means margin >= 0 and a larger margin means a closer match.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np

from utils.config import section
from utils.embedder import embed_texts

AGGREGATE_CONFIG = section("aggregate")
_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)*")


def numbers_in(text: str) -> frozenset[str]:
    """The numbers quoted in ``text`` (thousands separators dropped: ``10,000`` == ``10000``)."""

    return frozenset(match.replace(",", "") for match in _NUMBER_RE.findall(text))


class ClaimScorer:
    """Pair margins between claims, from their vectors, stances, entities and numbers."""

    def __init__(
        self,
        claims: Mapping[str, Mapping[str, Any]],
        claim_entities: Mapping[str, Sequence[str]],
        vectors: np.ndarray,
        *,
        merge_threshold: Mapping[str, float] | None = None,
    ) -> None:
        self.ids = list(claims)
        self.row = {claim_id: i for i, claim_id in enumerate(self.ids)}
        self.vectors = vectors
        self.stances = [str(claims[c].get("stance") or "") for c in self.ids]
        self.entities = [frozenset(claim_entities.get(c, ())) for c in self.ids]
        self.numbers = [numbers_in(str(claims[c].get("claim_text") or "")) for c in self.ids]
        thresholds = merge_threshold or AGGREGATE_CONFIG["merge_threshold"]
        self.shared_bar = float(thresholds["shared_entity"])
        self.other_bar = float(thresholds["no_shared_entity"])
        self.opposing = frozenset(AGGREGATE_CONFIG["opposing_stances"])

    @classmethod
    def embed(
        cls, claims: Mapping[str, Mapping[str, Any]], claim_entities: Mapping[str, Sequence[str]]
    ) -> ClaimScorer:
        """Embed every claim text (only texts missing from the cache cost an API call)."""

        texts = [str(c.get("claim_text") or "") for c in claims.values()]
        vectors = embed_texts(texts, AGGREGATE_CONFIG["embedding_kind"])
        return cls(claims, claim_entities, vectors)

    def opposite(self, a: int, b: int) -> bool:
        return {self.stances[a], self.stances[b]} == self.opposing

    def number_clash(self, a: int, b: int) -> bool:
        x, y = self.numbers[a], self.numbers[b]
        return bool(x and y and not x & y)

    def shares_entity(self, a: int, b: int) -> bool:
        return bool(self.entities[a] & self.entities[b])

    def cosines(self, claim_id: str, others: Sequence[str]) -> np.ndarray:
        rows = [self.row[o] for o in others]
        return self.vectors[rows] @ self.vectors[self.row[claim_id]]

    def margins(self, claim_id: str, others: Sequence[str]) -> np.ndarray:
        """Margin of ``claim_id`` against each of ``others`` (see the module docstring)."""

        cos = self.cosines(claim_id, others)
        out = np.full(len(others), -np.inf, dtype=np.float64)
        a = self.row[claim_id]
        for k in np.flatnonzero(cos >= min(self.shared_bar, self.other_bar)):
            b = self.row[others[k]]
            if self.opposite(a, b) or self.number_clash(a, b):
                continue
            bar = self.shared_bar if self.shares_entity(a, b) else self.other_bar
            out[k] = cos[k] - bar
        return out
