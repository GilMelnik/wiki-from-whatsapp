"""Tests for plan_reviewer.store."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from plan_reviewer.store import PlanStore


@pytest.fixture
def plan_files(tmp_path: Path) -> tuple[Path, Path]:
    aggregated = {
        "topics": {
            "parenting": {
                "title": "הורות",
                "category": "parenting",
                "claim_count": 2,
                "merged_claim_count": 1,
                "merged_claims": [
                    {
                        "claim_text": "טיפ A",
                        "stance": "neutral",
                        "support_count": 3,
                        "source_claim_ids": ["t1-c0"],
                        "entities": [],
                        "date_range": ["2024-01", "2024-01"],
                    }
                ],
                "contradictions": [],
                "timeline": {},
                "date_range": [None, None],
            },
            "baby-gear": {
                "title": "ציוד",
                "category": "parenting",
                "claim_count": 1,
                "merged_claim_count": 1,
                "merged_claims": [
                    {
                        "claim_text": "טיפ B",
                        "stance": "positive",
                        "support_count": 2,
                        "source_claim_ids": ["t2-c0"],
                        "entities": ["בקבוק"],
                        "date_range": ["2024-02", "2024-02"],
                    }
                ],
                "contradictions": [],
                "timeline": {},
                "date_range": [None, None],
            },
        },
        "metadata": {},
    }
    plan = {
        "pages": [
            {
                "id": "parenting",
                "title": "הורות",
                "category": "parenting",
                "source_tags": ["parenting"],
                "search_focus": "parenting",
                "rationale": "",
            },
            {
                "id": "baby-gear",
                "title": "ציוד לתינוק",
                "category": "parenting",
                "source_tags": ["baby-gear"],
                "search_focus": "baby gear",
                "rationale": "",
            },
        ],
        "links": [{"from": "parenting", "to": "baby-gear", "reason": "related"}],
    }
    agg_path = tmp_path / "aggregated.json"
    plan_path = tmp_path / "plan.json"
    agg_path.write_text(json.dumps(aggregated, ensure_ascii=False), encoding="utf-8")
    plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    return plan_path, agg_path


def test_update_page(plan_files: tuple[Path, Path]) -> None:
    plan_path, agg_path = plan_files
    store = PlanStore(plan_path=plan_path, aggregated_path=agg_path)
    store.load()
    page = store.update_page(
        "parenting",
        title="הורות ותינוקות",
        category="parenting",
        search_focus="gay surrogacy parenting",
    )
    assert page["title"] == "הורות ותינוקות"
    assert page["search_focus"] == "gay surrogacy parenting"


def test_merge_pages(plan_files: tuple[Path, Path]) -> None:
    plan_path, agg_path = plan_files
    store = PlanStore(plan_path=plan_path, aggregated_path=agg_path)
    store.load()
    merged = store.merge_pages("baby-gear", "parenting")
    assert set(merged["source_tags"]) == {"parenting", "baby-gear"}
    assert store.get_page("baby-gear") is None
    assert len(store.pages) == 1
    links = store._plan["links"]
    assert all(link["from"] != "baby-gear" and link["to"] != "baby-gear" for link in links)


def test_move_claim(plan_files: tuple[Path, Path]) -> None:
    plan_path, agg_path = plan_files
    store = PlanStore(plan_path=plan_path, aggregated_path=agg_path)
    store.load()
    moved = store.move_claim(
        topic_id="parenting",
        claim_key="t1-c0",
        target_topic_id="baby-gear",
    )
    assert moved["topic_id"] == "baby-gear"
    assert moved["key"] == "t1-c0"
    parenting = store.topics["parenting"]
    baby_gear = store.topics["baby-gear"]
    assert len(parenting["merged_claims"]) == 0
    assert len(baby_gear["merged_claims"]) == 2
