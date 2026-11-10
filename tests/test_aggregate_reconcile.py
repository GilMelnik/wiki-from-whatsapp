"""Step 5 re-runs (C14): new / changed / removed claims, manual edits, must-links, only, rebuild."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import step_5_aggregate.similarity as similarity
from step_5_aggregate.groups import allocate_group_id, used_group_ids
from step_5_aggregate.run import run
from tests.aggregate_fixtures import Corpus, angle_for

CLOSE = angle_for(0.995)


@pytest.fixture
def corpus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Corpus:
    built = Corpus(tmp_path)
    monkeypatch.setattr(similarity, "embed_texts", built.embed)
    built.add("a1", entities=["Alpha"], date="2024-01-01", supporters=[("p1", "2024-01-01")])
    built.add("a2", angle=CLOSE, entities=["Alpha"], date="2024-02-01")
    built.add("b1", plane=1, date="2024-01-05", supporters=[("p2", "2024-01-05")])
    built.add("b2", plane=1, angle=CLOSE, date="2024-03-01")
    built.add("c1", plane=2, date="2024-01-07")
    return built


def edited(corpus: Corpus) -> dict[str, Any]:
    return json.loads(corpus.artifact.edited.read_text("utf-8"))


def save_edited(corpus: Corpus, payload: dict[str, Any]) -> None:
    corpus.artifact.edited.write_text(json.dumps(payload), encoding="utf-8")


def by_member(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {c: g for g in payload["groups"] for c in g["member_claim_ids"]}


def test_unchanged_run_writes_nothing(corpus: Corpus) -> None:
    first = run(**corpus.paths())
    assert first["written"] and first["group_count"] == 3
    stamp = corpus.artifact.edited.stat().st_mtime_ns
    again = run(**corpus.paths())
    assert not again["written"]
    assert corpus.artifact.edited.stat().st_mtime_ns == stamp


def test_new_claims_join_or_open_and_ids_stay(corpus: Corpus) -> None:
    run(**corpus.paths())
    ids = {c: g["group_id"] for c, g in by_member(edited(corpus)).items()}
    corpus.add("a0", angle=-CLOSE, entities=["Alpha"], date="2023-06-01")  # older than the seed
    corpus.add("d1", plane=3, date="2024-04-01")
    stats = run(**corpus.paths())
    assert (stats["new_claims"], stats["joined"], stats["opened"]) == (2, 1, 1)
    groups = by_member(edited(corpus))
    assert groups["a0"]["group_id"] == ids["a1"]
    assert groups["a0"]["member_claim_ids"] == ["a0", "a1", "a2"]
    assert groups["d1"]["member_claim_ids"] == ["d1"]
    assert all(groups[c]["group_id"] == gid for c, gid in ids.items())


def test_removed_claim_leaves_and_empty_group_is_dropped(corpus: Corpus) -> None:
    run(**corpus.paths())
    corpus.claims = [c for c in corpus.claims if c["claim_id"] not in ("a2", "c1")]
    stats = run(**corpus.paths())
    assert stats["removed_claims"] == 2 and stats["groups_dropped"] == 1
    groups = by_member(edited(corpus))
    assert sorted(groups) == ["a1", "b1", "b2"]
    assert groups["a1"]["member_claim_ids"] == ["a1"]


def test_manual_edits_survive_and_derived_fields_refresh(corpus: Corpus) -> None:
    corpus.add("c2", plane=2, angle=CLOSE, date="2024-01-08")
    corpus.add("c3", plane=2, angle=-CLOSE, date="2024-01-09")
    run(**corpus.paths())
    payload = edited(corpus)
    groups = by_member(payload)
    # merge: the a-group (older) absorbs the b-group
    keep, gone = groups["a1"], groups["b1"]
    keep["member_claim_ids"] += gone["member_claim_ids"]
    keep["merged_from"] = [gone["group_id"]]
    payload["groups"].remove(gone)
    # split: c3 leaves the c-group as its own group; move: c2 joins the merged group
    c_group = groups["c1"]
    c_group["member_claim_ids"] = ["c1"]
    split_id = allocate_group_id("c3", used_group_ids(payload["groups"]))
    payload["groups"].append({"group_id": split_id, "member_claim_ids": ["c3"]})
    keep["member_claim_ids"].append("c2")
    # representative: a2 instead of a1
    keep["representative_claim_id"], keep["representative_manual"] = "a2", True
    save_edited(corpus, payload)
    # upstream: a new supporter for b2, and a new claim close to a1 / a2
    corpus.audit["b2"]["supporters"].append(
        {"person": "p9", "date": "2024-03-01", "via": "message", "message_id": "m"}
    )
    corpus.add("a3", angle=CLOSE / 2, entities=["Alpha"], date="2024-05-01")

    stats = run(**corpus.paths())
    assert stats["claims_left_groups"] == 0
    after = by_member(edited(corpus))
    merged = after["a1"]
    assert merged["group_id"] == keep["group_id"] and merged["merged_from"] == [gone["group_id"]]
    assert set(merged["member_claim_ids"]) == {"a1", "a2", "b1", "b2", "c2"}
    assert merged["representative_claim_id"] == "a2" and merged["representative_manual"]
    assert merged["supporter_count"] == 3  # p1, p2 and the new p9
    assert merged["thread_count"] == 5
    assert after["c3"]["group_id"] == split_id and after["c1"]["member_claim_ids"] == ["c1"]
    # a3 does not match b1 / b2 / c2 (other subjects), so it cannot join the merged group
    assert after["a3"]["member_claim_ids"] == ["a3"]


def test_changed_claim_stays_unless_its_stance_turns_opposite(corpus: Corpus) -> None:
    corpus.claim("a1")["stance"] = "positive"
    corpus.claim("a2")["stance"] = "positive"
    run(**corpus.paths())
    old_text = corpus.claim("a2")["claim_text"]
    corpus.claim("a2")["claim_text"] = old_text + " (edited)"
    corpus.vectors[old_text + " (edited)"] = corpus.vectors[old_text]
    corpus.claim("b2")["stance"] = "negative"  # b1 is factual: not opposite, stays
    assert run(**corpus.paths())["claims_left_groups"] == 0
    groups = by_member(edited(corpus))
    assert groups["a2"]["member_claim_ids"] == ["a1", "a2"]
    assert groups["b2"]["member_claim_ids"] == ["b1", "b2"]
    corpus.claim("a2")["stance"] = "negative"
    assert run(**corpus.paths())["claims_left_groups"] == 1
    groups = by_member(edited(corpus))
    assert groups["a1"]["member_claim_ids"] == ["a1"]
    assert groups["a2"]["member_claim_ids"] == ["a2"]
    assert groups["a1"]["contradicts"] == [groups["a2"]["group_id"]]


def test_must_links_apply_once_and_map_legacy_ids(corpus: Corpus) -> None:
    corpus.legacy = {"c1": ["old-c1"]}
    corpus.must_links = [
        {"id": "agg0000", "representative": "old-c1", "claim_ids": ["a1", "old-c1"]}
    ]
    stats = run(**corpus.paths())
    assert stats["must_links_applied"] == 1
    payload = edited(corpus)
    group = by_member(payload)["c1"]
    # a2 matches a1 but not c1, so it is not in the must-linked group (complete linkage)
    assert group["member_claim_ids"] == ["a1", "c1"]
    assert group["representative_claim_id"] == "c1" and group["representative_manual"]
    # a reviewer splits c1 out again: the unchanged must-link is not re-applied
    group["member_claim_ids"].remove("c1")
    payload["groups"].append(
        {"group_id": allocate_group_id("c1", used_group_ids(payload["groups"])),
         "member_claim_ids": ["c1"]}
    )
    save_edited(corpus, payload)
    assert run(**corpus.paths())["must_links_applied"] == 0
    assert by_member(edited(corpus))["c1"]["member_claim_ids"] == ["c1"]


def test_must_link_move_empties_a_group_into_merged_from(corpus: Corpus) -> None:
    run(**corpus.paths())
    c_id = by_member(edited(corpus))["c1"]["group_id"]
    corpus.must_links = [{"id": "agg0000", "representative": "a1", "claim_ids": ["a1", "c1"]}]
    run(**corpus.paths())
    group = by_member(edited(corpus))["c1"]
    assert group["member_claim_ids"] == ["a1", "c1", "a2"]
    assert group["merged_from"] == [c_id]


def test_only_restricts_the_claims_processed(corpus: Corpus) -> None:
    run(**corpus.paths())
    corpus.add("d1", plane=3)
    corpus.add("d2", plane=4)
    stats = run(**corpus.paths(), only=["d1"])
    assert stats["new_claims"] == 1
    payload = edited(corpus)
    assert "d1" in by_member(payload) and "d2" not in by_member(payload)
    assert "d2" not in payload["metadata"]["inputs"]
    assert run(**corpus.paths())["new_claims"] == 1  # d2 is still new


def test_rebuild_regroups_from_scratch_with_a_backup(corpus: Corpus) -> None:
    run(**corpus.paths())
    payload = edited(corpus)
    group = by_member(payload)["a1"]
    group["member_claim_ids"].remove("a2")
    payload["groups"].append({"group_id": "g-manual", "member_claim_ids": ["a2"]})
    save_edited(corpus, payload)
    stats = run(**corpus.paths(), rebuild=True)
    assert stats["written"] and stats["backup"]
    assert by_member(edited(corpus))["a2"]["member_claim_ids"] == ["a1", "a2"]
    assert Path(stats["backup"]).is_file()


def test_missing_audit_is_an_error(corpus: Corpus) -> None:
    paths = corpus.paths()
    paths["audit_path"].unlink()
    with pytest.raises(FileNotFoundError, match="audit"):
        run(**paths)
