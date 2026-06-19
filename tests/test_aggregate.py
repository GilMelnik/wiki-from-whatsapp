"""Step 5 aggregate: DBSCAN clustering and medoid representative selection."""

from __future__ import annotations

import numpy as np

from step_5_aggregate.run import (
    _Embedder,
    _dbscan,
    _medoid_index,
    _merge_claims,
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
    merged = _merge_claims(claims, {}, _Embedder(use_embeddings=False), similarity_threshold=0.55)
    assert len(merged) == 2
    trio = next(m for m in merged if m["endorsement_count"] == 3)
    assert set(trio["variants"]) == {
        "agency X is very expensive in Israel",
        "agency X is expensive in Israel",
        "agency X is costly in Israel",
    }
    assert trio["claim_text"] == "agency X is expensive in Israel"
