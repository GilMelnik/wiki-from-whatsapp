"""The claim-groups tool: both sides of a dispute, the merge check, the groups withheld from the
wiki and the page a group would be on (synthetic C6 via a real step-5 run, temp dirs only).

Added to ``groups_tool_support``'s corpus: d1 and c1 dispute each other in a conversation (C3
``counters``); n1 / n2 are lone negative reports naming Gamma, close enough to be suggested
(o2 is one too: negative, names Gamma, no supporter).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from review_tool.artifacts import ArtifactNode
from step_7_write_pages.withhold import withheld_ids
from tests.aggregate_fixtures import angle_for
from tests.groups_tool_support import GroupsSetup
from utils.json_io import write_json_file


@pytest.fixture
def groups(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> GroupsSetup:
    setup = GroupsSetup(tmp_path, monkeypatch, run_step=False)
    corpus = setup.corpus
    corpus.link("d1", "c1")
    corpus.add("n1", plane=6, stance="negative", entities=["Gamma"], date="2024-08-01",
               supporters=[("p5", "2024-08-01")])
    corpus.add("n2", plane=6, angle=angle_for(0.93), stance="negative", entities=["Gamma"],
               date="2024-09-01", supporters=[("p6", "2024-09-01")])
    setup.use_cache(corpus.embed)
    setup.run_step()
    setup.client = setup.new_client()
    return setup


def link_to(group: dict, other_id: str) -> dict:
    return next(link for link in group["contradicts"] if link["group_id"] == other_id)


def test_a_dispute_shows_both_sides_from_either_group(groups: GroupsSetup) -> None:
    d1, c1 = groups.gid("d1"), groups.gid("c1")
    from_d = link_to(groups.get(f"/groups/{d1}")["group"], c1)
    from_c = link_to(groups.get(f"/groups/{c1}")["group"], d1)
    assert from_d["origin"] == from_c["origin"] == "conversation"
    assert from_d["claim_text"] == groups.group_of("c1")["claim_text"]
    assert from_c["claim_text"] == groups.group_of("d1")["claim_text"]
    assert from_d["supporter_count"] == 0 and from_c["supporter_count"] == 1  # p4 backs d1
    assert (from_d["first_date"], from_c["first_date"]) == ("2024-04-01", "2024-03-01")
    [pair] = from_d["pairs"]
    assert (pair["ours"]["claim_id"], pair["theirs"]["claim_id"]) == ("d1", "c1")
    assert pair["theirs"]["claim_text"] and pair["ours"]["first_date"] == "2024-03-01"
    [back] = from_c["pairs"]
    assert (back["ours"]["claim_id"], back["theirs"]["claim_id"]) == ("c1", "d1")


def test_links_say_where_they_come_from(groups: GroupsSetup) -> None:
    o1, o2, t1 = groups.gid("o1"), groups.gid("o2"), groups.gid("t1")
    wording = link_to(groups.get(f"/groups/{o1}")["group"], o2)
    assert (wording["origin"], wording["pairs"], wording["manual"]) == ("wording", [], False)
    added = groups.post(f"/groups/{o1}/contradictions", {"group_id": t1, "linked": True})["group"]
    assert link_to(added, t1)["origin"] == "manual" and link_to(added, t1)["manual"]


def test_a_missing_other_side_is_shown_as_missing(groups: GroupsSetup) -> None:
    payload = groups.payload()
    o1 = next(g for g in payload["groups"] if "o1" in g["member_claim_ids"])
    o1["contradicts"] = [*o1["contradicts"], "g-gone"]
    write_json_file(payload, groups.edited)
    links = groups.get(f"/groups/{o1['group_id']}")["group"]["contradicts"]
    assert links[-1] == {"group_id": "g-gone", "missing": True, "manual": False}


def test_merging_disputing_groups_warns_but_stays_possible(groups: GroupsSetup) -> None:
    d1, c1 = groups.gid("d1"), groups.gid("c1")
    check = groups.client.get("/api/groups/merge-check", params={"group_ids": [c1, d1]}).json()
    assert check["survivor_id"] == d1  # d1 is the older group
    [dispute] = check["disputes"]
    assert (dispute["from_id"], dispute["group_id"], dispute["origin"]) == (d1, c1, "conversation")
    assert dispute["linked"] is True
    calm = groups.client.get("/api/groups/merge-check", params={"group_ids": [groups.gid("s1"), groups.gid("s2")]})
    assert calm.json()["disputes"] == []
    # The owner removed the link: the claims still dispute each other, so the warning stays.
    groups.post(f"/groups/{d1}/contradictions", {"group_id": c1, "linked": False})
    check = groups.client.get("/api/groups/merge-check", params={"group_ids": [d1, c1]}).json()
    assert [d["linked"] for d in check["disputes"]] == [False]
    merged = groups.post("/groups/merge", {"group_ids": [d1, c1]})
    assert merged["group_id"] == d1 and merged["group"]["contradicts"] == []
    assert groups.client.get("/api/groups/merge-check", params={"group_ids": [d1]}).status_code == 422


def test_a_suggested_pair_carries_its_dispute_and_both_sides_links(groups: GroupsSetup) -> None:
    s1, s2, o1 = groups.gid("s1"), groups.gid("s2"), groups.gid("o1")
    groups.post(f"/groups/{s1}/contradictions", {"group_id": s2, "linked": True})
    groups.post(f"/groups/{s2}/contradictions", {"group_id": o1, "linked": True})
    pair = next(i for i in groups.get("/suggestions", limit=10)["items"] if i["a"]["group_id"] == s1)
    assert pair["dispute"]["group_id"] == s2 and pair["dispute"]["origin"] == "manual"
    assert [link["group_id"] for link in pair["b"]["contradicts"]] == [s1, o1]
    assert pair["shared_entity_names"] == ["Beta"]
    other = next(i for i in groups.get("/suggestions", limit=10)["items"] if i["a"]["group_id"] == groups.gid("t1"))
    assert other["dispute"] is None and other["shared_entity_names"] == []


def test_the_withheld_list_is_the_rule_computed_live(groups: GroupsSetup) -> None:
    expected = withheld_ids(groups.payload()["groups"])
    assert expected == {groups.gid("o2"), groups.gid("n1"), groups.gid("n2")}
    listed = groups.get("/groups", limit=50, withheld=True)["items"]
    assert {row["group_id"] for row in listed} == expected
    assert all(row["withheld"] and row["entity_names"] == ["Gamma"] for row in listed)
    every = groups.get("/groups", limit=50)["items"]
    assert {row["group_id"] for row in every if row["withheld"]} == expected
    summary = groups.get("/summary")
    assert summary["withheld_count"] == 3 and summary["withhold_min_supporters"] == 2
    o1 = groups.get(f"/groups/{groups.gid('o1')}")["group"]
    assert link_to(o1, groups.gid("o2"))["withheld"] is True


def test_merging_two_lone_reports_releases_them_at_two_supporters(groups: GroupsSetup) -> None:
    n1, n2, o2 = groups.gid("n1"), groups.gid("n2"), groups.gid("o2")
    pair = next(i for i in groups.get("/suggestions", limit=10)["items"] if i["a"]["group_id"] == n1)
    assert pair["a"]["withheld"] and pair["b"]["withheld"]
    check = groups.client.get("/api/groups/merge-check", params={"group_ids": [n1, n2]}).json()
    assert (check["withheld"], check["withheld_after"], check["supporter_count_after"]) == ([n1, n2], False, 2)
    still = groups.client.get("/api/groups/merge-check", params={"group_ids": [o2, n1]}).json()
    assert (still["withheld_after"], still["supporter_count_after"]) == (True, 1)
    groups.post("/groups/merge", {"group_ids": [n1, n2]})
    assert n1 not in {row["group_id"] for row in groups.get("/groups", limit=50, withheld=True)["items"]}
    assert groups.get("/summary")["withheld_count"] == 1


def test_a_group_names_the_page_it_is_or_would_be_on(groups: GroupsSetup) -> None:
    root, o2, d1 = groups.root, groups.gid("o2"), groups.gid("d1")
    assert groups.get(f"/groups/{o2}")["group"]["page"] is None  # no plan yet
    plan = {"pages": {"clinics": {"title_he": "מרפאות", "merged_from": ["old-clinics"]}},
            "labels": {}}
    write_json_file(plan, root / "plan.json")
    records = {o2: {"pages": ["old-clinics"]}, d1: {"pages": []}}
    write_json_file({"assignments": records}, root / "assignments.json")
    graph = groups.graph
    graph.add(ArtifactNode("plan", "Plan", root / "plan.json", root / "plan_edited.json", "s6", ("groups",)))
    graph.add(ArtifactNode("assignments", "Assignments", root / "assignments.json",
                           root / "assignments_edited.json", "s6b", ("groups", "plan")))
    groups.client = groups.new_client()
    assert groups.get(f"/groups/{o2}")["group"]["page"] == {"page_id": "clinics", "title_he": "מרפאות"}
    assert groups.get(f"/groups/{d1}")["group"]["page"] is None
    (root / "assignments.json").write_text("not json", encoding="utf-8")  # unreadable: no pages
    assert groups.get(f"/groups/{o2}")["group"]["page"] is None
    records[o2] = {"pages": ["clinics"]}
    (root / "assignments.json").write_text(json.dumps({"assignments": records}), encoding="utf-8")
    assert groups.get(f"/groups/{o2}")["group"]["page"]["page_id"] == "clinics"


def test_a_group_can_be_found_by_its_id(groups: GroupsSetup) -> None:
    gid = groups.gid("o2")
    assert [row["group_id"] for row in groups.get("/groups", limit=50, q=gid)["items"]] == [gid]
