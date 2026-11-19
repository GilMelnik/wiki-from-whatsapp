"""The plan tool's assignment views, claim moves, proposals and undo across both files.

The assignments are made by the real step 6b (``step_6_plan.assign.run``) with a fake
classifier over the synthetic plan of ``tests.plan_fixtures`` (pages agencies / costs /
clinics / hospital; claim ``i`` is about topic ``i % 4``): topic 0 -> agencies, topic 1 ->
costs + agencies (two pages), topic 2 -> clinics (unsure), topic 3 -> no page. Nothing here
comes from real data.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from step_6_plan.assign import run
from step_6_plan.coverage import NONE
from step_6_plan.operation_requests import quote
from tests.assign_fixtures import FakeClassifier, topic
from tests.claim_fixtures import claim_record, claim_records, write_claim_inputs
from tests.plan_fixtures import FakeLLM, short_id_of
from tests.review_tool_support import locked_elsewhere
from tests.test_plan_tool import PlanSetup
from utils.json_io import write_json_file
from utils.paths import BACKUPS_DIR, Artifact

NEW_PAGE = "fertility-funds"
UNPLACED = [short_id_of(3), short_id_of(7), short_id_of(11)]
TWO_PAGES = [short_id_of(1), short_id_of(5), short_id_of(9)]


def pages_by_topic(state: str, options: Sequence[str]) -> dict[str, float]:
    probs = dict.fromkeys(options, 0.0)
    kind = topic(state)
    if kind == 0:
        probs["agencies"] = 0.9
    elif kind == 1:
        probs.update(costs=0.5, agencies=0.4)
    elif kind == 2:
        probs.update(clinics=0.6, hospital=0.2)
    elif NEW_PAGE in probs:  # topic 3 belongs on the page a proposal adds
        probs[NEW_PAGE] = 0.9
    else:
        probs.update({NONE: 0.8, "hospital": 0.15})
    return probs


def _proposal(kind: str, title: str, **fields: Any) -> dict[str, Any]:
    base = {"kind": kind, "target_id": "", "parent_id": "", "label_kind": "", "title_he": title,
            "description": f"תיאור {title}", "scope": f"תחום {title}", "reason": "סיבה",
            "claims": UNPLACED}
    return {**base, **fields}


PROPOSALS = {"proposals": [
    _proposal("page", "קרנות פוריות", target_id="agencies", parent_id="medical"),  # id taken
    _proposal("label", "ישראל", target_id="israel", label_kind="country"),
    _proposal("scope", "גם בתי חולים פרטיים", target_id="hospital", scope="תחום רחב יותר"),
]}


class AssignSetup(PlanSetup):
    """The plan tool over claims, a plan and assignments the real step 6b wrote."""

    def __init__(self, root: Path, *, with_assignments: bool = True) -> None:
        super().__init__(root)
        node = self.graph.node("assignments")
        self.artifact = Artifact(node.original, node.edited)
        if with_assignments:
            result = self.run_step(FakeLLM([PROPOSALS]))
            assert result["proposal_calls"] == 1

    def run_step(self, llm: FakeLLM | None = None) -> dict[str, Any]:
        plan = self.edited if self.edited.is_file() else self.original
        return run(sources=self.sources, plan_path=plan,
                   artifact=self.artifact, classifier=FakeClassifier(page=pages_by_topic),
                   llm=llm or FakeLLM([]))

    def assignments(self) -> dict[str, Any]:
        return json.loads(self.artifact.edited.read_text(encoding="utf-8"))

    def claims(self, status: int = 200, **params: Any) -> Any:
        return self.get("/assignments/claims", status, **params)

    def edit(self, claims: list[str], *changes: dict[str, Any], status: int = 200) -> Any:
        return self.post("/assignments/edit", {"claims": claims, "changes": list(changes)},
                         status)

    def proposal(self, kind: str) -> dict[str, Any]:
        return next(p for p in self.get("/proposals")["items"] if p["kind"] == kind)

    def accept(self, proposal_id: str, operation: dict[str, Any], status: int = 200) -> Any:
        return self.post(f"/proposals/{proposal_id}/accept", {"operation": operation}, status)


@pytest.fixture
def setup(tmp_path: Path) -> AssignSetup:
    return AssignSetup(tmp_path)


def ids(listing: dict[str, Any]) -> list[str]:
    return [row["short_id"] for row in listing["items"]]


def add_page_op(page_id: str = NEW_PAGE) -> dict[str, Any]:
    return {"op": "add_page", "page_id": page_id, "parent_id": "medical", "title_he": "קרנות",
            "description": "תיאור", "scope": "תחום הדף החדש"}


def snapshot(*paths: Path) -> list[bytes | None]:
    return [path.read_bytes() if path.is_file() else None for path in paths]


# ------------------------------------------------------------- without step 6b


def test_without_assignments_the_plan_tool_works_as_before(tmp_path: Path) -> None:
    setup = AssignSetup(tmp_path, with_assignments=False)
    overview = setup.get("/plan")
    assert overview["assignments"]["exists"] is False and overview["budget"]["pages"] == 4
    for path in ("/assignments/claims?scope=unplaced", "/proposals"):
        response = setup.client.get(f"/api/plan{path}")
        assert response.status_code == 409 and "the assignment step" in response.json()["detail"]
    assert setup.post("/operations/preview", {"op": "delete", "item_id": "hospital"})["assignments"] is None
    setup.apply({"op": "rename", "item_id": "costs", "title_he": "עלויות"})
    assert setup.post("/undo")["label"].startswith("Renamed")
    setup.run_step(FakeLLM([PROPOSALS]))  # step 6b ran: picked up without a restart
    assert setup.get("/plan")["assignments"]["counts"]["unplaced"] == 3


# ------------------------------------------------------------------- reading


def test_overview_counts_pages_labels_and_findings(setup: AssignSetup) -> None:
    summary = setup.get("/plan")["assignments"]
    assert summary["exists"] and summary["problem"] is None
    assert summary["counts"] == {"claims": 12, "unplaced": 3, "two_pages": 3, "manual": 0,
                                 "waiting": 0, "new_claims": 0, "followed": 0, "gone": 0}
    assert summary["pages"]["agencies"] == {"all": 6, "primary": 3, "manual": 0}
    assert summary["pages"]["hospital"] == {"all": 0, "primary": 0, "manual": 0}
    assert summary["labels"] == {"usa": 6, "georgia": 0}
    assert [(f["label_id"], f["finding"]) for f in summary["label_findings"]] == [
        ("usa", "large"), ("georgia", "few")]
    assert summary["proposals"] == {"pending": 3, "accepted": 0, "rejected": 0}
    assert summary["thresholds"] == {"page": 0.5, "label": 0.7}  # the step's own rules


def test_claims_newer_than_the_plan_are_counted(setup: AssignSetup) -> None:
    plan = json.loads(setup.original.read_text(encoding="utf-8"))
    plan["metadata"]["planned_claims"] = [short_id_of(0), short_id_of(1)]
    write_json_file(plan, setup.original)
    assert setup.get("/plan")["newer_claims"] == {"count": 10, "assigned": 10}


def test_a_page_lists_its_own_claims_least_certain_first_then_mentions(setup: AssignSetup) -> None:
    listing = setup.claims(scope="page", id="agencies")
    assert listing["counts"] == {"all": 6, "primary": 3, "mention": 3}
    rows = listing["items"]
    assert [row["role"] for row in rows] == ["primary"] * 3 + ["mention"] * 3
    assert [row["certainty"] for row in rows] == [0.9, 0.9, 0.9, 0.4, 0.4, 0.4]
    mention = rows[3]
    assert mention["pages"] == ["costs", "agencies"] and mention["claim_text"].startswith("Synthetic")
    assert mention["p"] == [["costs", 0.5], ["agencies", 0.4]] and mention["manual"] is False
    assert ids(setup.claims(scope="page", id="agencies", role="mention")) == TWO_PAGES
    assert ids(setup.claims(scope="page", id="agencies", q="NUMBER 5")) == [short_id_of(5)]
    paged = setup.claims(scope="page", id="agencies", limit=2, offset=2)
    assert (paged["total"], ids(paged)) == (6, [short_id_of(8), short_id_of(1)])
    by_date = setup.claims(scope="page", id="agencies", sort="date")
    assert ids(by_date)[:3] == [short_id_of(4), short_id_of(8), short_id_of(0)]
    setup.claims(status=404, scope="page", id="nope")
    setup.claims(status=422, scope="everything")


def test_labels_unplaced_and_waiting_lists(setup: AssignSetup) -> None:
    setup.edit([short_id_of(0)], {"action": "add_label", "label_id": "georgia"})  # pinned: last
    assert ids(setup.claims(scope="label", id="usa")) == [
        short_id_of(2), short_id_of(4), short_id_of(6), short_id_of(8), short_id_of(10), short_id_of(0)]
    assert ids(setup.claims(scope="label", id="georgia")) == [short_id_of(0)]
    unplaced = setup.claims(scope="unplaced")
    assert ids(unplaced) == UNPLACED and unplaced["items"][0]["certainty"] == 0.15
    assert setup.claims(scope="waiting")["total"] == 0
    write_claim_inputs([*claim_records(), claim_record(12)], setup.root)  # newer than 6b's run
    waiting = setup.claims(scope="waiting")
    assert ids(waiting) == [short_id_of(12)] and waiting["items"][0]["record"] is False
    setup.edit([short_id_of(12)], {"action": "unplace"}, status=409)


# ------------------------------------------------------------------- moving


def test_moving_one_claim_pins_it_in_the_edited_file_only(setup: AssignSetup) -> None:
    original = setup.artifact.original.read_bytes()
    before = setup.assignments()["assignments"][short_id_of(0)]
    answer = setup.edit([short_id_of(0)], {"action": "set_primary", "page_id": "clinics"})
    assert answer["changed"] == 1 and quote("כותרת clinics") in answer["label"]
    after = setup.assignments()["assignments"][short_id_of(0)]
    assert after == {**before, "pages": ["clinics"], "manual": True}
    assert setup.artifact.original.read_bytes() == original
    assert list(BACKUPS_DIR.glob("assignments_edited_*.json"))
    assert answer["overview"]["assignments"]["counts"]["manual"] == 1
    assert answer["overview"]["undo"]["label"] == answer["label"]
    listing = setup.claims(scope="page", id="clinics")
    assert listing["items"][-1]["short_id"] == short_id_of(0)  # pinned: last in "least certain"
    assert listing["items"][-1]["manual"] is True


def test_bulk_moves_second_pages_labels_and_unpin(setup: AssignSetup) -> None:
    records = lambda: setup.assignments()["assignments"]  # noqa: E731
    setup.edit(TWO_PAGES, {"action": "set_primary", "page_id": "agencies"})  # swap
    assert {tuple(records()[g]["pages"]) for g in TWO_PAGES} == {("agencies", "costs")}
    setup.edit(TWO_PAGES, {"action": "add_page", "page_id": "hospital"})  # replaces the second
    assert records()[short_id_of(1)]["pages"] == ["agencies", "hospital"]
    setup.edit(TWO_PAGES, {"action": "remove_page", "page_id": "agencies"})
    assert records()[short_id_of(5)]["pages"] == ["hospital"]
    setup.edit(UNPLACED, {"action": "add_page", "page_id": "hospital"})  # none yet: primary
    assert records()[short_id_of(3)]["pages"] == ["hospital"]
    setup.edit([short_id_of(0), short_id_of(3)], {"action": "unplace"},
               {"action": "add_label", "label_id": "georgia"})
    assert records()[short_id_of(0)]["pages"] == [] and records()[short_id_of(0)]["labels"] == [
        "usa", "georgia"]
    setup.edit([short_id_of(0)], {"action": "remove_label", "label_id": "usa"})
    assert records()[short_id_of(0)]["labels"] == ["georgia"]
    assert setup.claims(scope="unplaced")["manual"] == 2
    answer = setup.edit([short_id_of(0), short_id_of(1)], {"action": "unpin"})
    assert answer["changed"] == 2
    unpinned = records()[short_id_of(0)]
    assert "manual" not in unpinned
    assert (unpinned["pages"], unpinned["labels"]) == (["agencies"], ["usa"])  # the classifier's
    assert records()[short_id_of(1)]["pages"] == ["costs", "agencies"]


def test_a_move_never_touches_probabilities_or_metadata(setup: AssignSetup) -> None:
    before = setup.assignments()
    setup.edit(list(before["assignments"]), {"action": "set_primary", "page_id": "hospital"},
               {"action": "add_label", "label_id": "georgia"})
    after = setup.assignments()
    assert after["metadata"] == before["metadata"] and after["proposals"] == before["proposals"]
    for gid, record in after["assignments"].items():
        assert (record["p"], record["label_p"]) == (before["assignments"][gid]["p"],
                                                    before["assignments"][gid]["label_p"])
        assert record["pages"][0] == "hospital" and record["manual"] is True


def test_a_move_that_changes_nothing_is_not_saved(setup: AssignSetup) -> None:
    before = setup.artifact.edited.read_bytes()
    answer = setup.edit([short_id_of(0)], {"action": "add_label", "label_id": "usa"})
    assert answer["changed"] == 0 and setup.artifact.edited.read_bytes() == before
    assert answer["overview"]["undo"] is None


@pytest.mark.parametrize("change, status", [
    ({"action": "set_primary", "page_id": "nope"}, 409),
    ({"action": "add_label", "label_id": "nope"}, 409),
    ({"action": "teleport"}, 422),
])
def test_bad_moves_are_refused(setup: AssignSetup, change: dict[str, Any], status: int) -> None:
    before = setup.artifact.edited.read_bytes()
    setup.edit([short_id_of(0)], change, status=status)
    setup.edit(["g-unknown"], {"action": "unplace"}, status=409)
    setup.post("/assignments/edit", {"claims": [], "changes": [{"action": "unplace"}]}, 422)
    assert setup.artifact.edited.read_bytes() == before


def test_ids_missing_from_the_plan_are_followed_or_listed_as_waiting(setup: AssignSetup) -> None:
    setup.apply({"op": "merge", "survivor_id": "clinics", "merged_ids": ["agencies"]})
    agencies_claims = [short_id_of(0), short_id_of(4), short_id_of(8)]
    listing = setup.claims(scope="page", id="clinics")
    assert set(agencies_claims) <= set(ids(listing))
    row = next(r for r in listing["items"] if r["short_id"] == short_id_of(0))
    assert row["pages"] == ["clinics"] and row["redirected"] == {"agencies": "clinics"}
    assert setup.get("/plan")["assignments"]["counts"]["followed"] == 6
    setup.apply({"op": "delete", "item_id": "costs"})
    assert setup.claims(scope="waiting")["total"] == 0  # the two-page claims keep clinics
    clinics = setup.claims(scope="page", id="clinics")
    assert clinics["counts"] == {"all": 9, "primary": 9, "mention": 0}
    row = next(r for r in clinics["items"] if r["short_id"] == short_id_of(1))
    assert row["dropped"] == ["costs"] and row["redirected"] == {"agencies": "clinics"}
    setup.apply({"op": "delete", "item_id": "clinics"})
    assert set(ids(setup.claims(scope="waiting"))) == {
        *agencies_claims, *TWO_PAGES, short_id_of(2), short_id_of(6), short_id_of(10)}
    setup.edit([short_id_of(0)], {"action": "add_page", "page_id": "hospital"})
    record = setup.assignments()["assignments"][short_id_of(0)]
    assert record["pages"] == ["hospital"] and record["manual"] is True  # dead ids dropped


def test_an_outside_rewrite_of_the_assignments_is_picked_up(setup: AssignSetup) -> None:
    payload = setup.assignments()
    payload["assignments"][short_id_of(3)]["pages"] = ["hospital"]
    write_json_file(payload, setup.artifact.edited)
    assert setup.claims(scope="unplaced")["total"] == 2
    assert setup.get("/plan")["assignments"]["pages"]["hospital"]["all"] == 1


def test_locked_assignments_are_423(setup: AssignSetup) -> None:
    with locked_elsewhere(setup.artifact.edited):
        response = setup.client.post("/api/plan/assignments/edit", json={
            "claims": [short_id_of(0)], "changes": [{"action": "unplace"}]})
        assert response.status_code == 423
        proposal_id = setup.proposal("label")["id"]
        assert setup.client.post(f"/api/plan/proposals/{proposal_id}/reject").status_code == 423
        assert setup.claims(scope="unplaced")["total"] == 3  # reads still work


def test_an_unreadable_assignments_file_is_reported(setup: AssignSetup) -> None:
    write_json_file({"assignments": {short_id_of(0): {"pages": "agencies"}}}, setup.artifact.edited)
    summary = setup.get("/plan")["assignments"]
    assert summary["exists"] and "is not a valid assignments file" in summary["problem"]
    setup.claims(status=409, scope="unplaced")
