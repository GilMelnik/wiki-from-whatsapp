"""The suggestions tool: what it shows (list, evidence), the owner's decisions and corrections,
and the error mapping. Applying is in ``test_suggestions_tool_apply``. Synthetic data only."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import pytest

from review_tool.tools import TOOL_SPECS
from step_10_update.estimate import ESTIMATE_CONFIG
from step_8_review.inputs import REVIEW_CONFIG
from step_8_review.proposals import plain_text
from tests.review_fixtures import FakeReviewer, flat, sentence_a
from tests.suggestions_tool_support import ToolSetup
from utils.config import LLM_CONFIG


@pytest.fixture()
def setup(tmp_path: Path) -> ToolSetup:
    return ToolSetup(tmp_path)


def test_the_tool_is_registered_with_its_files() -> None:
    spec = next(spec for spec in TOOL_SPECS if spec.name == "suggestions")
    assert spec.reads == ("plan", "assignments", "pages", "groups")
    assert spec.writes == ("suggestions", "plan", "pages", "groups")


def test_before_step_8_has_run_the_tool_says_so(tmp_path: Path) -> None:
    setup = ToolSetup(tmp_path, review=False)
    overview = setup.get("/suggestions")
    assert overview["exists"] is False and overview["items"] == []
    assert overview["rerun"]["due"] == overview["rerun"]["passes"] == 3
    assert overview["rerun"]["usd"] > 0 and overview["rerun"]["batch"] is True
    setup.decide("accept", "sug-00000000", status=409)  # no file: the frame's 409


def test_the_list_carries_names_marks_counts_paid_and_the_run(setup: ToolSetup) -> None:
    overview = setup.get("/suggestions")
    assert overview["exists"] is True and overview["note"] == "הערה הערה הערה"
    assert overview["counts"]["status"] == {"pending": 6}
    kinds = {item["kind"]: item for item in overview["items"]}
    assert set(kinds) == {"mistake", "contradiction", "split", "merge", "add_page", "add_label"}
    assert {kind for kind, item in kinds.items() if item["paid"]} == {"mistake", "contradiction"}
    assert kinds["merge"]["kind_name"] == REVIEW_CONFIG["kind_names"]["merge"]
    assert all(item["busy"] is False and item["page_marks"] == {} for item in overview["items"])
    assert overview["run"]["model"] == "fake-opus" and overview["run"]["input_tokens"]
    assert overview["applies"] == {"running": None, "queue": [], "outcomes": []}


def test_the_rerun_estimate_counts_only_the_due_passes(
        setup: ToolSetup, monkeypatch: pytest.MonkeyPatch) -> None:
    # The review ran with the fake model (for the configured one every pass would be due).
    model = LLM_CONFIG["stages"][REVIEW_CONFIG["stage"]]["model"]
    prices = ESTIMATE_CONFIG["usd_per_million_tokens"]
    monkeypatch.setitem(prices, "fake-opus", prices[model])
    monkeypatch.setitem(LLM_CONFIG["stages"][REVIEW_CONFIG["stage"]], "model", "fake-opus")
    assert setup.get("/suggestions")["rerun"] == {"due": 0, "passes": 3, "usd": 0.0,
                                                  "batch": True}
    pages = setup.ws.pages()
    pages["pages"]["agencies"]["body_md"] += "\n\nפסקה חדשה."
    setup.ws.write_edited(pages)
    rerun = setup.get("/suggestions")["rerun"]
    assert rerun["due"] == 1 and 0 < rerun["usd"] < 1


def test_a_page_changed_since_the_review_is_marked(setup: ToolSetup) -> None:
    pages = setup.ws.pages()
    pages["pages"]["agencies"]["body_md"] += "\n\nפסקה חדשה."
    setup.ws.write_edited(pages)
    marks = {item["kind"]: item["mark_names"] for item in setup.get("/suggestions")["items"]}
    assert marks["mistake"] == {"agencies": REVIEW_CONFIG["page_marks"]["changed"]}


def test_a_mistake_shows_its_sentence_in_its_paragraph_and_its_groups(setup: ToolSetup) -> None:
    detail = setup.get(f"/suggestions/{setup.ids()['mistake']}")
    evidence = detail["evidence"]
    assert evidence["page"]["page_id"] == "agencies" and evidence["page"]["exists"]
    assert evidence["page"]["groups"]["main"] > 0 and evidence["page"]["words"] > 0
    quote = evidence["quote"]
    assert quote["text"][quote["start"]:quote["end"]] == plain_text(sentence_a("g-00000004"))
    group = evidence["groups"][0]
    assert group["group_id"] == "g-00000004" and group["claim_text"]
    assert {"supporters", "opposers", "first_date", "last_date", "stance"} <= set(group)
    assert group["said_in"][0]["page_id"] == "agencies"
    assert "all_supporters" not in group and "entity_names" not in group
    assert detail["changes"] is None and detail["suggestion"]["paid"] is True


def test_a_contradiction_shows_both_sides_and_where_each_is_said(setup: ToolSetup) -> None:
    evidence = setup.get(f"/suggestions/{setup.ids()['contradiction']}")["evidence"]
    assert [g["group_id"] for g in evidence["sides"]["a"]] == ["g-00000000"]
    assert [g["group_id"] for g in evidence["sides"]["b"]] == ["g-00000002"]
    assert evidence["sides"]["b"][0]["said_in"] and evidence["sides"]["a"][0]["claim_text"]
    assert [page["page_id"] for page in evidence["pages"]] == ["agencies", "clinics"]


def test_structural_suggestions_show_the_pages_involved(setup: ToolSetup) -> None:
    ids = setup.ids()
    split = setup.get(f"/suggestions/{ids['split']}")["evidence"]
    assert [p["page_id"] for p in split["pages"]] == ["clinics"] and split["pages"][0]["scope"]
    merge = setup.get(f"/suggestions/{ids['merge']}")["evidence"]
    assert [p["page_id"] for p in merge["pages"]] == ["agencies", "costs"]
    add_page = setup.get(f"/suggestions/{ids['add_page']}")["evidence"]
    assert add_page["parent"]["id"] == "choosing"
    assert [p["page_id"] for p in add_page["parent"]["pages"]] == ["agencies", "costs"]
    add_label = setup.get(f"/suggestions/{ids['add_label']}")["evidence"]
    assert "usa" in [label["label_id"] for label in add_label["labels"]]
    setup.get("/suggestions/sug-00000000", status=404)


def test_accept_reject_reopen_one_and_many(setup: ToolSetup) -> None:
    ids = setup.ids()
    assert setup.decide("accept", ids["merge"]) == {"done": [ids["merge"]], "refused": {}}
    assert setup.item(ids["merge"])["status"] == "accepted"
    setup.decide("reject", ids["merge"])
    setup.decide("reopen", ids["merge"])
    assert setup.item(ids["merge"])["status"] == "pending"
    many = setup.decide("accept", ids["merge"], ids["split"], ids["add_label"])
    assert many["done"] == [ids["merge"], ids["split"], ids["add_label"]]
    # A multi-selection with one that cannot move: the others move, that one is named.
    mixed = setup.decide("accept", ids["merge"], ids["add_page"])
    assert mixed["done"] == [ids["add_page"]] and ids["merge"] in mixed["refused"][ids["merge"]]
    # The step's original keeps what the review raised.
    assert all(s["status"] == "pending" for s in setup.ws.suggestions_payload()["suggestions"])


def test_refused_moves_and_unknown_ids(setup: ToolSetup) -> None:
    ids = setup.ids()
    detail = setup.decide("reopen", ids["merge"], status=409)["detail"]
    assert ids["merge"] in detail and REVIEW_CONFIG["actions"]["reopen"] in detail
    assert setup.decide("accept", ids["merge"], "sug-00000000", status=404)["detail"]
    setup.decide("accept", status=422)
    setup.post("/suggestions/decide", {"ids": [ids["merge"]], "action": "apply"}, 422)
    assert setup.item(ids["merge"])["status"] == "pending"


@pytest.mark.parametrize("kind, change", [
    ("split", {"new_page_id": "clinic-costs", "scope": "תחום מתוקן"}),
    ("merge", {"survivor_id": "costs", "merged_ids": ["agencies"]}),
    ("add_page", {"page_id": "trips", "parent_id": "medical"}),
    ("add_label", {"label_id": "lone-star"}),
    ("mistake", {"correction": "לתקן אחרת"}),
    ("contradiction", {"correction": "להציג אחרת"}),
])
def test_each_proposal_shape_can_be_corrected(setup: ToolSetup, kind: str,
                                              change: dict[str, Any]) -> None:
    sid = setup.ids()[kind]
    proposal = setup.item(sid)["proposal"]
    edited = setup.post(f"/suggestions/{sid}/edit", {"proposal": {**proposal, **change}})
    assert edited["edited_proposal"] == {**proposal, **change}
    setup.decide("accept", sid)
    # Back to the raised proposal: the correction is dropped (allowed while accepted).
    assert "edited_proposal" not in setup.post(f"/suggestions/{sid}/edit", {"proposal": proposal})


@pytest.mark.parametrize("kind, change, words", [
    ("add_label", {"label_id": "usa"}, "usa"),            # a taken id
    ("split", {"page_id": "nowhere"}, "nowhere"),
    ("mistake", {"sentence": "משפט שאינו בדף"}, "agencies"),
])
def test_a_refused_correction_is_shown_and_writes_nothing(setup: ToolSetup, kind: str,
                                                          change: dict[str, Any], words: str) -> None:
    sid = setup.ids()[kind]
    proposal = setup.item(sid)["proposal"]
    detail = setup.post(f"/suggestions/{sid}/edit", {"proposal": {**proposal, **change}},
                        status=409)["detail"]
    assert detail.startswith(REVIEW_CONFIG["messages"]["bad_proposal"].split("{")[0]) and words in detail
    assert not setup.ws.suggestions.edited.exists()
    setup.post("/suggestions/sug-00000000/edit", {"proposal": proposal}, status=404)


def test_a_step_8_run_outside_the_tool_is_picked_up(setup: ToolSetup) -> None:
    ids = setup.ids()
    setup.decide("reject", ids["merge"])
    # A rebuild keeps the owner's decided suggestions and drops the undecided ones.
    setup.ws.review(FakeReviewer([flat("add_label", label_id="nevada", title_he="נבדה",
                                       description="תיאור", label_kind="country")]), rebuild=True)
    items = setup.get("/suggestions")["items"]
    assert [item["id"] for item in items][0] == ids["merge"] and len(items) == 2
    assert items[1]["proposal"]["label_id"] == "nevada"
    assert setup.item(ids["merge"])["status"] == "rejected"


def test_concurrent_decisions_never_lose_each_other(setup: ToolSetup) -> None:
    ids = list(setup.ids().values())
    threads = [threading.Thread(target=setup.ws.store().accept, args=(sid,)) for sid in ids]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    edited = json.loads(setup.ws.suggestions.edited.read_text(encoding="utf-8"))
    assert {s["status"] for s in edited["suggestions"]} == {"accepted"}
