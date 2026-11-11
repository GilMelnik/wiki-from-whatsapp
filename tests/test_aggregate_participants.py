"""Step 5's ``participant_count``: distinct people who wrote in a group's conversations."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import step_5_aggregate.similarity as similarity
from step_5_aggregate.derive import participant_count
from step_5_aggregate.run import run
from tests.aggregate_fixtures import Corpus, angle_for

CLOSE = angle_for(0.995)


@pytest.fixture
def corpus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Corpus:
    built = Corpus(tmp_path)
    monkeypatch.setattr(similarity, "embed_texts", built.embed)
    built.add("a1", thread="t-1", participants=["p1", "p2", "p3"])
    built.add("a2", angle=CLOSE, thread="t-1", participants=["p1", "p2", "p3"])
    built.add("b1", plane=1, thread="t-2", participants=["p2", "p3"])
    built.add("b2", plane=1, angle=CLOSE, thread="t-3", participants=["p3", "p4", "p5"])
    built.add("c1", plane=2, thread="t-4", participants=["p1", "p6"])
    return built


def by_member(corpus: Corpus) -> dict[str, dict[str, Any]]:
    payload = json.loads(corpus.artifact.edited.read_text("utf-8"))
    return {c: g for g in payload["groups"] for c in g["member_claim_ids"]}


def test_one_conversation_and_several_with_overlapping_people(corpus: Corpus) -> None:
    run(**corpus.paths())
    groups = by_member(corpus)
    one = groups["a1"]
    assert one["member_claim_ids"] == ["a1", "a2"]
    assert (one["thread_count"], one["participant_count"]) == (1, 3)
    several = groups["b1"]
    assert several["member_claim_ids"] == ["b1", "b2"]
    # p3 wrote in both conversations: 4 distinct people, not 2 + 3
    assert (several["thread_count"], several["participant_count"]) == (2, 4)
    keys = list(several)
    assert keys[keys.index("thread_count") + 1] == "participant_count"
    assert "p3" not in corpus.artifact.edited.read_text("utf-8")  # counts only


def test_a_manual_merge_counts_distinct_people_across_both(corpus: Corpus) -> None:
    run(**corpus.paths())
    payload = json.loads(corpus.artifact.edited.read_text("utf-8"))
    groups = {g["member_claim_ids"][0]: g for g in payload["groups"]}
    keep, gone = groups["a1"], groups["c1"]
    keep["member_claim_ids"] += gone["member_claim_ids"]
    keep["merged_from"] = [gone["group_id"]]
    payload["groups"].remove(gone)
    corpus.artifact.edited.write_text(json.dumps(payload), encoding="utf-8")
    run(**corpus.paths())
    merged = by_member(corpus)["c1"]
    assert merged["group_id"] == keep["group_id"]
    assert (merged["thread_count"], merged["participant_count"]) == (2, 4)  # p1 p2 p3 p6


def test_the_count_leaves_ids_and_membership_alone(corpus: Corpus) -> None:
    for record in corpus.audit.values():  # an audit written before the field
        record.pop("participants")
    run(**corpus.paths())
    before = json.loads(corpus.artifact.edited.read_text("utf-8"))["groups"]
    assert {g["participant_count"] for g in before} == {None}
    corpus.audit["a1"]["participants"] = ["p1", "p2"]  # one member known: still unknown
    corpus.audit["c1"]["participants"] = ["p1", "p6"]
    result = run(**corpus.paths())
    after = json.loads(corpus.artifact.edited.read_text("utf-8"))["groups"]
    assert result["written"]
    assert [{**g, "participant_count": None} for g in after] == before
    assert [g["participant_count"] for g in after] == [None, None, 2]


def test_participant_count_needs_every_member() -> None:
    audit = {"a": {"participants": ["p1", "p2"]}, "b": {"participants": ["p2", "p3"]}, "c": {}}
    assert participant_count(["a", "b"], audit) == 3
    assert participant_count(["a"], audit) == 2
    assert participant_count(["a", "c"], audit) is None
    assert participant_count(["a", "missing"], audit) is None
