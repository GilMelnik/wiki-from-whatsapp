"""Pairwise similarity between entity strings (members) for clustering and assignment.

Signals: normalized string similarity (same-script spelling variants), a cross-script
transliteration skeleton (Hebrew vs Latin), a whole-word prefix (stronger when the two
strings share a claim), a confident contact shared by both, and the curated seed file.

Context guard: two *short* names that look alike (``Gaya`` / ``Gaia``) may still be two
different things. When their string similarity passes the threshold but the words around
them (TF-IDF over the claims each one stands for, minus the names' own words) barely
overlap, the pair is held apart and both entities are flagged for review. It replaced the
v1 guard built on step-3 subject tags (which claims no longer carry); on the human-reviewed
backup it holds apart more truly different pairs than that guard did, and none that the
reviewer merged (numbers in the task-21 report).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

import numpy as np
from scipy.sparse import csr_matrix
from sklearn.feature_extraction.text import TfidfVectorizer

from step_4_entities.mentions import Word
from step_4_entities.normalize import (
    _is_hebrew,
    _is_short_name,
    _is_word_prefix,
    normalize_name,
    normalize_token,
    transliteration_skeleton,
)
from utils.config import ENTITIES_CONFIG

# Bound once from config for the O(n^2) pairwise scoring hot path below.
MIN_SKELETON_LEN = ENTITIES_CONFIG["min_skeleton_len"]
PREFIX_SIMILARITY = ENTITIES_CONFIG["prefix_similarity"]
GUARD_CAP = ENTITIES_CONFIG["guard_cap"]
GUARD_MIN_CONTEXT = ENTITIES_CONFIG["guard_min_context"]
_SIGNALS = ENTITIES_CONFIG["signals"]
SIGNAL_CO_OCCUR = _SIGNALS["co_occur"]
SIGNAL_CONFIDENT_CONTACT = _SIGNALS["confident_contact"]
SIGNAL_PREFIX = _SIGNALS["prefix"]
SIGNAL_SEED = _SIGNALS["seed"]
SIGNAL_STRING = _SIGNALS["string"]
SIGNAL_TRANSLITERATION = _SIGNALS["transliteration"]


def context_vectors(members: list[dict[str, Any]], analysis: dict[str, list[Word]]) -> csr_matrix:
    """One L2-normalised TF-IDF row per member: the word cores of its claims minus its own words."""

    docs: list[list[str]] = []
    for member in members:
        own = {normalize_token(w) for w in member["name"].split()}
        docs.append(
            [
                core
                for claim_id in member.get("claim_ids") or []
                for word in analysis.get(claim_id, [])
                if (core := normalize_token(word.get("core") or "")) and core not in own
            ]
        )
    if not any(docs):
        return csr_matrix((len(members), 1), dtype=np.float64)
    return TfidfVectorizer(analyzer=lambda doc: doc, sublinear_tf=True).fit_transform(docs)


@dataclass
class EntityPairIndex:
    """Precomputed per-member fields for pairwise similarity."""

    members: list[dict[str, Any]]
    analysis: dict[str, list[Word]]
    similarity_threshold: float
    seed_groups: list[str | None] | None = None
    norms: list[str] = field(init=False)
    raw: list[str] = field(init=False)
    skeletons: list[str] = field(init=False)
    claim_ids: list[set[str]] = field(init=False)
    contacts: list[set[str]] = field(init=False)
    context: csr_matrix = field(init=False)

    def __post_init__(self) -> None:
        if self.seed_groups is None:
            self.seed_groups = [None] * len(self.members)
        self.raw = [m["name"] for m in self.members]
        self.norms = [normalize_name(name) for name in self.raw]
        self.skeletons = [transliteration_skeleton(name) for name in self.raw]
        self.claim_ids = [set(m.get("claim_ids") or []) for m in self.members]
        self.contacts = [_confident_contact_keys(m) for m in self.members]
        self.context = context_vectors(self.members, self.analysis)

    def _base_similarity(self, i: int, j: int) -> tuple[float, bool]:
        """(similarity, transliteration_won) for the base string signal."""

        sim = SequenceMatcher(None, self.norms[i], self.norms[j]).ratio()
        translit = False
        # Cross-script transliteration only: a Hebrew vs Latin pair compared on the
        # consonant skeleton. Same-script pairs are already handled by string sim and
        # must not be re-merged here (that is how distinct same-type entities leak in).
        if _is_hebrew(self.raw[i]) != _is_hebrew(self.raw[j]):
            sa, sb = self.skeletons[i], self.skeletons[j]
            if min(len(sa), len(sb)) >= MIN_SKELETON_LEN:
                tr = SequenceMatcher(None, sa, sb).ratio()
                if tr > sim:
                    sim, translit = tr, True
        return sim, translit

    def _compute_base_signals(self, i: int, j: int) -> tuple[float, set[str]]:
        base, translit = self._base_similarity(i, j)
        sim = base
        signals: set[str] = set()
        if base >= self.similarity_threshold:
            signals.add(SIGNAL_TRANSLITERATION if translit else SIGNAL_STRING)

        # Prefix is a medium signal; co-occurrence (sharing a claim) only matters as a
        # confirmation of a prefix relationship — alone it merges unrelated entities.
        if _is_word_prefix(self.norms[i], self.norms[j]):
            sim = max(sim, PREFIX_SIMILARITY)
            signals.add(SIGNAL_PREFIX)
            if self.claim_ids[i] & self.claim_ids[j]:
                sim = 1.0
                signals.add(SIGNAL_CO_OCCUR)

        return sim, signals

    def context_similarity(self, i: int, j: int) -> float:
        return float(self.context[i].multiply(self.context[j]).sum())

    def _held_apart(self, i: int, j: int, sim: float) -> bool:
        """Look-alike short names whose contexts barely overlap. Two strings that share a
        claim, or a string with no context words at all, are never held apart."""

        return (
            sim >= self.similarity_threshold
            and _is_short_name(self.norms[i])
            and _is_short_name(self.norms[j])
            and not self.claim_ids[i] & self.claim_ids[j]
            and self.context[i].nnz > 0
            and self.context[j].nnz > 0
            and self.context_similarity(i, j) < GUARD_MIN_CONTEXT
        )

    def pair_signals(self, i: int, j: int) -> tuple[float, set[str], bool]:
        """Final similarity, the signals that fired, and whether the context guard held
        a look-alike short pair apart for review."""

        sim, signals = self._compute_base_signals(i, j)
        if self.contacts[i] & self.contacts[j]:
            return 1.0, signals | {SIGNAL_CONFIDENT_CONTACT}, False
        if self.seed_groups[i] is not None and self.seed_groups[i] == self.seed_groups[j]:
            return 1.0, signals | {SIGNAL_SEED}, False
        if self._held_apart(i, j, sim):
            return min(sim, GUARD_CAP), signals, True
        return sim, signals, False

    def distance_matrix(self) -> tuple[np.ndarray, list[tuple[int, int]]]:
        """``1 - similarity`` for every pair, and the pairs the context guard held apart."""

        # ponytail: full O(n^2) scan, only for a first run / --rebuild (~1k strings, ~30 s);
        # incremental runs score new strings against existing members only.
        n = len(self.norms)
        dist = np.zeros((n, n), dtype=np.float32)
        held: list[tuple[int, int]] = []
        for i in range(n):
            for j in range(i + 1, n):
                sim, _, guarded = self.pair_signals(i, j)
                dist[i, j] = dist[j, i] = 1.0 - sim
                if guarded:
                    held.append((i, j))
        return dist, held


def _confident_contact_keys(member: dict[str, Any]) -> set[str]:
    return {
        f"{kind}:{value}"
        for kind, values in (member.get("contacts") or {}).items()
        for value in values
    }
