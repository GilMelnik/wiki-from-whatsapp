"""The claim-groups tool, editing side: merge / split / move / representative / contradiction
links / "not the same", each persisted to the edited file only, recounted with step 5's own
functions (a real step-5 run afterwards finds nothing to change) and surviving step-5 re-runs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from step_5_aggregate.groups import allocate_group_id, used_group_ids
from tests.groups_tool_support import GroupsSetup
from tests.review_tool_support import locked_elsewhere


@pytest.fixture
def groups(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> GroupsSetup:
    return GroupsSetup(tmp_path, monkeypatch)


def assert_recount_matches(groups: GroupsSetup) -> None:
    """Step 5 re-run on unchanged inputs: the tool's derived fields are exactly what the step
    computes, so nothing is written."""
    before = groups.edited.read_bytes()
    assert not groups.run_step()["written"]
    assert groups.edited.read_bytes() == before


def suggestion_pairs(groups: GroupsSetup) -> list[tuple[str, str]]:
    items = groups.get("/suggestions", limit=50)["items"]
    return [(s["a"]["group_id"], s["b"]["group_id"]) for s in items]


def test_merge_keeps_the_older_group_records_it_and_recounts(groups: GroupsSetup) -> None:
    a, d = groups.gid("a1"), groups.gid("d1")
    original = groups.original.read_bytes()
    order = [g["group_id"] for g in groups.payload()["groups"]]
    answer = groups.post("/groups/merge", {"group_ids": [d, a]})
    assert answer["group_id"] == a and answer["merged"] == [d]
    assert answer["summary"]["group_count"] == 8
    group = answer["group"]
    assert [m["claim_id"] for m in group["members"]] == ["a1", "a2", "a3", "d1"]
    assert (group["supporter_count"], group["opposer_count"]) == (2, 1)  # p2, p4; p1 disputes
    assert group["merged_from"] == [d]
    assert groups.original.read_bytes() == original  # only the edited file is written
    assert [g["group_id"] for g in groups.payload()["groups"]] == [g for g in order if g != d]
    gone = groups.client.get(f"/api/groups/groups/{d}")
    assert gone.status_code == 404 and a in gone.json()["detail"]
    assert_recount_matches(groups)


def test_merge_needs_two_known_groups(groups: GroupsSetup) -> None:
    a = groups.gid("a1")
    assert groups.client.post("/api/groups/groups/merge", json={"group_ids": [a, a]}).status_code == 409
    assert groups.client.post("/api/groups/groups/merge", json={"group_ids": [a, "g-x"]}).status_code == 404
    assert groups.client.post("/api/groups/groups/merge", json={"group_ids": [a]}).status_code == 422


def test_split_appends_a_new_id_and_is_not_suggested_back(groups: GroupsSetup) -> None:
    a = groups.gid("a1")
    expected_id = allocate_group_id("a3", used_group_ids(groups.payload()["groups"]))
    answer = groups.post(f"/groups/{a}/split", {"claim_ids": ["a3"]})
    assert answer["group_id"] == a and answer["new_group_id"] == expected_id
    payload = groups.payload()
    assert payload["groups"][-1]["group_id"] == expected_id
    assert payload["groups"][-1]["member_claim_ids"] == ["a3"]
    assert groups.group_of("a1")["member_claim_ids"] == ["a1", "a2"]
    assert payload["not_same"][0]["claims_a"] == ["a1", "a2"]
    assert payload["not_same"][0]["claims_b"] == ["a3"] and payload["not_same"][0]["source"] == "split"
    # a3 still matches a1 / a2 at step 5's own bar, but the owner just split it off.
    assert (a, expected_id) not in suggestion_pairs(groups)
    for bad in (["a3"], ["a1", "a2"], []):
        status = 422 if not bad else 409
        assert groups.client.post(f"/api/groups/groups/{a}/split", json={"claim_ids": bad}).status_code == status
    assert_recount_matches(groups)


def test_move_and_a_source_left_empty_counts_as_merged(groups: GroupsSetup) -> None:
    a, s1, s2 = groups.gid("a1"), groups.gid("s1"), groups.gid("s2")
    answer = groups.post(f"/groups/{a}/move", {"claim_id": "a3", "target_id": s1})
    assert answer["group_id"] == a and answer["target_id"] == s1
    assert groups.group_of("a3")["group_id"] == s1
    assert groups.group_of("a1")["member_claim_ids"] == ["a1", "a2"]
    assert groups.client.post(f"/api/groups/groups/{a}/move", json={"claim_id": "a3", "target_id": s1}).status_code == 409
    emptied = groups.post(f"/groups/{s2}/move", {"claim_id": "s2", "target_id": s1})
    assert emptied["group_id"] is None and emptied["group"]["merged_from"] == [s2]
    assert_recount_matches(groups)


def test_representative_is_manual_until_cleared(groups: GroupsSetup) -> None:
    a = groups.gid("a1")
    auto = groups.payload()["groups"][0]["representative_claim_id"]
    other = next(c for c in ("a1", "a2", "a3") if c != auto)
    group = groups.post(f"/groups/{a}/representative", {"claim_id": other})["group"]
    assert group["representative_claim_id"] == other and group["representative_manual"]
    assert group["claim_text"] == next(m["claim_text"] for m in group["members"] if m["claim_id"] == other)
    stored = groups.group_of("a1")
    assert stored["representative_claim_id"] == other and stored["representative_manual"] is True
    assert groups.client.post(f"/api/groups/groups/{a}/representative", json={"claim_id": "d1"}).status_code == 409
    assert_recount_matches(groups)
    cleared = groups.post(f"/groups/{a}/representative", {"claim_id": None})["group"]
    assert cleared["representative_claim_id"] == auto and not cleared["representative_manual"]


def test_contradiction_links_can_be_removed_and_added(groups: GroupsSetup) -> None:
    o1, o2, c1 = groups.gid("o1"), groups.gid("o2"), groups.gid("c1")
    removed = groups.post(f"/groups/{o1}/contradictions", {"group_id": o2, "linked": False})["group"]
    assert removed["contradicts"] == [] and [r["group_id"] for r in removed["contradicts_rejected"]] == [o2]
    assert groups.group_of("o2")["contradicts"] == [] and groups.group_of("o2")["contradicts_rejected"] == [o1]
    added = groups.post(f"/groups/{c1}/contradictions", {"group_id": o2, "linked": True})["group"]
    assert [(link["group_id"], link["manual"]) for link in added["contradicts"]] == [(o2, True)]
    assert groups.group_of("o2")["contradicts"] == [c1]
    assert_recount_matches(groups)
    # A link restored by hand: the rejection is lifted, the automatic link is back.
    restored = groups.post(f"/groups/{o1}/contradictions", {"group_id": o2, "linked": True})["group"]
    assert [link["group_id"] for link in restored["contradicts"]] == [o2]
    assert "contradicts_rejected" not in groups.group_of("o1")
    # A manual link to a group that is merged away follows it into the survivor.
    groups.post("/groups/merge", {"group_ids": [groups.gid("d1"), o2]})
    assert groups.group_of("c1")["contradicts"] == [groups.gid("d1")]
    assert groups.group_of("c1")["contradicts_manual"] == [o2]
    assert_recount_matches(groups)


def test_not_same_hides_the_pair_survives_and_can_be_undone(groups: GroupsSetup) -> None:
    s1, s2, t1, t2 = (groups.gid(c) for c in ("s1", "s2", "t1", "t2"))
    assert suggestion_pairs(groups) == [(s1, s2), (t1, t2)]
    answer = groups.post("/not-same", {"group_ids": [s1, s2]})
    record = answer["record"]
    assert (record["claims_a"], record["claims_b"], record["source"]) == (["s1"], ["s2"], "suggestion")
    assert answer["summary"]["suggestions"]["total"] == 1 and answer["summary"]["not_same_count"] == 1
    assert suggestion_pairs(groups) == [(t1, t2)]
    groups.client = groups.new_client()  # a restarted server remembers it
    assert suggestion_pairs(groups) == [(t1, t2)]
    # A merge of s1 with another group does not bring the rejected claim pair back.
    groups.post("/groups/merge", {"group_ids": [groups.gid("c1"), s1]})
    assert suggestion_pairs(groups) == [(t1, t2)]
    groups.delete(f"/not-same/{record['id']}", status=200)
    assert groups.client.delete(f"/api/groups/not-same/{record['id']}").status_code == 404
    assert "not_same" not in groups.payload()


def test_merging_a_suggestion_removes_it(groups: GroupsSetup) -> None:
    t1, t2 = groups.gid("t1"), groups.gid("t2")
    groups.post("/groups/merge", {"group_ids": [t1, t2]})
    pairs = suggestion_pairs(groups)
    assert (t1, t2) not in pairs and all(t2 not in pair for pair in pairs)


def test_every_edit_survives_a_step5_rerun_with_new_claims(groups: GroupsSetup) -> None:
    a, d, s1, s2, t1, t2, o1, o2 = (
        groups.gid(c) for c in ("a1", "d1", "s1", "s2", "t1", "t2", "o1", "o2")
    )
    groups.post("/groups/merge", {"group_ids": [a, d]})
    new_id = groups.post(f"/groups/{a}/split", {"claim_ids": ["a3"]})["new_group_id"]
    groups.post(f"/groups/{s2}/move", {"claim_id": "s2", "target_id": t1})
    groups.post(f"/groups/{a}/representative", {"claim_id": "a2"})
    groups.post(f"/groups/{o1}/contradictions", {"group_id": o2, "linked": False})
    groups.post("/not-same", {"group_ids": [s1, t2]})
    groups.post(f"/groups/{groups.gid('c1')}/contradictions", {"group_id": t2, "linked": True})
    edited: dict[str, Any] = groups.payload()
    # New claims arrive: one close to a1 / a2 / a3 (complete linkage keeps it out of the group
    # holding the merged-in d1; it joins the split-off a3) and one apart from everything.
    groups.corpus.add("a4", angle=-0.05, entities=["Alpha"], date="2024-09-01")
    groups.corpus.add("e1", plane=6, date="2024-09-02")
    stats = groups.run_step()
    assert stats["written"] and stats["new_claims"] == 2
    after = groups.payload()
    assert groups.group_of("a1")["group_id"] == a
    assert groups.group_of("a1")["member_claim_ids"] == ["a1", "a2", "d1"]
    assert groups.group_of("a4")["group_id"] == new_id
    assert groups.group_of("a1")["merged_from"] == [d]
    assert groups.group_of("a1")["representative_claim_id"] == "a2"
    assert groups.group_of("s2")["group_id"] == t1
    assert groups.group_of("o1")["contradicts"] == [] and groups.group_of("o1")["contradicts_rejected"] == [o2]
    assert groups.group_of("c1")["contradicts"] == [t2]
    assert after["not_same"] == edited["not_same"]
    assert groups.payload(groups.original)["groups"] == after["groups"]
    # The open tool follows the re-run.
    assert groups.get(f"/groups/{new_id}")["group"]["size"] == 2
    assert groups.get(f"/groups/{groups.gid('e1')}")["group"]["size"] == 1


def test_saving_while_step5_holds_the_lock_is_423(groups: GroupsSetup) -> None:
    a, d = groups.gid("a1"), groups.gid("d1")
    before = groups.edited.read_bytes()
    with locked_elsewhere(groups.edited):
        response = groups.client.post("/api/groups/groups/merge", json={"group_ids": [a, d]})
    assert response.status_code == 423
    assert groups.edited.read_bytes() == before
    groups.post("/groups/merge", {"group_ids": [a, d]})  # works once the lock is gone


def test_an_external_rewrite_is_picked_up_before_the_next_edit(groups: GroupsSetup) -> None:
    a, d, c = groups.gid("a1"), groups.gid("d1"), groups.gid("c1")
    groups.get("/summary")  # the store is loaded
    payload = groups.payload()
    payload["groups"] = [g for g in payload["groups"] if g["group_id"] != c]
    groups.edited.write_text(json.dumps(payload), encoding="utf-8")
    groups.post("/groups/merge", {"group_ids": [a, d]})
    assert c not in {g["group_id"] for g in groups.payload()["groups"]}
