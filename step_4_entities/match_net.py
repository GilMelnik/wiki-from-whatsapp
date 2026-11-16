"""Stage 1 of the model matching: the net of candidate pairs for every new name.

A candidate is a pair of names that might be the same thing. Two sources, both measured on the
real run (task 70's plan):

* spelling: the base string / transliteration similarity of ``EntityPairIndex`` at
  ``entities.match.net.spelling_similarity`` (lower than the merge threshold), a whole-word
  prefix, one seed entry, or a confident contact both share;
* meaning: the ``neighbours`` nearest names by the project's hosted embedder
  (``utils.embedder``, kind ``net.embedding_kind``), once on the bare name and once on the name
  followed by a few of its claims. Ranks, not a cosine bar: every name has a neighbour over
  any useful bar.

A pair that spelling alone would merge or flag today (similarity at the merge threshold, a
prefix, a seed link, a shared contact, or a look-alike pair the context guard holds apart) is
``strong``: it skips the screen and always reaches the judge. On an update the new names'
partners are the existing names too; pairs of two existing names are never made.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from step_4_entities.match_claims import ClaimSampler
from step_4_entities.normalize import _is_word_prefix
from step_4_entities.pair_index import EntityPairIndex
from utils.config import ENTITIES_CONFIG

NET_CONFIG = ENTITIES_CONFIG["match"]["net"]
MERGE_THRESHOLD = ENTITIES_CONFIG["similarity_threshold"]

EmbedFunction = Callable[[Sequence[str], str], np.ndarray]
PairKey = tuple[str, str]

# Where a candidate pair came from (stored as counts in entity_matches.json).
SOURCE_SPELLING = "spelling"
SOURCE_PREFIX = "prefix"
SOURCE_SEED = "seed"
SOURCE_CONTACT = "contact"
SOURCE_MEANING_NAME = "meaning_name"
SOURCE_MEANING_CLAIMS = "meaning_claims"
SPELLING_SOURCES = (SOURCE_SPELLING, SOURCE_PREFIX, SOURCE_SEED, SOURCE_CONTACT)
MEANING_SOURCES = (SOURCE_MEANING_NAME, SOURCE_MEANING_CLAIMS)


def pair_key(a: str, b: str) -> PairKey:
    return (a, b) if a <= b else (b, a)


@dataclass
class Candidate:
    """One candidate pair: its sources, its spelling similarity and whether it is strong."""

    names: PairKey
    sources: set[str] = field(default_factory=set)
    similarity: float = 0.0
    strong: bool = False


class CandidateNet:
    """The candidate pairs of the new names among ``members`` (one member per distinct name)."""

    def __init__(
        self,
        members: list[dict[str, Any]],
        new_names: Collection[str],
        index: EntityPairIndex,
        sampler: ClaimSampler,
        embed: EmbedFunction,
    ) -> None:
        self.members = members
        self.names = [m["name"] for m in members]
        wanted = set(new_names)
        self.new = [i for i, name in enumerate(self.names) if name in wanted]
        self.is_new = [name in wanted for name in self.names]
        self.index = index
        self.sampler = sampler
        self.embed = embed

    def _candidate(self, found: dict[PairKey, Candidate], i: int, j: int) -> Candidate:
        key = pair_key(self.names[i], self.names[j])
        if key not in found:
            sim, _, held = self.index.pair_signals(i, j)
            found[key] = Candidate(key, similarity=round(sim, 4),
                                   strong=sim >= MERGE_THRESHOLD or held)
        return found[key]

    def _partners(self, i: int) -> list[int]:
        """Every other name for a new name: existing ones, and new ones after it."""

        return [j for j in range(len(self.names)) if j != i and (not self.is_new[j] or j > i)]

    def spelling(self, found: dict[PairKey, Candidate]) -> None:
        index = self.index
        for i in self.new:
            for j in self._partners(i):
                base, _ = index._base_similarity(i, j)
                sources = set()
                if base >= NET_CONFIG["spelling_similarity"]:
                    sources.add(SOURCE_SPELLING)
                if _is_word_prefix(index.norms[i], index.norms[j]):
                    sources.add(SOURCE_PREFIX)
                if index.seed_groups[i] is not None and index.seed_groups[i] == index.seed_groups[j]:
                    sources.add(SOURCE_SEED)
                if index.contacts[i] & index.contacts[j]:
                    sources.add(SOURCE_CONTACT)
                if sources:
                    self._candidate(found, i, j).sources |= sources

    def texts(self) -> dict[str, list[str]]:
        """The texts embedded per meaning source: the bare name, the name with a few claims."""

        separator = NET_CONFIG["text_separator"]
        with_claims = []
        for member in self.members:
            picked = self.sampler.spread(member.get("claim_ids") or [], NET_CONFIG["text_claims"])
            claims = separator.join(self.sampler.text(c) for c in picked)
            with_claims.append(f"{member['name']}: {claims}" if claims else member["name"])
        return {SOURCE_MEANING_NAME: list(self.names), SOURCE_MEANING_CLAIMS: with_claims}

    def meaning(self, found: dict[PairKey, Candidate]) -> None:
        if not self.new or len(self.names) < 2:
            return
        k = min(NET_CONFIG["neighbours"], len(self.names) - 1)
        for source, texts in self.texts().items():
            vectors = np.asarray(self.embed(texts, NET_CONFIG["embedding_kind"]), dtype=np.float32)
            sims = vectors[self.new] @ vectors.T
            sims[np.arange(len(self.new)), self.new] = -np.inf
            nearest = np.argsort(-sims, axis=1, kind="stable")[:, :k]
            for row, i in enumerate(self.new):
                for j in nearest[row]:
                    self._candidate(found, i, int(j)).sources.add(source)

    def pairs(self, *, meaning: bool = True) -> dict[PairKey, Candidate]:
        """Every candidate pair, by its sorted names (``meaning=False``: spelling only)."""

        found: dict[PairKey, Candidate] = {}
        self.spelling(found)
        if meaning:
            self.meaning(found)
        return dict(sorted(found.items()))
