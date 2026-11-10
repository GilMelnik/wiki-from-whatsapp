"""Step 5 grouping: the pair rule, complete linkage, people counting, ids, contradictions."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

import step_5_aggregate.similarity as similarity
from step_5_aggregate.derive import people
from step_5_aggregate.groups import allocate_group_id, used_group_ids
from step_5_aggregate.run import run
from step_5_aggregate.similarity import AGGREGATE_CONFIG, numbers_in
from tests.aggregate_fixtures import Corpus, angle_for

BARS = AGGREGATE_CONFIG["merge_threshold"]
BETWEEN_BARS = angle_for((BARS["shared_entity"] + BARS["no_shared_entity"]) / 2)
CLOSE = angle_for(0.995)


@pytest.fixture
def corpus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Corpus:
    built = Corpus(tmp_path)
    monkeypatch.setattr(similarity, "embed_texts", built.embed)
    return built


def groups_of(corpus: Corpus) -> list[dict[str, Any]]:
    run(**corpus.paths())
    return json.loads(corpus.artifact.edited.read_text("utf-8"))["groups"]


def members(groups: list[dict[str, Any]]) -> list[list[str]]:
    return sorted(g["member_claim_ids"] for g in groups)


def test_shared_entity_lowers_the_bar(corpus: Corpus) -> None:
    corpus.add("a", entities=["Alpha"])
    corpus.add("b", angle=BETWEEN_BARS, entities=["Alpha"], date="2024-02-01")
    corpus.add("c", plane=1)
    corpus.add("d", plane=1, angle=BETWEEN_BARS, date="2024-02-01")
    assert members(groups_of(corpus)) == [["a", "b"], ["c"], ["d"]]


def test_opposite_stances_stay_apart_and_contradict(corpus: Corpus) -> None:
    corpus.add("pos", stance="positive", entities=["Alpha"])
    corpus.add("neg", stance="negative", angle=CLOSE, entities=["Alpha"])
    corpus.add("pos2", stance="positive", plane=1)
    corpus.add("neg2", stance="negative", plane=1, angle=CLOSE)
    groups = {g["representative_claim_id"]: g for g in groups_of(corpus)}
    assert len(groups) == 4
    assert groups["pos"]["contradicts"] == [groups["neg"]["group_id"]]
    assert groups["neg"]["contradicts"] == [groups["pos"]["group_id"]]
    assert groups["pos2"]["contradicts"] == [] and groups["neg2"]["contradicts"] == []


def test_neutral_and_factual_claims_may_join_a_positive_one(corpus: Corpus) -> None:
    corpus.add("a", stance="positive", entities=["Alpha"])
    corpus.add("b", stance="factual", angle=CLOSE, entities=["Alpha"])
    assert members(groups_of(corpus)) == [["a", "b"]]


def test_claims_quoting_different_numbers_never_merge(corpus: Corpus) -> None:
    assert numbers_in("costs 10,000 or 2.5 per form 300") == {"10000", "2.5", "300"}
    corpus.add("a", text="file form 300", entities=["Alpha"])
    corpus.add("b", text="file form 302", angle=CLOSE, entities=["Alpha"])
    corpus.add("c", text="the form 300 is the one", angle=-CLOSE, entities=["Alpha"])
    assert members(groups_of(corpus)) == [["a", "c"], ["b"]]


def test_a_claim_must_match_every_member(corpus: Corpus) -> None:
    half = angle_for(BARS["shared_entity"]) * 0.75
    corpus.add("a", entities=["Alpha"])
    corpus.add("b", angle=half, entities=["Alpha"], date="2024-01-02")
    # c matches b, but not a: complete linkage keeps it out (no chaining).
    corpus.add("c", angle=2 * half, entities=["Alpha"], date="2024-01-03")
    assert members(groups_of(corpus)) == [["a", "b"], ["c"]]


def test_people_are_distinct_across_members_and_months() -> None:
    audit = {
        "a": {
            "supporters": [
                {"person": "p1", "date": "2024-01-05"},
                {"person": "p2", "date": "2024-01-05"},
                {"person": "p3", "date": "2024-02-01"},
            ],
            "opposers": [{"person": "p4", "date": "2024-02-03"}],
        },
        "b": {
            "supporters": [
                {"person": "p1", "date": "2024-03-01"},  # p1 again: counted once
                {"person": "p4", "date": "2024-03-02"},  # p4 changed their mind: supporter
                {"person": "p5", "date": "2024-02-01"},
            ],
            "opposers": [{"person": "p5", "date": "2024-02-01"}],  # same day: opposer wins
        },
    }
    out = people(["a", "b"], audit)
    assert out["supporter_count"] == 4  # p1 p2 p3 p4
    assert out["opposer_count"] == 1  # p5
    assert out["by_month"] == {
        "2024-01": {"supporters": 2, "opposers": 0},
        "2024-02": {"supporters": 1, "opposers": 2},
        "2024-03": {"supporters": 2, "opposers": 0},
    }


def test_group_fields_and_id_from_the_oldest_claim(corpus: Corpus) -> None:
    corpus.add("late", entities=["Alpha"], date="2024-05-01", supporters=[("p1", "2024-05-01")])
    corpus.add(
        "early",
        angle=CLOSE,
        entities=["Alpha", "Beta"],
        date="2023-12-30",
        supporters=[("p1", "2023-12-30"), ("p2", "2023-12-30")],
        opposers=[("p3", "2024-01-02")],
    )
    (group,) = groups_of(corpus)
    prefix, chars = AGGREGATE_CONFIG["group_id_prefix"], AGGREGATE_CONFIG["group_id_hash_chars"]
    assert group["group_id"] == prefix + hashlib.sha1(b"early").hexdigest()[:chars]
    assert group["representative_claim_id"] == "early"  # most supporters
    assert group["claim_text"] == "claim early"
    assert group["member_claim_ids"] == ["early", "late"]
    assert group["entities"] == ["e0000", "e0001"]
    assert group["entity_names"] == ["Alpha", "Beta"]
    assert (group["supporter_count"], group["opposer_count"]) == (2, 1)
    assert (group["first_date"], group["last_date"]) == ("2023-12-30", "2024-05-01")
    assert group["thread_count"] == 2
    assert group["by_month"]["2024-01"] == {"supporters": 0, "opposers": 1}


def test_allocate_group_id_never_reuses_an_id() -> None:
    first = allocate_group_id("seed", set())
    groups = [{"group_id": "g-other", "member_claim_ids": ["x"], "merged_from": [first]}]
    used = used_group_ids(groups)
    assert used == {"g-other", first}
    second = allocate_group_id("seed", used)
    assert second != first and second.startswith(AGGREGATE_CONFIG["group_id_prefix"])
