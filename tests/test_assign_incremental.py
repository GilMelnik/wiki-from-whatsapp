"""Step 6b re-runs (C14): only what changed is asked, manual records survive, plan edits are
followed (scope change, new page, merge, delete, labels), and ``apply_plan_edit``."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from step_6_plan import operations
from step_6_plan.assign_rules import apply_plan_edit
from step_6_plan.coverage import NONE
from tests.assign_fixtures import FakeClassifier, Workspace, by_topic, topic
from tests.plan_fixtures import PAGE_IDS, FakeLLM, make_groups
from utils.json_io import write_json_file
from utils.paths import BACKUPS_DIR


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    workspace = Workspace(tmp_path)
    workspace.run()
    return workspace


def _set_manual(ws: Workspace, group_id: str, **record: Any) -> None:
    payload = ws.edited()
    payload["assignments"][group_id].update(record, manual=True)
    ws.write_edited(payload)


def test_second_run_asks_nothing_and_writes_nothing(ws: Workspace) -> None:
    before = ws.artifact.edited.stat().st_mtime_ns
    clf = FakeClassifier()
    result = ws.run(clf)
    assert clf.items == [] and result["classifier_requests"] == 0
    assert result["written"] is False and result["proposal_calls"] == 0
    assert ws.artifact.edited.stat().st_mtime_ns == before


def test_a_plan_edit_that_moves_nothing_still_refreshes_the_file(ws: Workspace) -> None:
    before = ws.edited()["assignments"]
    ws.write_plan(operations.edit(ws.plan(), "costs", {"aliases": {"he": ["מחיר"], "en": []}}).plan)
    clf = FakeClassifier()
    result = ws.run(clf)
    assert clf.items == [] and result["written"] is True  # newer than the plan: not stale
    assert ws.edited()["assignments"] == before
    assert ws.run(FakeClassifier())["written"] is False


def test_only_new_and_changed_groups_are_asked(ws: Workspace) -> None:
    groups = make_groups(13)
    groups[2]["claim_text"] = "Synthetic claim number 2 about topic 3, reworded."
    del groups[5]
    ws.write_groups(groups)
    clf = FakeClassifier()
    result = ws.run(clf)
    assert sorted(s.split("\n")[0] for s, _ in clf.items) == [
        "Synthetic claim number 12 about topic 0.",
        "Synthetic claim number 2 about topic 3, reworded.",
    ]
    assignments = ws.edited()["assignments"]
    assert "g-00000005" not in assignments and result["removed"] == 1
    assert assignments["g-00000002"]["pages"] == ["hospital"]
    assert assignments["g-0000000c"]["pages"] == ["agencies"]
    assert list(assignments) == [g["group_id"] for g in groups]


def test_a_manual_record_is_never_re_decided(ws: Workspace) -> None:
    _set_manual(ws, "g-00000001", pages=["clinics", "agencies"], labels=["georgia"])
    groups = make_groups()
    groups[1]["claim_text"] = "Synthetic claim number 1 about topic 0, changed."
    ws.write_groups(groups)
    plan = ws.plan()
    ws.write_plan(operations.edit(plan, "clinics", {"scope": "תחום חדש לגמרי."}).plan)
    clf = FakeClassifier(screen=lambda state, page_id: 0.9)
    ws.run(clf)
    record = ws.edited()["assignments"]["g-00000001"]
    assert (record["pages"], record["labels"], record["manual"]) == (
        ["clinics", "agencies"], ["georgia"], True)
    assert not any("number 1 " in state for state, _ in clf.items)
    assert ws.run(FakeClassifier())["written"] is False


def test_un_pinning_a_manual_record_asks_it_again(ws: Workspace) -> None:
    _set_manual(ws, "g-00000001", pages=["clinics"], labels=[])
    pinned = ws.edited()
    write_json_file(pinned, ws.artifact.original)  # as a run that saw the pin writes it
    del pinned["assignments"]["g-00000001"]["manual"]
    ws.write_edited(pinned)
    assert ws.run()["full"] == 1
    assert ws.edited()["assignments"]["g-00000001"]["pages"] == ["costs"]


def test_a_scope_edit_reasks_only_what_it_affects(ws: Workspace) -> None:
    plan = ws.plan()
    ws.write_plan(operations.edit(plan, "costs", {"scope": "רק עלויות, גם של מרפאות."}).plan)

    def screen(state: str, page_id: str) -> float:
        return 0.9 if "number 2 " in state else 0.0  # one clinics group may now fit costs

    def page(state: str, options: Any) -> dict[str, float]:
        if "number 2 " in state:
            return {**{o: 0.0 for o in options}, "costs": 0.9}
        return by_topic(state, options)

    clf = FakeClassifier(page=page, screen=screen)
    result = ws.run(clf)
    reasked = sorted(int(s.split("number ")[1].split(" ")[0]) for s in clf.asked("page"))
    assert reasked == [1, 2, 5, 9]  # the costs groups + the one the screen let in
    assert len(clf.asked("screen")) == 12 - 3  # every other automatic group, once
    assert clf.asked("label:usa") == []  # labels did not change
    assert result["plan_changes"]["rescoped_pages"] == ["costs"]
    assert ws.edited()["assignments"]["g-00000002"]["pages"] == ["costs"]
    assert ws.run(FakeClassifier())["written"] is False


def test_a_new_page_is_screened_for_and_unplaced_groups_are_asked_again(tmp_path: Path) -> None:
    ws = Workspace(tmp_path)

    def nowhere_for_3(state: str, options: Any) -> dict[str, float]:
        if topic(state) == 3 and "tax" not in options:
            return {o: (0.8 if o == NONE else 0.0) for o in options}
        if topic(state) == 3:
            return {**{o: 0.0 for o in options}, "tax": 0.9}
        return by_topic(state, options)

    ws.run(FakeClassifier(page=nowhere_for_3), FakeLLM([{"proposals": []}]))
    assert ws.edited()["metadata"]["summary"]["unplaced"] == 3
    ws.write_plan(operations.add_page(ws.plan(), "tax", parent_id="choosing", title_he="מס",
                                      description="מס", scope="מיסים.").plan)
    clf = FakeClassifier(page=nowhere_for_3)
    result = ws.run(clf)
    assert result["plan_changes"]["created_pages"] == ["tax"]
    assert len(clf.asked("page")) == 3 and len(clf.asked("screen")) == 9
    assert ws.edited()["metadata"]["summary"]["page_sizes"]["tax"]["all"] == 3


def test_a_merge_is_followed_without_asking(ws: Workspace) -> None:
    _set_manual(ws, "g-00000006", pages=["clinics"], labels=["usa"])
    edit = operations.merge(ws.plan(), "hospital", ["clinics"])
    ws.write_plan(edit.plan)
    clf = FakeClassifier()
    result = ws.run(clf)
    assert clf.asked("page") == [] and clf.asked("screen") == []  # nothing was unplaced
    assignments = ws.edited()["assignments"]
    for gid in ("g-00000002", "g-0000000a"):  # topic 2 = clinics
        assert assignments[gid]["pages"] == ["hospital"]
        assert assignments[gid]["p"] == {"hospital": 0.9}
    assert assignments["g-00000006"] == {"pages": ["hospital"], "labels": ["usa"],
                                         "p": {"hospital": 0.9}, "label_p": {"usa": 0.95},
                                         "manual": True}
    assert result["plan_changes"]["removed_pages"] == ["clinics"]


def test_a_deleted_page_clears_and_reasks(ws: Workspace) -> None:
    _set_manual(ws, "g-00000003", pages=["hospital"], labels=[])
    _set_manual(ws, "g-00000007", pages=["agencies", "hospital"], labels=[])
    ws.write_plan(operations.delete(ws.plan(), "hospital").plan)
    clf = FakeClassifier()
    result = ws.run(clf)
    asked = sorted(int(s.split("number ")[1].split(" ")[0]) for s in clf.asked("page"))
    assert asked == [3, 11]  # the automatic hospital group + the manual one left without a page
    assignments = ws.edited()["assignments"]
    assert assignments["g-00000007"]["pages"] == ["agencies"] and assignments["g-00000007"]["manual"]
    assert "manual" not in assignments["g-00000003"] and assignments["g-00000003"]["pages"] == []
    assert result["cleared_manual"] == {"g-00000003": ["hospital"], "g-00000007": ["hospital"]}
    assert all("hospital" not in r["pages"] and "hospital" not in r["p"] for r in assignments.values())


def test_labels_new_ones_asked_alone_deleted_ones_dropped(ws: Workspace) -> None:
    plan = operations.add_label(ws.plan(), "canada", title_he="קנדה", description="קנדה.",
                                kind="country").plan
    ws.write_plan(operations.delete(plan, "usa").plan)
    clf = FakeClassifier(label=lambda state, lid: 0.9 if lid == "canada" else 0.0)
    ws.run(clf)
    assert len(clf.items) == 12
    assert all([q.id for q in questions] == ["label:canada"] for _, questions in clf.items)
    for record in ws.edited()["assignments"].values():
        assert record["labels"] == ["canada"] and set(record["label_p"]) == {"canada"}


def test_only_reassigns_exactly_the_given_groups(ws: Workspace) -> None:
    ws.write_plan(operations.edit(ws.plan(), "costs", {"scope": "תחום אחר."}).plan)
    clf = FakeClassifier()
    result = ws.run(clf, only=["g-00000004"])
    assert len(clf.items) == 1 and result["full"] == 1
    # the plan change is still pending for the others
    clf = FakeClassifier()
    assert ws.run(clf)["plan_changes"]["rescoped_pages"] == ["costs"]
    assert len(clf.asked("page")) == 3


def test_rebuild_asks_everything_but_keeps_manual_records_and_proposals(ws: Workspace) -> None:
    _set_manual(ws, "g-00000000", pages=["costs"], labels=[])
    payload = ws.edited()
    payload["proposals"] = [{"id": "prop-1", "kind": "page", "group_ids": [], "title_he": "x",
                             "description": "y", "reason": "z", "status": "rejected"}]
    ws.write_edited(payload)
    clf = FakeClassifier()
    result = ws.run(clf, rebuild=True)
    assert result["full"] == 11 and len(clf.items) == 11
    after = ws.edited()
    assert after["assignments"]["g-00000000"]["pages"] == ["costs"]
    assert after["proposals"] == payload["proposals"]
    assert any(BACKUPS_DIR.glob(f"{ws.artifact.edited.stem}_*.json"))


def test_apply_plan_edit_follows_one_operation(ws: Workspace) -> None:
    assignments = ws.edited()["assignments"]
    assignments["g-00000006"].update(manual=True)
    merged = apply_plan_edit(assignments, operations.merge(ws.plan(), "hospital", ["clinics"]))
    assert merged.assignments["g-00000002"]["pages"] == ["hospital"]
    assert merged.assignments["g-00000006"]["pages"] == ["hospital"]
    assert merged.reask == () and merged.cleared == {}

    rescoped = apply_plan_edit(assignments, operations.edit(ws.plan(), "costs", {"scope": "אחר."}))
    assert rescoped.reask == ("g-00000001", "g-00000005", "g-00000009")
    assert rescoped.screen_pages == ("costs",)

    dropped = apply_plan_edit(assignments, operations.delete(ws.plan(), "usa"))
    assert dropped.reask == () and all(r["labels"] == [] for r in dropped.assignments.values())
    assert dropped.cleared["g-00000000"] == ["usa"]

    split = operations.split_page(ws.plan(), "costs", "fees", title_he="עמלות", description="ע",
                                  scope="עמלות.")
    after_split = apply_plan_edit(assignments, split)
    assert after_split.reask == ("g-00000001", "g-00000005", "g-00000009")
    assert after_split.screen_pages == ("fees", "costs")
    assert assignments["g-00000002"]["pages"] == [PAGE_IDS[2]]  # the input is not changed
