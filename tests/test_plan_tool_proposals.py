"""The plan tool's proposals (accept / reject / reopen), undo across the plan and the
assignments, and what the operation dialogs say about the assignments. Synthetic data from
``tests.test_plan_tool_assignments`` (the real step 6b with a fake classifier)."""

from __future__ import annotations

from typing import Any

import pytest

from tests.plan_fixtures import FakeLLM
from tests.test_plan_tool_assignments import (
    NEW_PAGE,
    UNPLACED,
    AssignSetup,
    add_page_op,
    ids,
    snapshot,
)
from utils.json_io import write_json_file
from utils.paths import BACKUPS_DIR


@pytest.fixture
def setup(tmp_path: Any) -> AssignSetup:
    return AssignSetup(tmp_path)


def both(setup: AssignSetup) -> list[bytes | None]:
    return snapshot(setup.edited, setup.artifact.edited, setup.original, setup.artifact.original)


# ------------------------------------------------------------------ reading


def test_proposals_come_pending_first_with_groups_and_targets(setup: AssignSetup) -> None:
    listing = setup.get("/proposals")
    assert listing["counts"] == {"pending": 3, "accepted": 0, "rejected": 0}
    page, label, scope = listing["items"]
    assert [p["kind"] for p in listing["items"]] == ["page", "label", "scope"]
    assert ids({"items": page["groups"]}) == UNPLACED and page["groups"][0]["claim_text"]
    assert page["target_id"] == "" and page["target"] == {"id_check": None, "parent_ok": True}
    assert label["target"]["id_check"]["ok"] is True and label["target"]["kind_known"] is True
    assert scope["target"] == {"page_id": "hospital", "title_he": "כותרת hospital",
                               "scope": "מה שייך לעמוד hospital ומה לא."}
    setup.post(f"/proposals/{label['id']}/reject")
    assert [p["status"] for p in setup.get("/proposals")["items"]] == [
        "pending", "pending", "rejected"]


# ------------------------------------------------------------------ deciding


def test_accepting_a_page_proposal_saves_both_files_and_the_run_places_its_groups(
        setup: AssignSetup) -> None:
    proposal = setup.proposal("page")
    answer = setup.accept(proposal["id"], add_page_op())
    assert answer["focus"] == NEW_PAGE and answer["edit"]["created"] == [NEW_PAGE]
    assert answer["label"].startswith("קבלת ההצעה „קרנות פוריות”")
    assert NEW_PAGE in setup.saved()["pages"]
    assert setup.proposal("page")["status"] == "accepted"
    records = setup.assignments()["assignments"]
    assert all(records[g]["pages"] == [] for g in UNPLACED)  # not moved by hand
    assert list(BACKUPS_DIR.glob("assignments_edited_*.json"))
    assert answer["overview"]["assignments"]["proposals"]["accepted"] == 1
    setup.run_step()  # the offered step-6b run asks the groups against the new page
    assert ids(setup.groups(scope="page", id=NEW_PAGE)) == UNPLACED
    assert setup.groups(scope="unplaced")["total"] == 0


def test_accepting_label_and_scope_proposals(setup: AssignSetup) -> None:
    label = setup.proposal("label")
    setup.accept(label["id"], {"op": "add_label", "label_id": "israel", "title_he": "ישראל",
                               "description": "תיאור", "kind": "country"})
    assert "israel" in setup.saved()["labels"]
    scope = setup.proposal("scope")
    setup.accept(scope["id"], {"op": "edit", "item_id": "hospital",
                               "changes": {"scope": "תחום רחב יותר, כפי שתוקן"}})
    assert setup.saved()["pages"]["hospital"]["scope"] == "תחום רחב יותר, כפי שתוקן"
    assert setup.get("/proposals")["counts"] == {"pending": 1, "accepted": 2, "rejected": 0}


@pytest.mark.parametrize("kind, operation, words", [
    ("page", add_page_op("agencies"), "שוברת את הכללים"),  # the proposer's id is taken
    ("page", {"op": "add_label", "label_id": "x-label", "title_he": "x", "description": "x",
              "kind": "country"}, "add_page"),
    ("scope", {"op": "edit", "item_id": "costs", "changes": {"scope": "אחר"}}, "hospital"),
    ("scope", {"op": "edit", "item_id": "hospital", "changes": {"title_he": "x"}}, "hospital"),
])
def test_a_refused_acceptance_changes_neither_file(setup: AssignSetup, kind: str,
                                                   operation: dict[str, Any], words: str) -> None:
    before = both(setup)
    detail = setup.accept(setup.proposal(kind)["id"], operation, status=409)["detail"]
    assert words in detail
    assert both(setup) == before
    assert setup.get("/plan")["undo"] is None


def test_reject_reopen_and_refusals(setup: AssignSetup) -> None:
    proposal_id = setup.proposal("scope")["id"]
    answer = setup.post(f"/proposals/{proposal_id}/reject")
    assert answer["label"].startswith("דחיית ההצעה")
    assert setup.proposal("scope")["status"] == "rejected"
    assert "כבר נדחתה" in setup.post(f"/proposals/{proposal_id}/reject", status=409)["detail"]
    setup.accept(proposal_id, {"op": "edit", "item_id": "hospital", "changes": {"scope": "x"}},
                 status=409)
    setup.post(f"/proposals/{proposal_id}/reopen")
    assert setup.proposal("scope")["status"] == "pending"
    setup.post(f"/proposals/{proposal_id}/reopen", status=409)
    setup.post("/proposals/prop-nothere/reject", status=404)
    # The step keeps the owner's decision and never proposes a rejected one again.
    setup.post(f"/proposals/{proposal_id}/reject")
    assert setup.run_step(FakeLLM([]))["proposal_calls"] == 0
    assert setup.proposal("scope")["status"] == "rejected"


# ---------------------------------------------------------------------- undo


def test_undo_puts_back_both_files_of_an_accepted_proposal(setup: AssignSetup) -> None:
    plan_before = setup.original.read_text(encoding="utf-8")
    proposal_id = setup.proposal("page")["id"]
    setup.accept(proposal_id, add_page_op())
    answer = setup.post("/undo")
    assert answer["label"].startswith("קבלת ההצעה") and answer["focus"] is None
    assert NEW_PAGE not in setup.saved()["pages"]
    assert setup.proposal("page")["status"] == "pending"
    assert setup.original.read_text(encoding="utf-8") == plan_before


def test_undo_of_moves_one_at_a_time(setup: AssignSetup) -> None:
    first = setup.assignments()["assignments"]
    setup.edit(["g-00000000"], {"action": "unplace"})
    setup.edit(["g-00000000", "g-00000004"], {"action": "set_primary", "page_id": "hospital"})
    setup.post("/undo")
    assert setup.assignments()["assignments"]["g-00000000"]["pages"] == []
    assert setup.assignments()["assignments"]["g-00000004"] == first["g-00000004"]
    setup.post("/undo")
    assert setup.assignments()["assignments"] == first
    setup.post("/undo", status=409)


def test_undo_is_refused_after_the_assignments_changed_outside(setup: AssignSetup) -> None:
    setup.apply({"op": "rename", "item_id": "costs", "title_he": "עלויות"})
    setup.edit(["g-00000000"], {"action": "unplace"})
    payload = setup.assignments()
    payload["metadata"]["note"] = "an assignment run wrote this"
    write_json_file(payload, setup.artifact.edited)
    assert setup.get("/plan")["undo"] is None
    assert "השיוך השתנו מחוץ לכלי" in setup.post("/undo", status=409)["detail"]
    setup.post("/undo", status=409)  # the older plan change is not offered either


def test_a_plan_change_cannot_be_undone_after_an_assignment_run(setup: AssignSetup) -> None:
    proposal_id = setup.proposal("page")["id"]
    setup.accept(proposal_id, add_page_op())
    setup.run_step()  # groups landed on the new page: undoing the page would orphan them
    setup.post("/undo", status=409)
    assert NEW_PAGE in setup.saved()["pages"]


# ------------------------------------------------------- dialogs and the assignments


def test_previews_say_what_the_next_assignment_run_does(setup: AssignSetup) -> None:
    merge = setup.post("/operations/preview", {"op": "merge", "survivor_id": "clinics",
                                               "merged_ids": ["agencies"]})["assignments"]
    assert merge["redirected"] == 6 and merge["reask"] == 3  # the unplaced are asked again
    setup.edit(["g-00000002"], {"action": "set_primary", "page_id": "hospital"})
    delete = setup.post("/operations/preview", {"op": "delete", "item_id": "hospital"})["assignments"]
    assert delete["pages_cleared"] == 1 and delete["manual_lost"] == 1
    assert delete["manual_reask"] == 1
    label = setup.post("/operations/preview", {"op": "add_label", "label_id": "israel",
                                               "title_he": "ישראל", "description": "d",
                                               "kind": "country"})["assignments"]
    assert label["asked_labels"] == ["israel"] and label["reask"] == 0
    scope = setup.post("/operations/preview", {"op": "edit", "item_id": "costs",
                                               "changes": {"scope": "חדש"}})["assignments"]
    assert scope["screen_pages"] == ["costs"] and scope["reask"] == 6
