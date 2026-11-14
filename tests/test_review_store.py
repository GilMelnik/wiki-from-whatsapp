"""The suggestions store: status moves, corrections, applying, and what stays untouched."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from step_6_plan.operations import delete

from review_tool.tools.suggestions.apply import ApplyRefused
from step_8_review.inputs import REVIEW_CONFIG
from review_tool.tools.suggestions.store import SuggestionConflict, SuggestionNotFound
from tests.pages_fixtures import FakeWriter
from tests.review_fixtures import FakeReviewer, ReviewWorkspace, valid_answers
from utils.paths import BACKUPS_DIR


@pytest.fixture()
def ws(tmp_path: Path) -> ReviewWorkspace:
    workspace = ReviewWorkspace(tmp_path)
    workspace.review(FakeReviewer(valid_answers()))
    return workspace


def _ids(ws: ReviewWorkspace) -> dict[str, str]:
    return {s["kind"]: s["id"] for s in ws.suggestions_payload()["suggestions"]}


def test_list_shows_names_counts_and_the_note(ws: ReviewWorkspace) -> None:
    listing = ws.store().list()
    assert listing["counts"]["status"] == {"pending": 6} and listing["note"] == "הערה הערה הערה"
    first = listing["items"][0]
    assert first["kind_name"] == REVIEW_CONFIG["kind_names"][first["kind"]]
    assert first["status_name"] == REVIEW_CONFIG["status_names"]["pending"]


def test_status_moves_go_to_the_edited_file_with_a_backup(ws: ReviewWorkspace) -> None:
    store, ids = ws.store(), _ids(ws)
    original = ws.suggestions.original.read_bytes()
    assert store.accept(ids["merge"])["status"] == "accepted"
    assert store.reopen(ids["merge"])["status"] == "pending"
    assert store.reject(ids["merge"])["status"] == "rejected"
    assert store.reopen(ids["merge"])["status"] == "pending"
    assert store.accept(ids["split"])["status"] == "accepted"
    assert store.reject(ids["split"])["status"] == "rejected"
    edited = {s["id"]: s for s in ws.suggestions_payload(edited=True)["suggestions"]}
    assert edited[ids["merge"]]["status"] == "pending" and edited[ids["split"]]["status"] == "rejected"
    assert ws.suggestions.original.read_bytes() == original
    assert any(BACKUPS_DIR.glob("suggestions_edited_*.json"))


@pytest.mark.parametrize("first,then", [("reject", "accept"), ("accept", "accept")])
def test_a_move_from_the_wrong_status_is_refused(ws: ReviewWorkspace, first: str, then: str) -> None:
    store, sid = ws.store(), _ids(ws)["merge"]
    getattr(store, first)(sid)
    with pytest.raises(SuggestionConflict, match=sid):
        getattr(store, then)(sid)


def test_an_unknown_id_is_not_found(ws: ReviewWorkspace) -> None:
    with pytest.raises(SuggestionNotFound):
        ws.store().accept("sug-00000000")


def test_an_edit_is_checked_kept_and_removed_when_equal(ws: ReviewWorkspace) -> None:
    store, sid = ws.store(), _ids(ws)["add_label"]
    proposal = next(s for s in ws.suggestions_payload()["suggestions"] if s["id"] == sid)["proposal"]
    with pytest.raises(SuggestionConflict) as bad:
        store.edit(sid, {**proposal, "label_id": "usa"})
    assert bad.value.details
    assert "edited_proposal" not in store.get(sid)
    assert store.edit(sid, {**proposal, "label_id": "lone-star"})["edited_proposal"]["label_id"] == "lone-star"
    assert "edited_proposal" not in store.edit(sid, proposal)


def test_apply_needs_an_accepted_suggestion_and_marks_it_applied(ws: ReviewWorkspace) -> None:
    store, sid = ws.store(), _ids(ws)["add_label"]
    with pytest.raises(SuggestionConflict):
        store.apply(sid)
    store.accept(sid)
    done = store.apply(sid)
    assert done["suggestion"]["status"] == "applied" and done["suggestion"]["applied_at"]
    assert done["result"]["message"] == REVIEW_CONFIG["messages"]["plan_applied"]
    with pytest.raises(SuggestionConflict):
        store.reopen(sid)
    with pytest.raises(SuggestionConflict):
        store.edit(sid, done["suggestion"]["proposal"])


def test_a_refused_apply_leaves_every_file_byte_identical(ws: ReviewWorkspace) -> None:
    store, sid = ws.store(FakeWriter("fake-apply")), _ids(ws)["merge"]
    store.accept(sid)
    ws.plan_artifact.edited.write_text(json.dumps(delete(ws.plan(), "costs").plan),
                                       encoding="utf-8")
    before = ws.snapshot()
    with pytest.raises(ApplyRefused):
        store.apply(sid)
    assert ws.snapshot() == before
