"""Step 5 and disputes (C3 ``counters``): a cannot-link in the grouping, lifted to the groups'
``contradicts``; the reviewer's manual links / rejections and merges keep winning.

Synthetic data only (``aggregate_fixtures``: vectors are planted, so "near-identical wording"
is a cosine of 0.995 or more).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import step_5_aggregate.similarity as similarity
from step_5_aggregate.derive import counter_links
from step_5_aggregate.inputs import load_inputs
from step_5_aggregate.reviewer.suggestions import CandidatePairs
from step_5_aggregate.run import run
from tests.aggregate_fixtures import Corpus, angle_for

CLOSE = angle_for(0.995)


@pytest.fixture
def corpus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Corpus:
    built = Corpus(tmp_path)
    monkeypatch.setattr(similarity, "embed_texts", built.embed)
    # a1 / a2: same stance, same entity, near-identical vectors -> one group unless linked.
    built.add("a1", entities=["Alpha"], date="2024-01-01", supporters=[("p1", "2024-01-01")])
    built.add("a2", angle=CLOSE, entities=["Alpha"], date="2024-01-02",
              supporters=[("p2", "2024-01-02")])
    built.add("c1", plane=2, date="2024-01-07")
    return built


def edited(corpus: Corpus) -> dict[str, Any]:
    return json.loads(corpus.artifact.edited.read_text("utf-8"))


def save_edited(corpus: Corpus, payload: dict[str, Any]) -> None:
    corpus.artifact.edited.write_text(json.dumps(payload), encoding="utf-8")


def by_member(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {c: g for g in payload["groups"] for c in g["member_claim_ids"]}


def test_without_a_link_near_identical_claims_share_a_group(corpus: Corpus) -> None:
    stats = run(**corpus.paths())
    groups = by_member(edited(corpus))
    assert groups["a1"] is groups["a2"]
    assert (stats["counter_links"], stats["counter_pairs_in_one_group"]) == (0, 0)


def test_countering_claims_never_merge_and_their_groups_contradict(corpus: Corpus) -> None:
    corpus.link("a1", "a2")
    stats = run(**corpus.paths())
    groups = by_member(edited(corpus))
    assert groups["a1"]["group_id"] != groups["a2"]["group_id"]
    assert groups["a1"]["contradicts"] == [groups["a2"]["group_id"]]
    assert groups["a2"]["contradicts"] == [groups["a1"]["group_id"]]
    assert groups["c1"]["contradicts"] == []
    assert stats["counter_links"] == 1 and stats["contradiction_links"] == 1
    # Counts stay each side's own: nobody is counted twice because of the link.
    assert (groups["a1"]["supporter_count"], groups["a1"]["opposer_count"]) == (1, 0)
    assert (groups["a2"]["supporter_count"], groups["a2"]["opposer_count"]) == (1, 0)


def test_a_link_listed_on_one_side_only_still_counts(corpus: Corpus) -> None:
    corpus.claim("a2")["counters"] = ["a1"]  # e.g. a hand-edited claims file
    run(**corpus.paths())
    groups = by_member(edited(corpus))
    assert groups["a1"]["contradicts"] == [groups["a2"]["group_id"]]


def test_a_link_to_a_claim_that_is_gone_is_ignored(corpus: Corpus) -> None:
    corpus.claim("a2")["counters"] = ["deleted-claim"]
    run(**corpus.paths())
    groups = by_member(edited(corpus))
    assert groups["a1"] is groups["a2"] and groups["a1"]["contradicts"] == []


def test_a_dissent_cannot_join_the_group_of_the_claim_it_answers(corpus: Corpus) -> None:
    """a3 matches a1 on its own, but a1's group holds a2, which a3 disputes (complete
    linkage): a3 opens a group of its own, linked to the whole a1 + a2 group."""

    corpus.add("a3", angle=-CLOSE, entities=["Alpha"], date="2024-01-03")
    corpus.link("a2", "a3")
    run(**corpus.paths())
    groups = by_member(edited(corpus))
    assert groups["a1"] is groups["a2"]
    assert groups["a3"]["member_claim_ids"] == ["a3"]
    assert groups["a3"]["contradicts"] == [groups["a1"]["group_id"]]
    assert groups["a1"]["contradicts"] == [groups["a3"]["group_id"]]


def test_a_new_dissent_on_a_rerun_respects_the_cannot_link(corpus: Corpus) -> None:
    run(**corpus.paths())
    before = by_member(edited(corpus))
    corpus.add("a4", angle=CLOSE / 2, entities=["Alpha"], date="2024-02-01")
    corpus.link("a1", "a4")  # step 3's update: a new claim disputes an existing one
    stats = run(**corpus.paths())
    groups = by_member(edited(corpus))
    assert (stats["new_claims"], stats["opened"], stats["joined"]) == (1, 1, 0)
    assert groups["a1"]["group_id"] == before["a1"]["group_id"]
    assert groups["a4"]["group_id"] != before["a1"]["group_id"]
    assert groups["a4"]["contradicts"] == [groups["a1"]["group_id"]]
    assert groups["a1"]["contradicts"] == [groups["a4"]["group_id"]]


def test_rerun_keeps_the_links_and_writes_nothing(corpus: Corpus) -> None:
    corpus.link("a1", "a2")
    run(**corpus.paths())
    first = edited(corpus)
    again = run(**corpus.paths())
    assert not again["written"]
    assert edited(corpus)["groups"] == first["groups"]


def test_a_rejected_link_stays_rejected_and_a_manual_link_still_adds(corpus: Corpus) -> None:
    corpus.link("a1", "a2")
    run(**corpus.paths())
    payload = edited(corpus)
    groups = by_member(payload)
    a1, a2, c1 = groups["a1"], groups["a2"], groups["c1"]
    a1["contradicts_rejected"] = [a2["group_id"]]  # what the groups tool writes
    c1["contradicts_manual"] = [a2["group_id"]]
    save_edited(corpus, payload)
    corpus.add("d1", plane=3, date="2024-03-01")  # a later run with something new
    run(**corpus.paths())
    groups = by_member(edited(corpus))
    assert groups["a1"]["contradicts"] == []
    assert groups["a2"]["contradicts"] == [groups["c1"]["group_id"]]
    assert groups["a1"]["contradicts_rejected"] == [groups["a2"]["group_id"]]
    assert groups["a1"]["group_id"] != groups["a2"]["group_id"]  # still never merged


def test_a_manual_merge_of_both_sides_wins_without_a_link(
    corpus: Corpus, caplog: pytest.LogCaptureFixture
) -> None:
    corpus.link("a1", "a2")
    run(**corpus.paths())
    payload = edited(corpus)
    groups = by_member(payload)
    survivor, other = groups["a1"], groups["a2"]
    survivor["member_claim_ids"] += other["member_claim_ids"]
    survivor["merged_from"] = [other["group_id"]]
    payload["groups"] = [g for g in payload["groups"] if g is not other]
    save_edited(corpus, payload)
    corpus.add("d1", plane=3, date="2024-03-01")
    with caplog.at_level("INFO"):
        stats = run(**corpus.paths())
    groups = by_member(edited(corpus))
    assert groups["a1"] is groups["a2"] and groups["a1"]["contradicts"] == []
    assert (stats["counter_links"], stats["counter_pairs_in_one_group"]) == (0, 1)
    assert "a1 and a2 dispute each other but share a group" in caplog.text


def test_a_must_link_of_both_sides_wins_too(corpus: Corpus) -> None:
    corpus.link("a1", "a2")
    corpus.must_links = [{"id": "m1", "claim_ids": ["a1", "a2"], "representative": "a1"}]
    stats = run(**corpus.paths())
    groups = by_member(edited(corpus))
    assert groups["a1"] is groups["a2"]
    assert stats["counter_pairs_in_one_group"] == 1


def test_data_without_links_comes_out_unchanged(tmp_path: Path, corpus: Corpus) -> None:
    """The same corpus with ``counters: []`` and with no ``counters`` key at all."""

    run(**corpus.paths())
    with_field = edited(corpus)
    for claim in corpus.claims:
        del claim["counters"]
    corpus.root = tmp_path / "without"
    corpus.root.mkdir()
    run(**corpus.paths())
    without = edited(corpus)
    assert with_field["groups"] == without["groups"]
    assert with_field["metadata"]["inputs"] == without["metadata"]["inputs"]


def test_counter_links_and_the_groups_tool_suggestions(corpus: Corpus) -> None:
    """The groups tool's merge suggestions use step 5's scorer: a disputing pair is never
    suggested, even with the lowered bars."""

    corpus.link("a1", "a2")
    paths = corpus.paths()
    run(**paths)
    inputs = load_inputs(paths["claims_path"], paths["entities_path"], paths["audit_path"])
    scorer = similarity.ClaimScorer.embed(inputs.claims, inputs.claim_entities)
    assert scorer.counter_pairs() == [("a1", "a2")]
    assert CandidatePairs(inputs, scorer).matches == {}
    links = counter_links(edited(corpus)["groups"], scorer)
    assert len(links.group_pairs) == 1 and links.within_group == ()
