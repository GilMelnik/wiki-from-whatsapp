"""The Withheld page (``review_tool.tools.withheld``): what the same-complaint check released,
what stays withheld and why, and the owner's "keep withheld" marks. Synthetic claims, people
and answers; no model."""

from __future__ import annotations

import json
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
    def __init__(self, root: Path) -> None:
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
        return {key_of[row["short_id"]]: row["why"] for row in self.get("/withheld", **params)["items"]}


@pytest.fixture
def page(tmp_path: Path) -> Setup:
    return Setup(tmp_path)


def test_before_the_check_ran_every_lone_complaint_waits_or_is_alone(page: Setup) -> None:
    summary = page.get("/summary")
    assert summary["counts"] == {"withheld": 6, "released": 0, "still_withheld": 6, "kept": 0,
                                 "released_groups": 0}
    assert summary["lists"] == {"answered": 0, "waiting": 2, "unusable": 0}
    assert summary["min_supporters"] == 2 and summary["answered_at"] is None
    assert page.why() == {"a": "waiting", "b": "waiting", "c": "waiting", "e": "alone",
                          "f": "waiting", "g": "waiting"}
    assert page.get("/released") == {"entities": []}


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


def test_no_identity_or_message_id_is_served(page: Setup) -> None:
    page.answer("e0001", ["a", "b"])
    served = json.dumps([page.get("/summary"), page.get("/released"), page.get("/withheld")])
    assert "SECRET" not in served and "claim_id" not in served and "thread_id" not in served


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


def test_a_stale_or_unusable_answer_releases_nothing(page: Setup) -> None:
    page.answer("e0001", ["a", "b"], fingerprint="0" * 40)
    page.answer("e0003", ["f", "g"], problem="the model's answer did not parse")
    assert page.get("/summary")["lists"] == {"answered": 0, "waiting": 1, "unusable": 1}
    assert page.why()["a"] == "waiting" and page.why()["f"] == "unusable"
    assert page.get("/released") == {"entities": []}


def test_search_finds_text_and_entity_names_in_any_case(page: Setup) -> None:
    assert set(page.why(q="LAWYER")) == {"f", "g"}
    assert set(page.why(q="הפיקדון")) == {"a"}
    assert set(page.why(q="agency deposit")) == {"b"}
    assert page.get("/withheld", q="nothing like this")["total"] == 0
