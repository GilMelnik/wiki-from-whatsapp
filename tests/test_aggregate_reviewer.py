"""Tests for aggregate_reviewer.store."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from step_5_aggregate.reviewer.store import AggregateStore


@pytest.fixture
def review_files(tmp_path: Path) -> tuple[Path, Path, Path]:
    claims = {
        "claims": [
            {
                "claim_id": "t1-c0",
                "thread_id": "t1",
                "claim_text": "agency X is expensive",
                "topic_tags": ["money-costs"],
                "stance": "negative",
                "date": "2024-01",
                "support_count": 2,
                "entities": ["agency X"],
            },
            {
                "claim_id": "t1-c1",
                "thread_id": "t2",
                "claim_text": "agency X is very expensive",
                "topic_tags": ["money-costs"],
                "stance": "negative",
                "date": "2024-02",
                "support_count": 3,
                "entities": ["agency X"],
            },
            {
                "claim_id": "t2-c0",
                "thread_id": "t3",
                "claim_text": "IVF costs a lot",
                "topic_tags": ["money-costs"],
                "stance": "neutral",
                "date": "2024-03",
                "support_count": 1,
                "entities": [],
            },
        ]
    }
    aggregated = {
        "topics": {
            "money-costs": {
                "title": "עלויות",
                "category": "money",
                "claim_count": 3,
                "merged_claim_count": 2,
                "merged_claims": [
                    {
                        "claim_text": "agency X is expensive",
                        "variants": [
                            "agency X is expensive",
                            "agency X is very expensive",
                        ],
                        "stance": "negative",
                        "support_count": 5,
                        "endorsement_count": 2,
                        "thread_count": 2,
                        "entities": ["agency X"],
                        "source_claim_ids": ["t1-c0", "t1-c1"],
                        "date_range": ["2024-01", "2024-02"],
                    },
                    {
                        "claim_text": "IVF costs a lot",
                        "variants": ["IVF costs a lot"],
                        "stance": "neutral",
                        "support_count": 1,
                        "endorsement_count": 1,
                        "thread_count": 1,
                        "entities": [],
                        "source_claim_ids": ["t2-c0"],
                        "date_range": ["2024-03", "2024-03"],
                    },
                ],
                "contradictions": [],
                "timeline": {},
                "date_range": [None, None],
            }
        },
        "metadata": {},
    }
    agg_path = tmp_path / "aggregated.json"
    claims_path = tmp_path / "claims.json"
    audit_path = tmp_path / "audit.json"
    agg_path.write_text(json.dumps(aggregated, ensure_ascii=False), encoding="utf-8")
    claims_path.write_text(json.dumps(claims, ensure_ascii=False), encoding="utf-8")
    audit_path.write_text(json.dumps({"audit": []}), encoding="utf-8")
    return agg_path, claims_path, audit_path


def test_stats_histogram(review_files: tuple[Path, Path, Path]) -> None:
    agg_path, claims_path, audit_path = review_files
    store = AggregateStore(
        aggregated_path=agg_path, claims_path=claims_path, audit_path=audit_path
    )
    store.load()
    stats = store.stats()
    assert stats["group_count"] == 2
    assert stats["source_claim_count"] == 3
    assert stats["singleton_count"] == 1
    sizes = {b["size"]: b["count"] for b in stats["cluster_size"]}
    assert sizes == {1: 1, 2: 1}
    assert len(stats["cluster_size"]) == 2
    assert stats["max_cluster_size"] == 2
    assert stats["cluster_size"][0]["description"] == "טענה אחת: 1 קבוצות"


def test_stats_histogram_large_cluster(review_files: tuple[Path, Path, Path]) -> None:
    agg_path, claims_path, audit_path = review_files
    aggregated = json.loads(agg_path.read_text(encoding="utf-8"))
    aggregated["topics"]["money-costs"]["merged_claims"].append(
        {
            "claim_text": "big cluster",
            "variants": ["x"] * 12,
            "stance": "neutral",
            "support_count": 1,
            "endorsement_count": 12,
            "thread_count": 1,
            "entities": [],
            "source_claim_ids": [f"big-{i}" for i in range(12)],
            "date_range": ["2024-01", "2024-01"],
        }
    )
    agg_path.write_text(json.dumps(aggregated, ensure_ascii=False), encoding="utf-8")

    store = AggregateStore(
        aggregated_path=agg_path, claims_path=claims_path, audit_path=audit_path
    )
    store.load()
    stats = store.stats()
    sizes = {b["size"]: b["count"] for b in stats["cluster_size"]}
    assert stats["max_cluster_size"] == 12
    assert len(stats["cluster_size"]) == 3
    assert sizes == {1: 1, 2: 1, 12: 1}


def test_set_representative(review_files: tuple[Path, Path, Path]) -> None:
    agg_path, claims_path, audit_path = review_files
    store = AggregateStore(
        aggregated_path=agg_path, claims_path=claims_path, audit_path=audit_path
    )
    store.load()
    group = store.set_representative("money-costs", "t1-c0", "t1-c1")
    assert group["claim_text"] == "agency X is very expensive"


def test_move_member(review_files: tuple[Path, Path, Path]) -> None:
    agg_path, claims_path, audit_path = review_files
    store = AggregateStore(
        aggregated_path=agg_path, claims_path=claims_path, audit_path=audit_path
    )
    store.load()
    moved = store.move_member(
        "money-costs",
        "t1-c0",
        source_claim_id="t1-c1",
        target_group_key="t2-c0",
    )
    assert "t1-c1" in moved["source_claim_ids"]
    topic = store.topics["money-costs"]
    assert len(topic["merged_claims"]) == 2
    singleton = next(g for g in topic["merged_claims"] if g["endorsement_count"] == 1)
    assert singleton["source_claim_ids"] == ["t1-c0"]
    merged_pair = next(g for g in topic["merged_claims"] if g["endorsement_count"] == 2)
    assert set(merged_pair["source_claim_ids"]) == {"t2-c0", "t1-c1"}


def test_split_cluster(review_files: tuple[Path, Path, Path]) -> None:
    agg_path, claims_path, audit_path = review_files
    store = AggregateStore(
        aggregated_path=agg_path, claims_path=claims_path, audit_path=audit_path
    )
    store.load()
    result = store.split_cluster("money-costs", "t1-c0", source_claim_ids=["t1-c1"])
    topic = store.topics["money-costs"]
    assert len(topic["merged_claims"]) == 3
    assert result["original"]["source_claim_ids"] == ["t1-c0"]
    assert result["new"]["source_claim_ids"] == ["t1-c1"]


def test_move_member_empties_source_group(review_files: tuple[Path, Path, Path]) -> None:
    agg_path, claims_path, audit_path = review_files
    store = AggregateStore(
        aggregated_path=agg_path, claims_path=claims_path, audit_path=audit_path
    )
    store.load()
    store.move_member(
        "money-costs",
        "t2-c0",
        source_claim_id="t2-c0",
        target_group_key="t1-c0",
    )
    topic = store.topics["money-costs"]
    assert len(topic["merged_claims"]) == 1
    assert topic["merged_claim_count"] == 1
    assert len(topic["merged_claims"][0]["source_claim_ids"]) == 3
