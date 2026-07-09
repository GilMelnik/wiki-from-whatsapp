"""Step 5: aggregate per-thread claims into per-topic knowledge.

For each topic the claims are grouped, near-duplicates merged with complete-linkage
clustering (via cached E5 query/passage embeddings when available, otherwise a fuzzy
text fallback), distinct supporters tallied across threads (message authors and
reaction senders with positive reactions, each user counted once using the PRIVATE audit map),
contradicting stances per entity surfaced, and a month-by-month timeline built.

Claim embeddings and the all-claims distance matrix are cached under ``data/``
and rebuilt only when the claims source changes. This module orchestrates the
pipeline; the heavy lifting lives in :mod:`embeddings`, :mod:`clustering`, and
:mod:`merge` (whose public helpers are re-exported here for callers/tests).

Output: ``data/claims_aggregated.json`` (no sender ids; counts only).
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import numpy as np

from step_5_aggregate.clustering import (
    _Embedder,
    _claim_distance_matrix,
    _cluster,
    _medoid_index,
)
from step_5_aggregate.embeddings import (
    DEFAULT_EMBEDDING_MODEL,
    _claim_ids,
    _claim_texts,
    _write_claim_embeddings,
    ensure_claim_embeddings,
)
from step_5_aggregate.merge import (
    _contradictions,
    _entity_stances,
    _merge_claims,
    build_merged_claim,
)
from step_5_aggregate.resolver import (
    apply_entity_resolution,
    load_deleted_claims,
    load_entity_resolver,
)
from utils.json_io import write_json_file
from utils.paths import (
    AUDIT_PATH,
    CLAIM_DISTANCE_MATRIX_PATH,
    CLAIM_DISTANCE_META_PATH,
    MANUAL_AGGREGATIONS_PATH,
    ORIGINAL_AGGREGATED_PATH,
    resolve_claims_path,
)
from utils.taxonomy import category_title, get_page

# Re-exported for callers/tests that still import these from step_5_aggregate.run.
__all__ = [
    "DEFAULT_EMBEDDING_MODEL",
    "_Embedder",
    "_claim_distance_matrix",
    "_claim_texts",
    "_cluster",
    "_load_audit_records",
    "_medoid_index",
    "_merge_claims",
    "_write_claim_embeddings",
    "apply_manual_aggregations",
    "build_merged_claim",
    "ensure_claim_embeddings",
    "ensure_distance_matrix",
    "run",
]


def _distance_matrix_metadata(
    source_path: Path,
    claims: list[dict[str, Any]],
    *,
    distance_method: str,
) -> dict[str, Any]:
    return {
        "source": str(source_path.resolve()),
        "claim_count": len(claims),
        "claim_ids": _claim_ids(claims),
        "distance_method": distance_method,
    }


def _distance_matrix_need_rebuild(
    meta_path: Path,
    matrix_path: Path,
    expected: dict[str, Any],
) -> bool:
    if not meta_path.exists() or not matrix_path.exists():
        return True
    with meta_path.open(encoding="utf-8") as f:
        stored = json.load(f).get("metadata", {})
    return stored != expected


def ensure_distance_matrix(
    claims: list[dict[str, Any]],
    texts: list[str],
    source_path: Path | str,
    query_vectors: list[np.ndarray] | None,
    passage_vectors: list[np.ndarray] | None,
    *,
    matrix_path: Path | str = CLAIM_DISTANCE_MATRIX_PATH,
    meta_path: Path | str = CLAIM_DISTANCE_META_PATH,
) -> np.ndarray:
    """Build or load the cached all-claims distance matrix."""

    source = Path(source_path).resolve()
    matrix_output = Path(matrix_path)
    meta_output = Path(meta_path)
    distance_method = "embeddings" if query_vectors is not None else "fuzzy"
    expected_meta = _distance_matrix_metadata(
        source, claims, distance_method=distance_method
    )

    if not _distance_matrix_need_rebuild(meta_output, matrix_output, expected_meta):
        return np.load(matrix_output)

    dist = _claim_distance_matrix(texts, query_vectors, passage_vectors)
    matrix_output.parent.mkdir(parents=True, exist_ok=True)
    np.save(matrix_output, dist)
    write_json_file({"metadata": expected_meta}, meta_output)
    return dist


def _load_manual_aggregations(
    path: Path | str | None = None,
) -> list[dict[str, Any]]:
    resolved = Path(path) if path is not None else MANUAL_AGGREGATIONS_PATH
    if not resolved.is_file():
        return []
    with resolved.open(encoding="utf-8") as f:
        return json.load(f).get("aggregations") or []


def apply_manual_aggregations(
    claims: list[dict[str, Any]], aggregations: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Force-merge reviewer-chosen claim groups (Req 4).

    Each group collapses to its representative claim, which carries the other
    members under ``_manual_group`` so ``build_merged_claim`` can still tally
    their support. Returns the reduced claims list; the dropped members no
    longer cluster on their own.

    ponytail: the group lands in the representative's topic(s)/stance only;
    members tagged with other topics follow the representative.
    """

    if not aggregations:
        return claims
    by_id = {c["claim_id"]: c for c in claims}
    drop: set[str] = set()
    for group in aggregations:
        member_ids = [cid for cid in (group.get("claim_ids") or []) if cid in by_id]
        if len(member_ids) < 2:
            continue
        rep = group.get("representative")
        if rep not in member_ids:
            rep = member_ids[0]
        by_id[rep]["_manual_group"] = [by_id[cid] for cid in member_ids]
        drop.update(cid for cid in member_ids if cid != rep)
    if not drop:
        return claims
    return [c for c in claims if c["claim_id"] not in drop]


def _load_audit_records(audit_path: Path | str) -> dict[str, dict[str, Any]]:
    """claim_id -> private audit record (supporters, reactions)."""

    path = Path(audit_path)
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        audit = json.load(f)
    return {rec["claim_id"]: rec for rec in audit["audit"]}


def run(
    claims_path: Path | str | None = None,
    audit_path: Path | str = AUDIT_PATH,
    output_path: Path | str = ORIGINAL_AGGREGATED_PATH,
    use_embeddings: bool = True,
    similarity_threshold: float = 0.86,
    max_cluster_size: int = 8,
    keep_together_similarity: float = 0.97,
) -> dict[str, Any]:
    resolved_claims = Path(claims_path) if claims_path is not None else resolve_claims_path()
    with resolved_claims.open(encoding="utf-8") as f:
        claims_payload = json.load(f)
    claims = claims_payload["claims"]

    deleted_claims = load_deleted_claims()
    if deleted_claims:
        claims = [c for c in claims if c["claim_id"] not in deleted_claims]

    entity_resolver = load_entity_resolver()
    apply_entity_resolution(claims, entity_resolver)
    claims = apply_manual_aggregations(claims, _load_manual_aggregations())

    audit_by_id = _load_audit_records(audit_path)
    embedder = _Embedder(use_embeddings)
    texts = _claim_texts(claims)
    claim_index = {c["claim_id"]: i for i, c in enumerate(claims)}

    embeddings = embedder.load(claims, texts, resolved_claims)
    query_vectors = passage_vectors = None
    if embeddings is not None:
        query_vectors, passage_vectors = embeddings

    dist = ensure_distance_matrix(
        claims,
        texts,
        resolved_claims,
        query_vectors,
        passage_vectors,
    )

    by_topic: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for claim in claims:
        for tag in claim.get("topic_tags") or ["overview"]:
            by_topic[tag].append(claim)

    topics_out: dict[str, Any] = {}
    for topic_id, topic_claims in by_topic.items():
        page = get_page(topic_id)
        indices = [claim_index[c["claim_id"]] for c in topic_claims]
        idx = np.array(indices, dtype=int)
        topic_dist = dist[np.ix_(idx, idx)]
        merged = _merge_claims(
            topic_claims,
            audit_by_id,
            topic_dist,
            similarity_threshold,
            max_cluster_size=max_cluster_size,
            keep_together_similarity=keep_together_similarity,
        )
        entity_stances = _entity_stances(merged)
        timeline = Counter(
            c["date"] for c in topic_claims if c.get("date")
        )
        all_dates = sorted(c["date"] for c in topic_claims if c.get("date"))

        topics_out[topic_id] = {
            "title": page.title_he if page else topic_id,
            "category": page.category if page else "emergent",
            "category_title": category_title(page.category) if page else category_title("emergent"),
            "is_emergent": page is None,
            "claim_count": len(topic_claims),
            "merged_claim_count": len(merged),
            "merged_claims": merged,
            "entity_stances": entity_stances,
            "contradictions": _contradictions(entity_stances),
            "timeline": dict(sorted(timeline.items())),
            "date_range": [all_dates[0], all_dates[-1]] if all_dates else [None, None],
        }

    output = {
        "topics": topics_out,
        "entities_registry": entity_resolver.registry() if entity_resolver else {},
        "metadata": {
            "source": str(resolved_claims),
            "entity_resolution": entity_resolver is not None,
            "topic_count": len(topics_out),
            "total_claims": len(claims),
            "merge_method": (
                "agglomerative_embeddings"
                if (use_embeddings and not embedder._failed)
                else "agglomerative_fuzzy"
            ),
            "similarity_threshold": similarity_threshold,
            "max_cluster_size": max_cluster_size,
            "keep_together_similarity": keep_together_similarity,
        },
    }
    write_json_file(output, Path(output_path))
    return output["metadata"]


if __name__ == "__main__":
    run(similarity_threshold=0.93, max_cluster_size=8, keep_together_similarity=0.95)
