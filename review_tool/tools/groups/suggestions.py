"""Merge suggestions: pairs of groups that are close, but not close enough for step 5 to merge.

Step 5 merges only what it is nearly sure about, so most groups are single claims and the
reviewer finds the remaining merges. A pair of groups is suggested when EVERY claim of one
matches EVERY claim of the other (complete linkage, as in step 5) under step 5's own pair test
(``ClaimScorer``: no opposite stances, no clash of quoted numbers, an entity-dependent cosine
bar) with both bars lowered by ``aggregate.review.suggestion_band``. Best first: the pair whose
weakest claim pair comes closest to step 5's real bar.

Vectors come from the embedding cache only (``CachedEmbedder``): building suggestions never
calls the paid API. Claims without a cached vector (step 5 has not run on them yet) make
``EmbeddingsMissing``; the tool then offers the step-5 run instead.

The claim pairs above the lowered bars depend on the claims alone and are found once per load
(``CandidatePairs``); ranking them into group pairs (``rank_suggestions``) is redone after
every edit. "Not the same" decisions are kept per claim pair (``rejected_pairs``), so they
survive merges, splits and step-5 re-runs, which never change claim ids.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from step_5_aggregate.groups import Group
from step_5_aggregate.inputs import StepInputs
from step_5_aggregate.similarity import AGGREGATE_CONFIG, ClaimScorer
from utils.embedder import Embedder

REVIEW_CONFIG = AGGREGATE_CONFIG["review"]
ClaimPair = tuple[str, str]


class EmbeddingsMissing(Exception):
    """Some claim texts have no cached vector; only a step-5 run may embed them (paid)."""

    def __init__(self, count: int) -> None:
        super().__init__(f"{count} claim texts have no cached embedding; run step 5 first")
        self.count = count


class CachedEmbedder(Embedder):
    """The project embedder restricted to its disk cache: a text it would have to send to the
    API raises ``EmbeddingsMissing`` instead (no client is ever created)."""

    def _embed_missing(self, store: Any, kind: Any, keys: list[str], texts: list[str]) -> None:
        raise EmbeddingsMissing(len(keys))


def cached_vectors(texts: Sequence[str]) -> np.ndarray:
    """Step 5's vectors for ``texts`` (same model and kind), from the cache only."""

    return CachedEmbedder.for_config().embed_texts(texts, AGGREGATE_CONFIG["embedding_kind"])


def pair_key(a: str, b: str) -> ClaimPair:
    return (a, b) if a <= b else (b, a)


def rejected_pairs(records: Iterable[Mapping[str, Any]]) -> set[ClaimPair]:
    """Every claim pair a "not the same" record covers (claims of one side × the other)."""

    return {
        pair_key(a, b)
        for record in records
        for a in record.get("claims_a") or []
        for b in record.get("claims_b") or []
    }


@dataclass(frozen=True)
class ClaimMatch:
    """One claim pair above the lowered bars: its cosine and step 5's real bar for it."""

    cosine: float
    bar: float
    shared_entity: bool

    @property
    def margin(self) -> float:
        """Cosine minus step 5's bar: >= 0 means step 5 itself would accept the pair."""
        return self.cosine - self.bar


class CandidatePairs:
    """The claim pairs close enough to suggest, found once per load with step 5's scorer
    (``scorer``: built on ``inputs`` with step 5's own bars)."""

    def __init__(self, inputs: StepInputs, scorer: ClaimScorer) -> None:
        band = float(REVIEW_CONFIG["suggestion_band"])
        lowered = ClaimScorer(
            inputs.claims,
            inputs.claim_entities,
            scorer.vectors,
            merge_threshold={
                "shared_entity": scorer.shared_bar - band,
                "no_shared_entity": scorer.other_bar - band,
            },
        )
        floor = min(lowered.shared_bar, lowered.other_bar)
        block = int(REVIEW_CONFIG["similarity_block_rows"])
        ids, vectors = scorer.ids, scorer.vectors
        self.matches: dict[ClaimPair, ClaimMatch] = {}
        for start in range(0, len(ids), block):
            for i, row in enumerate(vectors[start : start + block] @ vectors.T, start):
                later = [int(j) for j in np.flatnonzero(row >= floor) if j > i]
                if not later:
                    continue
                margins = lowered.margins(ids[i], [ids[j] for j in later])
                for k in np.flatnonzero(margins >= 0):
                    j = later[k]
                    shared = scorer.shares_entity(i, j)
                    self.matches[pair_key(ids[i], ids[j])] = ClaimMatch(
                        cosine=float(row[j]),
                        bar=scorer.shared_bar if shared else scorer.other_bar,
                        shared_entity=shared,
                    )


@dataclass(frozen=True)
class Suggestion:
    """Two groups (``a`` older than ``b``) every cross claim pair of which matches."""

    a: str
    b: str
    margin: float
    min_cosine: float
    max_cosine: float
    shared_entity: bool

    @property
    def key(self) -> str:
        return f"{self.a}|{self.b}"


def rank_suggestions(
    groups: Sequence[Group], candidates: CandidatePairs, rejected: set[ClaimPair]
) -> list[Suggestion]:
    """Every qualifying group pair, best first (weakest margin, then strongest cosine, then
    the older pair). A rejected claim pair disqualifies its two groups."""

    position = {g["group_id"]: k for k, g in enumerate(groups)}
    group_of = {cid: g["group_id"] for g in groups for cid in g["member_claim_ids"]}
    size = {g["group_id"]: len(g["member_claim_ids"]) for g in groups}
    found: dict[tuple[str, str], list[ClaimMatch]] = {}
    blocked: set[tuple[str, str]] = set()
    for (x, y), match in candidates.matches.items():
        gx, gy = group_of.get(x), group_of.get(y)
        if gx is None or gy is None or gx == gy:
            continue
        key = (gx, gy) if position[gx] < position[gy] else (gy, gx)
        if (x, y) in rejected:
            blocked.add(key)
        else:
            found.setdefault(key, []).append(match)
    out = [
        Suggestion(
            a=a,
            b=b,
            margin=min(m.margin for m in matches),
            min_cosine=min(m.cosine for m in matches),
            max_cosine=max(m.cosine for m in matches),
            shared_entity=all(m.shared_entity for m in matches),
        )
        for (a, b), matches in found.items()
        if (a, b) not in blocked and len(matches) == size[a] * size[b]
    ]
    out.sort(key=lambda s: (-s.margin, -s.max_cosine, position[s.a], position[s.b]))
    return out
