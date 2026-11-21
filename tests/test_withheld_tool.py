"""The Withheld page (``review_tool.tools.withheld``): what the same-complaint check released,
what stays withheld and why, and the owner's marks ("keep withheld", "released" by hand).
Synthetic claims, people and answers; no model."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from review_tool.artifacts import ArtifactNode
from review_tool.tools.withheld.tool import WithheldTool
from step_5_same_complaint.claim_records import load_claim_records
from step_5_same_complaint.release import entity_lists, list_fingerprint
from tests.review_tool_support import BASE_URL, build_app, build_graph, locked_elsewhere
from tests.same_complaint_support import World
from utils.json_io import read_json_file, write_json_file
from utils.paths import BACKUPS_DIR

DEPOSIT = "הסוכנות לא החזירה את הפיקדון"  # synthetic Hebrew text


class Setup:
    def __init__(self, root: Path,
                 more: Mapping[str, tuple[Sequence[str], Sequence[int]]] | None = None) -> None:
        self.world = World(root)
        w = self.world
        self.ids = {
            "a": w.add(["e0001"], [1], text=DEPOSIT),
            "b": w.add(["e0001"], [2], text="The deposit never came back."),
            "c": w.add(["e0001"], [3], text="Very slow answers."),
            "d": w.add(["e0001"], [1, 4], text="The deposit was kept, twice."),
            "e": w.add(["e0002"], [5], text="The clinic is far away."),
            "f": w.add(["e0003"], [6], text="The lawyer billed too much."),
            "g": w.add(["e0003"], [7], text="The lawyer missed a deadline."),
        }
        for key, (entities, people) in (more or {}).items():
            self.ids[key] = w.add(entities, people)
        self.sources = w.write()
        self.answers = root / "same_complaint.json"
        self.vetoes = root / "vetoes.json"
        graph = build_graph(root, optional_reads=True)
        graph.add(ArtifactNode("same_complaint", "Same-complaint answers", self.answers))
        graph.add(ArtifactNode("withheld_vetoes", "Kept withheld", self.vetoes))
        tool = WithheldTool(graph, audit=self.sources.audit, lock_timeout=0.2)
        self.client = TestClient(build_app(root, specs=(tool.spec,), graph=graph), base_url=BASE_URL)

    def answer(self, entity_id: str, *groups: list[str], fingerprint: str | None = None,
               problem: str | None = None) -> None:
        stored = read_json_file(self.answers)["entities"] if self.answers.exists() else {}
        claims = entity_lists(load_claim_records(self.sources))[entity_id]
        stored[entity_id] = {
            "fingerprint": fingerprint or list_fingerprint(claims), "problem": problem,
            "groups": [{"label": "did not return the deposit", "claims": [self.ids[k] for k in g]}
                       for g in groups],
            "answered_at": "2026-11-19T00:00:00+00:00"}
        write_json_file({"entities": stored, "metadata": {}}, self.answers)

    def get(self, path: str, **params: Any) -> Any:
        response = self.client.get(f"/api/withheld{path}", params=params)
        assert response.status_code == 200, response.text
        return response.json()

    def keep(self, key: str, keep: bool, status: int = 200) -> Any:
        short_id = self.ids.get(key, key)
        response = self.client.post(f"/api/withheld/claims/{short_id}/keep", json={"keep": keep})
        assert response.status_code == status, response.text
        return response.json()

    def why(self, **params: Any) -> dict[str, str]:
        key_of = {short_id: key for key, short_id in self.ids.items()}
        return {key_of[row["short_id"]]: row["why"] for row in self.get("/withheld", **params)["items"]
                if row["withheld"]}

    def release(self, key: str, release: bool, status: int = 200) -> Any:
        short_id = self.ids.get(key, key)
        response = self.client.post(f"/api/withheld/claims/{short_id}/release",
                                    json={"release": release})
        assert response.status_code == status, response.text
        return response.json()

    def marks(self) -> tuple[list[str], list[str]]:
        """The stored marks, as keys: (kept withheld, released by hand)."""

        key_of = {short_id: key for key, short_id in self.ids.items()}
        stored = read_json_file(self.vetoes)
        return ([key_of[s] for s in stored["keep_withheld"]],
                [key_of[s] for s in stored["released"]])


@pytest.fixture
def page(tmp_path: Path) -> Setup:
    return Setup(tmp_path)


def test_before_the_check_ran_every_lone_complaint_waits_or_is_alone(page: Setup) -> None:
    summary = page.get("/summary")
    assert summary["counts"] == {"withheld": 6, "released": 0, "released_by_you": 0,
                                 "still_withheld": 6, "kept": 0, "released_groups": 0}
    assert summary["lists"] == {"answered": 0, "waiting": 2, "unusable": 0}
    assert summary["min_supporters"] == 2 and summary["answered_at"] is None
    assert page.why() == {"a": "waiting", "b": "waiting", "c": "waiting", "e": "alone",
                          "f": "waiting", "g": "waiting"}
    assert page.get("/released") == {"entities": [], "by_you": []}


def test_the_released_view_shows_each_group_with_its_claims(page: Setup) -> None:
    page.answer("e0001", ["a", "b", "d"])
    page.answer("e0003")
    entities = page.get("/released")["entities"]
    assert [e["entity_name"] for e in entities] == ["Agency One"]
    (group,) = entities[0]["groups"]
    assert entities[0]["released"] == 2
    assert (group["label"], group["people"], group["people_unmarked"]) == (
        "did not return the deposit", 3, 3)
    assert [(c["text"], c["status"], c["supporter_count"], c["month"]) for c in group["claims"]] == [
        (DEPOSIT, "released", 1, "2024-03"),
        ("The deposit never came back.", "released", 1, "2024-03"),
        ("The deposit was kept, twice.", "published", 2, "2024-03"),
    ]
    assert page.why() == {"c": "unmatched", "e": "alone", "f": "unmatched", "g": "unmatched"}
    assert page.get("/summary")["counts"]["released"] == 2


def test_no_identity_or_claim_id_is_served(page: Setup) -> None:
    page.answer("e0001", ["a", "b"])
    page.release("c", True)
    served = json.dumps([page.get("/summary"), page.get("/released"), page.get("/withheld")])
    assert "SECRET-person" not in served and "m-SECRET" not in served and "claim_id" not in served
    # the conversation's id only opens it on this local site (the Conversations page)
    records = load_claim_records(page.sources)
    assert not any(r["claim_id"] in served for r in records)
    threads = {r["short_id"]: r["thread_id"] for r in records}
    assert all(row["thread_id"] == threads[row["short_id"]]
               for row in page.get("/withheld")["items"])


def test_keep_withheld_is_a_switch_and_counts_people_without_the_kept_claim(page: Setup) -> None:
    page.answer("e0001", ["a", "b"])
    summary = page.keep("a", True)["summary"]
    assert summary["counts"]["released"] == 0 and summary["counts"]["kept"] == 1
    (group,) = page.get("/released")["entities"][0]["groups"]
    assert [c["status"] for c in group["claims"]] == ["kept", "short"]
    assert (group["people"], group["people_unmarked"]) == (1, 2)
    assert page.why()["a"] == "kept" and page.why()["b"] == "short"
    assert read_json_file(page.vetoes)["keep_withheld"] == [page.ids["a"]]
    assert page.keep("a", True)["summary"]["counts"]["kept"] == 1  # no change
    assert page.keep("a", False)["summary"]["counts"]["released"] == 2
    assert read_json_file(page.vetoes)["keep_withheld"] == []
    assert list(BACKUPS_DIR.glob("vetoes_*.json"))


def test_a_mark_needs_a_withheld_claim_and_a_free_file(page: Setup) -> None:
    assert "nothing to keep" in page.keep("d", True, status=409)["detail"]
    assert "No claim" in page.keep("g-00000000", True, status=404)["detail"]
    with locked_elsewhere(page.vetoes):
        page.keep("a", True, status=423)
    assert not page.vetoes.exists()


def test_the_same_complaint_twice_from_one_person_stays_withheld_and_says_so(page: Setup) -> None:
    # "a" (person 1) and "d" (people 1 and 4) share person 1; "f" and "g" are two people
    page.answer("e0001", ["a", "d"])
    page.answer("e0003", ["f", "g"])
    assert "f" not in page.why() and "g" not in page.why()
    world = Setup(page.world.root / "twice", more={"h": (["e0002"], [5])})
    world.answer("e0002", ["e", "h"])  # person 5 made the complaint twice
    assert world.why()["e"] == "same_person" and world.why()["h"] == "same_person"
    assert world.get("/summary")["counts"]["released"] == 0


def test_a_stale_or_unusable_answer_releases_nothing(page: Setup) -> None:
    page.answer("e0001", ["a", "b"], fingerprint="0" * 40)
    page.answer("e0003", ["f", "g"], problem="the model's answer did not parse")
    assert page.get("/summary")["lists"] == {"answered": 0, "waiting": 1, "unusable": 1}
    assert page.why()["a"] == "waiting" and page.why()["f"] == "unusable"
    assert page.get("/released") == {"entities": [], "by_you": []}


def test_search_finds_text_and_entity_names_in_any_case(page: Setup) -> None:
    assert set(page.why(q="LAWYER")) == {"f", "g"}
    assert set(page.why(q="הפיקדון")) == {"a"}
    assert set(page.why(q="agency deposit")) == {"b"}
    assert page.get("/withheld", q="nothing like this")["total"] == 0


def test_a_withheld_claim_says_where_it_is_filed_and_which_messages_it_rests_on(
        tmp_path: Path) -> None:
    page = Setup(tmp_path, more={"h": (["e0001", "e0002"], [8])})
    claim_id = {r["short_id"]: r["claim_id"] for r in load_claim_records(page.sources)}
    stored = read_json_file(page.sources.claims)
    for claim in stored["claims"]:
        if claim["claim_id"] == claim_id[page.ids["h"]]:
            claim["supporting_message_ids"] = ["m2", "m5"]
    write_json_file(stored, page.sources.claims)
    rows = {row["short_id"]: row for row in page.get("/withheld")["items"]}
    # five claims name the agency and two the clinic: the claim is filed under the rarer name
    h = rows[page.ids["h"]]
    assert (h["filed_under"], h["filed_under_claims"], h["message_ids"]) == ("Clinic Two", 2, ["m2", "m5"])
    a = rows[page.ids["a"]]
    assert (a["filed_under"], a["filed_under_claims"], a["message_ids"]) == ("Agency One", 5, [])
    # the Released view's claims open their conversation at their messages too
    page.release("h", True)
    assert page.get("/released")["by_you"][0]["message_ids"] == ["m2", "m5"]


def test_a_withheld_claim_counts_the_published_claims_of_its_message(tmp_path: Path) -> None:
    page = Setup(tmp_path)
    claim_id = {r["short_id"]: r["claim_id"] for r in load_claim_records(page.sources)}
    # "c" and "d" were written from message m5; "c" and "e" from message m7. Two people back
    # "d", so the rule never withheld it; "c" and "e" are lone complaints
    messages = {page.ids["c"]: ["m5", "m7"], page.ids["d"]: ["m5"], page.ids["e"]: ["m7"]}
    stored = read_json_file(page.sources.claims)
    for claim in stored["claims"]:
        for short_id, ids in messages.items():
            if claim["claim_id"] == claim_id[short_id]:
                claim["supporting_message_ids"] = ids
    write_json_file(stored, page.sources.claims)

    def siblings() -> dict[str, int]:
        key_of = {short_id: key for key, short_id in page.ids.items()}
        return {key_of[row["short_id"]]: row["published_siblings"]
                for row in page.get("/withheld")["items"]}

    assert siblings() == {"a": 0, "b": 0, "c": 1, "e": 0, "f": 0, "g": 0}
    page.release("c", True)  # now a published claim rests on the message "e" rests on
    assert siblings()["e"] == 1


# ------------------------------------------------------------------ released by the owner's hand


def test_a_hand_release_publishes_a_lone_claim_and_can_be_taken_back(page: Setup) -> None:
    summary = page.release("e", True)["summary"]  # before the check ever ran
    assert summary["counts"] == {"withheld": 6, "released": 0, "released_by_you": 1,
                                 "still_withheld": 5, "kept": 0, "released_groups": 0}
    assert page.marks() == ([], ["e"])
    assert "e" not in page.why()
    row = next(r for r in page.get("/withheld")["items"] if r["short_id"] == page.ids["e"])
    # it keeps its place in the list, marked, with the entities it names
    assert (row["withheld"], row["why"], row["hand_released"], row["entity_names"]) == (
        False, None, True, ["Clinic Two"])
    assert page.get("/withheld")["total"] == 5
    (claim,) = page.get("/released")["by_you"]
    assert (claim["short_id"], claim["status"]) == (page.ids["e"], "by_you")
    assert page.release("e", True)["summary"]["counts"]["released_by_you"] == 1  # no change
    summary = page.release("e", False)["summary"]
    assert (summary["counts"]["released_by_you"], summary["counts"]["still_withheld"]) == (0, 6)
    assert page.marks() == ([], []) and page.why()["e"] == "alone"
    assert page.get("/released")["by_you"] == []


def test_the_two_marks_exclude_each_other(page: Setup) -> None:
    page.release("c", True)
    page.keep("c", True)  # keeping it withheld takes the hand release away
    assert page.marks() == (["c"], [])
    assert page.why()["c"] == "kept"
    page.release("c", True)  # the check does not release it: released by hand instead
    assert page.marks() == ([], ["c"])
    counts = page.get("/summary")["counts"]
    assert (counts["kept"], counts["released_by_you"]) == (0, 1)


def test_releasing_a_kept_claim_the_check_releases_only_takes_the_mark_away(page: Setup) -> None:
    page.answer("e0001", ["a", "b"])
    page.keep("a", True)
    assert {"a", "b"} <= set(page.why())
    summary = page.release("a", True)["summary"]
    assert page.marks() == ([], [])
    assert (summary["counts"]["released"], summary["counts"]["released_by_you"]) == (2, 0)
    # withholding it again needs the "keep withheld" mark back: the check would release it
    page.release("a", False)
    assert page.marks() == (["a"], [])
    assert page.why()["a"] == "kept" and page.why()["b"] == "short"


def test_a_hand_release_in_a_released_group_shows_as_yours(page: Setup) -> None:
    page.answer("e0001", ["a", "b"])
    page.keep("a", True)
    page.release("b", True)
    (group,) = page.get("/released")["entities"][0]["groups"]
    assert [c["status"] for c in group["claims"]] == ["kept", "by_you"]
    assert set(page.why()) == {"a", "c", "e", "f", "g"}


def test_a_hand_release_needs_a_claim_the_rule_withholds(page: Setup) -> None:
    assert "nothing to release" in page.release("d", True, status=409)["detail"]
    assert "No claim" in page.release("g-00000000", True, status=404)["detail"]
    with locked_elsewhere(page.vetoes):
        page.release("a", True, status=423)
    assert not page.vetoes.exists()
