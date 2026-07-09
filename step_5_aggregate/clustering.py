"""Distance-matrix construction and complete-linkage clustering for step 5.

Claim similarity is measured with E5 embeddings when available (cosine of the
query-vs-passage asymmetric pair) and a fuzzy text ratio otherwise. Clustering
is diameter-bounded complete linkage with a soft size cap so near-duplicate
claims group without DBSCAN-style chaining. Caching of the resulting matrix is
orchestrated by :mod:`run`.
"""

from __future__ import annotations

from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import numpy as np

from step_5_aggregate.embeddings import ensure_claim_embeddings


class _Embedder:
    """Tracks whether embedding-based merge is available."""

    def __init__(self, use_embeddings: bool) -> None:
        self.use_embeddings = use_embeddings
        self._failed = False

    def load(
        self,
        claims: list[dict[str, Any]],
        texts: list[str],
        source_path: Path,
    ) -> tuple[list[np.ndarray], list[np.ndarray]] | None:
        if not self.use_embeddings or self._failed:
            return None
        try:
            return ensure_claim_embeddings(claims, texts, source_path)
        except Exception:  # noqa: BLE001 - fall back to fuzzy
            self._failed = True
            return None


def _claim_similarity(
    i: int,
    j: int,
    *,
    texts: list[str],
    query_vectors: list[np.ndarray] | None,
    passage_vectors: list[np.ndarray] | None,
) -> float:
    if query_vectors is not None and passage_vectors is not None:
        from step_1_threads_split.embedding.embedding import cosine_similarity

        sim_ij = cosine_similarity(query_vectors[i], passage_vectors[j])
        sim_ji = cosine_similarity(query_vectors[j], passage_vectors[i])
        return max(sim_ij, sim_ji)
    return SequenceMatcher(None, texts[i], texts[j]).ratio()


def _claim_distance_matrix(
    texts: list[str],
    query_vectors: list[np.ndarray] | None,
    passage_vectors: list[np.ndarray] | None,
) -> np.ndarray:
    n = len(texts)
    dist = np.zeros((n, n), dtype=np.float32)
    for i in range(n):
        for j in range(i + 1, n):
            d = 1.0 - _claim_similarity(
                i, j, texts=texts, query_vectors=query_vectors, passage_vectors=passage_vectors
            )
            dist[i, j] = dist[j, i] = d
    return dist


def _cluster_diameter(members: list[int], dist: np.ndarray) -> float:
    """Largest pairwise distance within the member set (complete-linkage diameter)."""

    if len(members) < 2:
        return 0.0
    return max(dist[i, j] for i in members for j in members if i < j)


def _split_oversized(
    members: list[int],
    dist: np.ndarray,
    *,
    max_size: int,
    keep_together_distance: float,
) -> list[list[int]]:
    """Recursively bisect a cluster until each piece is <= max_size.

    ponytail: soft cap, not hard. An oversized cluster whose complete-linkage
    diameter is already below ``keep_together_distance`` (near-duplicates) stays
    intact instead of being split. Worst case is O(n^2) per oversized cluster
    from the diameter scan; per-topic cluster sizes make that fine. If clusters
    ever get large, switch the diameter scan to the cached matrix max.
    """

    if len(members) <= max_size:
        return [members]
    if _cluster_diameter(members, dist) <= keep_together_distance:
        return [members]

    from sklearn.cluster import AgglomerativeClustering

    sub = dist[np.ix_(members, members)]
    halves = AgglomerativeClustering(
        n_clusters=2, metric="precomputed", linkage="complete"
    ).fit_predict(sub)
    out: list[list[int]] = []
    for label in (0, 1):
        half = [members[k] for k, lab in enumerate(halves) if lab == label]
        out.extend(
            _split_oversized(
                half,
                dist,
                max_size=max_size,
                keep_together_distance=keep_together_distance,
            )
        )
    return out


def _cluster(
    dist: np.ndarray,
    *,
    distance_threshold: float,
    max_size: int,
    keep_together_distance: float,
) -> list[int]:
    """Complete-linkage clusters bounded by diameter, then capped by size.

    Members within ``distance_threshold`` of each other group together (no
    DBSCAN-style chaining); oversized groups are bisected via _split_oversized.
    """

    from sklearn.cluster import AgglomerativeClustering

    n = dist.shape[0]
    if n == 0:
        return []
    if n == 1:
        return [0]

    base = AgglomerativeClustering(
        n_clusters=None,
        distance_threshold=distance_threshold,
        metric="precomputed",
        linkage="complete",
    ).fit_predict(dist)

    groups: dict[int, list[int]] = defaultdict(list)
    for idx, label in enumerate(base.tolist()):
        groups[label].append(idx)

    labels = [0] * n
    next_id = 0
    for members in groups.values():
        for piece in _split_oversized(
            members,
            dist,
            max_size=max_size,
            keep_together_distance=keep_together_distance,
        ):
            for idx in piece:
                labels[idx] = next_id
            next_id += 1
    return labels


def _medoid_index(members: list[int], dist: np.ndarray) -> int:
    """Index of the member with the smallest total distance to all others."""

    if len(members) == 1:
        return members[0]
    best = members[0]
    best_sum = float("inf")
    for i in members:
        total = sum(dist[i, j] for j in members if j != i)
        if total < best_sum:
            best_sum = total
            best = i
    return best
