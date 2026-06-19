"""Step 5 aggregate: DBSCAN clustering, medoid selection, and caches."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from step_5_aggregate.run import (
    _claim_distance_matrix,
    _claim_texts,
    _dbscan,
    _medoid_index,
    _merge_claims,
    ensure_claim_embeddings,
    ensure_distance_matrix,
)


def test_dbscan_groups_density_connected_points():
    # 0-1 and 1-2 are close; 0-2 is not — DBSCAN chains through 1.
    dist = np.array(
        [
            [0.0, 0.1, 0.5],
            [0.1, 0.0, 0.1],
            [0.5, 0.1, 0.0],
        ],
        dtype=np.float32,
    )
    labels = _dbscan(dist, eps=0.15, min_samples=2)
    assert labels[0] == labels[1] == labels[2]


def test_dbscan_noise_becomes_singleton_cluster():
    dist = np.array(
        [
            [0.0, 0.9],
            [0.9, 0.0],
        ],
        dtype=np.float32,
    )
    labels = _dbscan(dist, eps=0.15, min_samples=2)
    assert labels[0] != labels[1]


def test_medoid_picks_central_point():
    dist = np.array(
        [
            [0.0, 0.4, 0.4],
            [0.4, 0.0, 0.1],
            [0.4, 0.1, 0.0],
        ],
        dtype=np.float32,
    )
    assert _medoid_index([0, 1, 2], dist) == 1


def test_merge_claims_uses_dbscan_and_medoid():
    claims = [
        {
            "claim_id": "a",
            "claim_text": "agency X is very expensive in Israel",
            "stance": "negative",
            "date": "2024-01",
            "entities": ["agency X"],
            "thread_id": "t1",
            "topic_tags": ["costs"],
            "support_count": 2,
        },
        {
            "claim_id": "b",
            "claim_text": "agency X is expensive in Israel",
            "stance": "negative",
            "date": "2024-02",
            "entities": ["agency X"],
            "thread_id": "t2",
            "topic_tags": ["costs"],
            "support_count": 1,
        },
        {
            "claim_id": "c",
            "claim_text": "agency X is costly in Israel",
            "stance": "negative",
            "date": "2024-03",
            "entities": ["agency X"],
            "thread_id": "t4",
            "topic_tags": ["costs"],
            "support_count": 1,
        },
        {
            "claim_id": "d",
            "claim_text": "egg donation laws in Georgia are clear",
            "stance": "positive",
            "date": "2024-04",
            "entities": ["Georgia"],
            "thread_id": "t3",
            "topic_tags": ["costs"],
            "support_count": 3,
        },
    ]
    merged = _merge_claims(
        claims,
        {},
        _claim_distance_matrix(_claim_texts(claims), None, None),
        similarity_threshold=0.55,
    )
    assert len(merged) == 2
    trio = next(m for m in merged if m["endorsement_count"] == 3)
    assert set(trio["variants"]) == {
        "agency X is very expensive in Israel",
        "agency X is expensive in Israel",
        "agency X is costly in Israel",
    }
    assert trio["claim_text"] == "agency X is expensive in Israel"


def test_claim_embeddings_cache_reuses_file(tmp_path: Path):
    claims = [
        {"claim_id": "a", "claim_text": "first claim"},
        {"claim_id": "b", "claim_text": "second claim"},
    ]
    texts = _claim_texts(claims)
    source = tmp_path / "claims.json"
    source.write_text(json.dumps({"claims": claims}), encoding="utf-8")
    query_path = tmp_path / "query.json"
    passage_path = tmp_path / "passage.json"

    fake_query = [np.array([1.0, 0.0], dtype=np.float32), np.array([0.0, 1.0], dtype=np.float32)]
    fake_passage = [np.array([0.9, 0.1], dtype=np.float32), np.array([0.1, 0.9], dtype=np.float32)]

    import step_5_aggregate.run as aggregate

    aggregate._write_claim_embeddings(
        query_path,
        fake_query,
        source_path=source,
        model_name=aggregate.DEFAULT_EMBEDDING_MODEL,
        embedding_dim=2,
        embedding_kind="query",
        claims=claims,
        companion_path=passage_path,
    )
    aggregate._write_claim_embeddings(
        passage_path,
        fake_passage,
        source_path=source,
        model_name=aggregate.DEFAULT_EMBEDDING_MODEL,
        embedding_dim=2,
        embedding_kind="passage",
        claims=claims,
        companion_path=query_path,
    )

    class ExplodingEmbedder:
        def __init__(self, *args, **kwargs):
            raise AssertionError("embedder should not run when cache is valid")

    from step_1_threads_split.embedding import embedding as embedding_mod

    original_cls = embedding_mod.Embedder
    embedding_mod.Embedder = ExplodingEmbedder
    try:
        query_vectors, passage_vectors = ensure_claim_embeddings(
            claims,
            texts,
            source,
            query_path=query_path,
            passage_path=passage_path,
        )
    finally:
        embedding_mod.Embedder = original_cls

    assert len(query_vectors) == len(passage_vectors) == 2
    assert np.allclose(query_vectors[0], fake_query[0])


def test_distance_matrix_cache_reuses_file(tmp_path: Path):
    claims = [
        {"claim_id": "a", "claim_text": "first claim"},
        {"claim_id": "b", "claim_text": "second claim"},
    ]
    texts = _claim_texts(claims)
    source = tmp_path / "claims.json"
    source.write_text(json.dumps({"claims": claims}), encoding="utf-8")
    matrix_path = tmp_path / "matrix.npy"
    meta_path = tmp_path / "matrix.json"
    dist = _claim_distance_matrix(texts, None, None)

    import step_5_aggregate.run as aggregate

    original_build = aggregate._claim_distance_matrix
    calls = {"count": 0}

    def counting_build(*args, **kwargs):
        calls["count"] += 1
        return dist

    aggregate._claim_distance_matrix = counting_build
    try:
        first = ensure_distance_matrix(
            claims,
            texts,
            source,
            None,
            None,
            matrix_path=matrix_path,
            meta_path=meta_path,
        )
        second = ensure_distance_matrix(
            claims,
            texts,
            source,
            None,
            None,
            matrix_path=matrix_path,
            meta_path=meta_path,
        )
    finally:
        aggregate._claim_distance_matrix = original_build

    assert calls["count"] == 1
    assert np.array_equal(first, second)
    assert matrix_path.is_file() and meta_path.is_file()
