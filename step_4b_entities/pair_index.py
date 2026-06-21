"""Precomputed per-entity fields for O(n²) pairwise clustering."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

import numpy as np

from step_4b_entities.constants import (
    MIN_SKELETON_LEN,
    PREFIX_SIMILARITY,
    SIGNAL_CO_OCCUR,
    SIGNAL_CONFIDENT_CONTACT,
    SIGNAL_PREFIX,
    SIGNAL_SEED,
    SIGNAL_STRING,
    SIGNAL_TRANSLITERATION,
    SIMILARITY_THRESHOLD,
    TOPIC_GUARD_CAP,
)
from step_4b_entities.normalize import (
    _is_hebrew,
    _is_short_name,
    _is_word_prefix,
    transliteration_skeleton,
)


@dataclass
class EntityPairIndex:
    """Precomputed per-entity fields for pairwise similarity and clustering."""

    entities: list[dict[str, Any]]
    seed_groups: list[str | None] | None = None
    norms: list[str] = field(init=False)
    raw: list[str] = field(init=False)
    skeletons: list[str] = field(init=False)
    claim_ids: list[set[str]] = field(init=False)
    contacts: list[set[str]] = field(init=False)
    topics: list[set[str]] = field(init=False)

    def __post_init__(self) -> None:
        if self.seed_groups is None:
            self.seed_groups = [None] * len(self.entities)
        names = [e["name"] for e in self.entities]
        self.raw = names
        self.norms = [e["normalized"] for e in self.entities]
        self.skeletons = [transliteration_skeleton(name) for name in names]
        self.claim_ids = [set(e.get("claim_ids") or []) for e in self.entities]
        self.contacts = [_confident_contact_keys(e) for e in self.entities]
        self.topics = [set(e.get("topics") or []) for e in self.entities]

    def names(self) -> list[str]:
        return self.raw

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
        if base >= SIMILARITY_THRESHOLD:
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

    def _apply_topic_guard(self, i: int, j: int, sim: float) -> tuple[float, bool]:
        if (
            sim >= SIMILARITY_THRESHOLD
            and _is_short_name(self.norms[i])
            and _is_short_name(self.norms[j])
            and not (self.topics[i] & self.topics[j])
        ):
            return min(sim, TOPIC_GUARD_CAP), True
        return sim, False

    def pair_signals(self, i: int, j: int) -> tuple[float, set[str], bool]:
        """Final similarity, the signals that fired, and whether the topic guard held
        a string match apart for review."""

        contact_link = bool(self.contacts[i] & self.contacts[j])
        seed_link = (
            self.seed_groups[i] is not None
            and self.seed_groups[i] == self.seed_groups[j]
        )

        sim, signals = self._compute_base_signals(i, j)

        if contact_link:
            return 1.0, signals | {SIGNAL_CONFIDENT_CONTACT}, False
        if seed_link:
            return 1.0, signals | {SIGNAL_SEED}, False

        sim, guarded = self._apply_topic_guard(i, j, sim)
        return sim, signals, guarded

    def distance_matrix(self) -> np.ndarray:
        # ponytail: full O(n^2) pairwise scan. Fine for the few hundred distinct entity
        # strings here; if this grows to thousands, add blocking on normalized prefix.
        n = len(self.norms)
        dist = np.zeros((n, n), dtype=np.float32)
        for i in range(n):
            for j in range(i + 1, n):
                sim, _, _ = self.pair_signals(i, j)
                dist[i, j] = dist[j, i] = 1.0 - sim
        return dist

    def signal_signature(self) -> str:
        """Hash of inputs ``pair_signals`` depends on beyond the names."""

        payload = [
            [
                sorted(e.get("claim_ids") or []),
                e.get("contacts") or {},
                sorted(e.get("topics") or []),
                seed,
            ]
            for e, seed in zip(self.entities, self.seed_groups)
        ]
        blob = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hashlib.sha1(blob.encode("utf-8")).hexdigest()


def _confident_contact_keys(entity: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    for kind, values in (entity.get("contacts") or {}).items():
        for value in values:
            keys.add(f"{kind}:{value}")
    return keys
