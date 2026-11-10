"""Group member records (entity strings) into suggested canonical entities."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.cluster import AgglomerativeClustering

from step_4_entities.mentions import Word
from step_4_entities.normalize import normalize_name
from step_4_entities.pair_index import EntityPairIndex
from step_4_entities.records import new_entity


def load_seed_index(seed_path: Path) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    """Curated seed file -> (``normalized alias -> seed id``, ``seed id -> entry``)."""

    if not seed_path.is_file():
        return {}, {}
    data = json.loads(seed_path.read_text(encoding="utf-8"))
    entries = (data.get("entities") or []) if isinstance(data, dict) else (data or [])
    norm_to_seed: dict[str, str] = {}
    seed_by_id: dict[str, dict[str, Any]] = {}
    for idx, entry in enumerate(entries):
        seed_id = entry.get("id") or f"seed{idx}"
        seed_by_id[seed_id] = entry
        for name in [entry.get("canonical", ""), *(entry.get("aliases") or [])]:
            norm = normalize_name(name)
            if norm:
                norm_to_seed.setdefault(norm, seed_id)
    return norm_to_seed, seed_by_id


def _cluster_labels(dist: np.ndarray, similarity_threshold: float) -> list[int]:
    n = dist.shape[0]
    if n < 2:
        return list(range(n))
    labels = AgglomerativeClustering(
        n_clusters=None,
        distance_threshold=1.0 - similarity_threshold,
        metric="precomputed",
        linkage="complete",
    ).fit_predict(dist)
    return labels.tolist()


def _cohesion(indices: list[int], dist: np.ndarray) -> float:
    """Worst (largest) pairwise distance in a cluster, as a similarity."""

    if len(indices) < 2:
        return 1.0
    diameter = max(dist[i, j] for i in indices for j in indices if i < j)
    return round(1.0 - float(diameter), 4)


class EntityClusterer:
    """Scores and groups entity strings with one threshold, seed file and claim analysis."""

    def __init__(
        self,
        analysis: dict[str, list[Word]],
        *,
        similarity_threshold: float,
        seed_path: Path,
    ) -> None:
        self.analysis = analysis
        self.similarity_threshold = similarity_threshold
        self.norm_to_seed, self.seed_by_id = load_seed_index(seed_path)

    def index(self, members: list[dict[str, Any]]) -> EntityPairIndex:
        seeds = [self.norm_to_seed.get(normalize_name(m["name"])) for m in members]
        return EntityPairIndex(
            members,
            self.analysis,
            similarity_threshold=self.similarity_threshold,
            seed_groups=seeds,
        )

    def _seeded(self, entity: dict[str, Any], seed_ids: set[str]) -> None:
        """Adopt the seed's canonical name (when it is a member) and type."""

        for seed_id in sorted(seed_ids):
            entry = self.seed_by_id[seed_id]
            if entry.get("canonical") in entity["aliases"]:
                entity["canonical_name"] = entry["canonical"]
            if entry.get("type"):
                entity["type"] = entry["type"]

    def cluster(
        self, members: list[dict[str, Any]], allocate_id: Callable[[], str]
    ) -> list[dict[str, Any]]:
        """Complete-linkage clusters of ``members`` as new entities, most-claimed first."""

        if not members:
            return []
        # Complete linkage breaks distance ties by input order, so fix the order here (most
        # claims first, as v1 did): measured on the reviewed backup, an alphabetical order
        # loses 18 of 182 correct pairs.
        members = sorted(members, key=lambda m: (-m["count"], m["name"]))
        index = self.index(members)
        dist, held = index.distance_matrix()
        labels = _cluster_labels(dist, self.similarity_threshold)
        grouped: dict[int, list[int]] = defaultdict(list)
        for idx, label in enumerate(labels):
            grouped[label].append(idx)

        def total(indices: list[int]) -> int:
            return sum(members[i]["count"] for i in indices)

        entities: list[dict[str, Any]] = []
        entity_of: dict[int, dict[str, Any]] = {}
        for indices in sorted(grouped.values(), key=lambda ix: (-total(ix), min(ix))):
            signals: set[str] = set()
            for a in indices:
                for b in indices:
                    if a < b:
                        sim, fired, _ = index.pair_signals(a, b)
                        if sim >= self.similarity_threshold:
                            signals |= fired
            entity = new_entity(
                allocate_id(),
                [members[i] for i in indices],
                merge_signals=sorted(signals),
                score=_cohesion(indices, dist),
            )
            self._seeded(entity, {s for i in indices if (s := index.seed_groups[i])})
            entities.append(entity)
            for i in indices:
                entity_of[i] = entity

        # Each look-alike pair the context guard held apart flags both entities for review.
        for i, j in held:
            if entity_of[i] is not entity_of[j]:
                flag_conflict(entity_of[i], entity_of[j])
        return entities


def flag_conflict(a: dict[str, Any], b: dict[str, Any]) -> None:
    """Mark two entities ``ambiguous`` and name each other in ``conflict_with``."""

    for entity, other in ((a, b), (b, a)):
        if entity.get("status", "suggested") == "suggested":
            entity["status"] = "ambiguous"
        entity["conflict_with"] = sorted(
            {*(entity.get("conflict_with") or []), other["canonical_name"]}
        )
