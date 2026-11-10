"""The plan review tool: endpoints over the plan operations, saving, undo, reloads, locks (synthetic C7)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from review_tool.artifacts import ArtifactGraph, ArtifactNode
from review_tool.tools import TOOL_SPECS
from step_6_plan import operations as ops
from step_6_plan.reviewer.tool import PlanTool
from tests.plan_fixtures import make_groups, make_plan
from tests.review_tool_support import BASE_URL, build_app, locked_elsewhere
from utils.json_io import write_json_file
from utils.paths import BACKUPS_DIR

SHORT_LOCK_TIMEOUT = 0.2
MERGE = {"op": "merge", "survivor_id": "agencies", "merged_ids": ["costs"]}


def plan_graph(root: Path) -> ArtifactGraph:
    return ArtifactGraph([
        ArtifactNode("groups", "Groups", root / "groups.json", root / "groups_edited.json",
                     "step_5", ()),
        ArtifactNode("plan", "Plan", root / "plan.json", root / "plan_edited.json", "step_6",
                     ("groups",), curated=True),
        ArtifactNode("assignments", "Assignments", root / "assignments.json",
                     root / "assignments_edited.json", "step_6b", ("groups", "plan")),
    ])


def coverage_report() -> dict[str, Any]:
    return {
        "sample_size": 10, "total_groups": 100, "confident_share": 0.8, "none_share": 0.1,
        "unsure_share": 0.1,
        "page_counts": {"agencies": 5, "costs": 3, "clinics": 2, "hospital": 0},
        "projected_groups": {"agencies": 50, "costs": 30, "clinics": 20, "hospital": 0},
        "small_pages": ["hospital"], "large_pages": ["agencies"],
        "none_group_ids": ["g-00000003"], "unsure_group_ids": ["g-00000004", "g-gone"],
        "findings": ["Page 'hospital' attracted only 0 of 10 sampled groups."],
        "metadata": {"generated_at": "2026-01-01T00:00:00+00:00"},
    }


class PlanSetup:
    def __init__(self, root: Path, *, with_plan: bool = True) -> None:
        self.root = root
        self.graph = plan_graph(root)
        node = self.graph.node("plan")
        self.original, self.edited = node.original, node.edited
        self.coverage = root / "coverage_report.json"
        write_json_file({"groups": make_groups(), "metadata": {}}, root / "groups.json")
        if with_plan:
            write_json_file(make_plan(), self.original)
        self.client = self.new_client()

    def new_client(self) -> TestClient:
        """A fresh tool (new store handle, empty history) on the same files, as after a restart."""
        tool = PlanTool(self.graph, coverage_path=self.coverage, lock_timeout=SHORT_LOCK_TIMEOUT)
        app = build_app(self.root, specs=(tool.spec,), graph=self.graph)
        return TestClient(app, base_url=BASE_URL)

    def get(self, path: str, status: int = 200, **params: Any) -> Any:
        response = self.client.get(f"/api/plan{path}", params=params)
        assert response.status_code == status, response.text
        return response.json()

    def post(self, path: str, body: dict[str, Any] | None = None, status: int = 200) -> Any:
        response = self.client.post(f"/api/plan{path}", json=body)
        assert response.status_code == status, response.text
        return response.json()

    def apply(self, body: dict[str, Any], status: int = 200) -> Any:
        return self.post("/operations", body, status)

    def saved(self) -> dict[str, Any]:
        return json.loads(self.edited.read_text(encoding="utf-8"))


@pytest.fixture
def setup(tmp_path: Path) -> PlanSetup:
    return PlanSetup(tmp_path)


def children(overview: dict[str, Any], node_id: str) -> list[str]:
    return next(row for row in overview["nodes"] if row["id"] == node_id)["children"]


# ------------------------------------------------------------- registration


def test_tool_is_hosted_by_the_server() -> None:
    spec = next(spec for spec in TOOL_SPECS if spec.name == "plan")
    assert spec.reads == ("plan", "groups") and spec.writes == ("plan", "assignments")


def test_missing_plan_still_registers_and_answers_409(tmp_path: Path) -> None:
    setup = PlanSetup(tmp_path, with_plan=False)
    tools = setup.client.get("/api/tools").json()
    assert [(t["name"], t["missing"]) for t in tools] == [("plan", ["plan"])]
    for path in ("/plan", "/pages/costs", "/ids"):
        response = setup.client.get(f"/api/plan{path}")
        assert response.status_code == 409 and "step_6_plan.run" in response.json()["detail"]
    setup.apply(MERGE, status=409)
    write_json_file(make_plan(), setup.original)  # step 6 ran: no restart needed
    assert setup.get("/plan")["budget"]["pages"] == 4


# ------------------------------------------------------------------ reading


def test_overview_shows_tree_budget_labels_and_no_changes(setup: PlanSetup) -> None:
    overview = setup.get("/plan")
    assert overview["file"] == str(setup.original) and overview["edited_exists"] is False
    assert overview["errors"] == [] and overview["undo"] is None and overview["coverage"] is None
    rows = {row["id"]: row for row in overview["nodes"]}
    assert [row["id"] for row in overview["nodes"]] == [
        "before", "choosing", "agencies", "costs", "during", "medical", "clinics", "hospital"]
    assert rows["costs"] == {
        "id": "costs", "kind": "page", "title_he": "כותרת costs", "description": "תיאור costs",
        "depth": 3, "parent_id": "choosing", "index": 1, "siblings": 2, "children": [],
        "page_count": 0,
    }
    assert rows["before"]["kind"] == "category" and rows["before"]["page_count"] == 2
    assert overview["budget"] == {"pages": 4, "labels": 2, "max_pages": 30, "min_depth": 3,
                                  "max_depth": 4, "slug_max_length": 40}
    assert overview["branches"] == [
        {"id": "before", "title_he": "לפני", "pages": 2, "depth": 3},
        {"id": "during", "title_he": "במהלך", "pages": 2, "depth": 3}]
    assert overview["label_kinds"] == [{"kind": "country", "label_ids": ["usa", "georgia"]}]
    assert overview["pages"]["costs"]["scope"] == "מה שייך לעמוד costs ומה לא."
    assert overview["changes"] == {"items": {}, "removed": []}
    assert overview["groups_available"] is True
    codes = {w["code"]: w["ids"] for w in overview["warnings"]}
    assert codes == {"no_examples": ["hospital"]}


def test_page_detail_has_example_texts_and_coverage(setup: PlanSetup) -> None:
    detail = setup.get("/pages/agencies")
    assert detail["coverage"] is None
    assert [e["group_id"] for e in detail["examples"]] == ["g-00000000"]
    assert detail["examples"][0]["claim_text"].startswith("Synthetic claim number 0")
    assert detail["examples"][0]["claim_count"] == 1
    write_json_file(coverage_report(), setup.coverage)
    detail = setup.get("/pages/agencies")
    assert detail["coverage"] == {"measured": True, "count": 5, "projected": 50,
                                  "merged_count": 0, "merged_projected": 0,
                                  "scope_changed": False, "small": False, "large": True}
    setup.get("/pages/ghost", status=404)
    setup.get("/pages/before", status=404)  # a category is not a page


def test_coverage_is_read_against_todays_plan(setup: PlanSetup) -> None:
    write_json_file(coverage_report(), setup.coverage)
    setup.apply(MERGE)
    setup.apply({"op": "edit", "item_id": "clinics", "changes": {"scope": "צר"}})
    coverage = setup.get("/plan")["coverage"]
    assert coverage["confident_share"] == 0.8 and coverage["none_count"] == 1
    assert coverage["generated_at"] == "2026-01-01T00:00:00+00:00"
    assert coverage["pages"]["agencies"]["merged_count"] == 3
    assert coverage["pages"]["clinics"]["scope_changed"] is True
    codes = {(w["code"], tuple(w["ids"])) for w in setup.get("/plan")["warnings"]}
    assert ("small_page", ("hospital",)) in codes and ("large_page", ("agencies",)) in codes
    unplaced = setup.get("/coverage/unplaced")
    assert [g["group_id"] for g in unplaced["none"]] == ["g-00000003"]
    assert unplaced["unsure"][1] == {"group_id": "g-gone", "missing": True}


@pytest.mark.parametrize(
    ("item_id", "ok", "problem"),
    [
        ("lawyers", True, None),
        ("", False, "הקלידו"),
        ("Bad Id", False, "אותיות לטיניות"),
        ("x" * 41, False, "ארוך"),
        ("costs", False, "דף „כותרת costs”"),
        ("choosing", False, "קטגוריה"),
        ("usa", False, "תווית"),
    ],
)
def test_check_id(setup: PlanSetup, item_id: str, ok: bool, problem: str | None) -> None:
    answer = setup.get("/ids", id=item_id)
    assert answer["ok"] is ok and answer["id"] == item_id
    assert (answer["problem"] is None) if problem is None else (problem in answer["problem"])


def test_check_id_refuses_retired_ids(setup: PlanSetup) -> None:
    setup.apply({"op": "delete", "item_id": "hospital"})
    assert "הוצא משימוש" in setup.get("/ids", id="hospital")["problem"]


# ------------------------------------------------------------------ preview


def test_preview_tells_the_consequences_and_saves_nothing(setup: PlanSetup) -> None:
    preview = setup.post("/operations/preview", MERGE)
    assert preview["label"] == "מיזוג „כותרת costs” לתוך „כותרת agencies”"
    assert preview["edit"] == {"created": [], "removed_pages": ["costs"], "removed_labels": [],
                               "redirects": {"costs": "agencies"}, "reassign": []}
    scope = setup.post("/operations/preview",
                       {"op": "edit", "item_id": "costs", "changes": {"scope": "צר"}})
    assert scope["edit"]["reassign"] == ["costs"]
    assert not setup.edited.exists()


def test_invalid_changes_are_409_with_the_rules_verbatim(setup: PlanSetup) -> None:
    for path in ("/operations/preview", "/operations"):
        detail = setup.post(path, {"op": "move", "item_id": "costs", "parent_id": None},
                            status=409)["detail"]
        lead, *rules = detail.split("\n")
        assert "שוברת את הכללים" in lead
        assert rules == ["• page 'costs' sits at depth 1; pages must sit between depth 3 and 4"]
    detail = setup.apply({"op": "delete", "item_id": "choosing"}, status=409)["detail"]
    assert "still holds ['agencies', 'costs']" in detail
    assert not setup.edited.exists()


@pytest.mark.parametrize(
    "body",
    [
        {"op": "explode", "item_id": "costs"},
        {"op": "move", "item_id": "costs", "parent_id": "medical", "position": -1},
        {"op": "rename", "item_id": "costs"},
    ],
)
def test_malformed_requests_are_422(setup: PlanSetup, body: dict[str, Any]) -> None:
    setup.apply(body, status=422)


# ------------------------------------------------------------------ changes


def test_every_operation_saves_a_valid_plan_to_the_edited_file(setup: PlanSetup) -> None:
    original = setup.original.read_bytes()
    steps = [
        ({"op": "add_category", "node_id": "after", "title_he": "אחרי"}, "after"),
        ({"op": "add_category", "node_id": "home", "title_he": "בית", "parent_id": "after"},
         "home"),
        ({"op": "add_page", "page_id": "school", "parent_id": "home", "title_he": "בית ספר",
          "description": "ת", "scope": "ס", "aliases": {"he": ["גן"], "en": ["kindergarten"]}},
         "school"),
        ({"op": "add_label", "label_id": "canada", "title_he": "קנדה", "description": "ק",
          "kind": "country"}, "canada"),
        ({"op": "rename", "item_id": "costs", "title_he": "עלויות"}, "costs"),
        ({"op": "edit", "item_id": "usa", "changes": {"kind": "region"}}, "usa"),
        ({"op": "split", "page_id": "agencies", "new_page_id": "lawyers", "title_he": "עו״ד",
          "description": "ת", "scope": "ס", "original_scope": "רק סוכנויות"}, "lawyers"),
        ({"op": "move", "item_id": "hospital", "parent_id": "home", "position": 0}, "hospital"),
        (MERGE, "agencies"),
        ({"op": "merge", "survivor_id": "usa", "merged_ids": ["georgia"]}, "usa"),
        ({"op": "change_id", "item_id": "school", "new_id": "schools"}, "schools"),
        ({"op": "delete", "item_id": "clinics"}, "medical"),
        ({"op": "delete", "item_id": "canada"}, None),
    ]
    for body, focus in steps:
        answer = setup.apply(body)
        assert answer["focus"] == focus, body
        assert answer["overview"]["errors"] == []
        assert ops.check_plan(setup.saved()) == setup.saved()
    plan = setup.saved()
    assert setup.original.read_bytes() == original  # the proposal is never touched
    assert plan["metadata"]["edited_by"] == "plan_tool" and plan["metadata"]["edited_at"]
    assert "עלויות" in plan["pages"]["agencies"]["aliases"]["he"]  # renamed, then merged in
    assert plan["pages"]["agencies"]["scope"] == "רק סוכנויות"
    assert plan["pages"]["agencies"]["merged_from"] == ["costs"]
    assert plan["pages"]["schools"]["path"] == ["after", "home"]
    assert plan["pages"]["hospital"]["path"] == ["after", "home"]
    assert plan["labels"]["usa"] == {"title_he": "ארצות הברית", "description": "ארה״ב",
                                     "kind": "region", "merged_from": ["georgia"]}
    assert set(plan["metadata"]["retired_ids"]) == {"clinics", "canada"}
    changes = setup.get("/plan")["changes"]
    assert changes["items"]["schools"] == "new"
    assert changes["items"]["agencies"] == ["scope", "aliases"]
    assert changes["items"]["hospital"] == ["path"]
    assert {"id": "georgia", "kind": "label", "title_he": "גאורגיה", "redirect": "usa"} in \
        changes["removed"]


def test_moves_are_named_for_the_undo_button(setup: PlanSetup) -> None:
    reorder = {"op": "move", "item_id": "costs", "parent_id": "choosing", "position": 0}
    assert setup.post("/operations/preview", reorder)["label"] == "שינוי הסדר: „כותרת costs”"
    move = {"op": "move", "item_id": "costs", "parent_id": "medical"}
    assert setup.post("/operations/preview", move)["label"] == "העברת „כותרת costs” אל „רפואי”"
    assert children(setup.apply(reorder)["overview"], "choosing") == ["costs", "agencies"]


def test_answers_carry_the_downstream_consequences(setup: PlanSetup) -> None:
    answer = setup.apply(MERGE)
    assert answer["edit"]["removed_pages"] == ["costs"]
    assert answer["edit"]["redirects"] == {"costs": "agencies"}
    assert children(answer["overview"], "choosing") == ["agencies"]
    split = setup.apply({"op": "split", "page_id": "clinics", "new_page_id": "labs",
                         "title_he": "מעבדות", "description": "ת", "scope": "ס"})
    assert split["edit"]["created"] == ["labs"] and split["edit"]["reassign"] == ["clinics"]
    assert children(split["overview"], "medical") == ["clinics", "labs", "hospital"]


def test_each_save_backs_up_the_previous_edited_file(setup: PlanSetup) -> None:
    def backups() -> set[Path]:
        return set(BACKUPS_DIR.glob("plan_edited_*.json")) if BACKUPS_DIR.is_dir() else set()

    before = backups()
    setup.apply({"op": "rename", "item_id": "costs", "title_he": "א"})
    assert backups() == before  # nothing to back up yet: the original stays as it is
    setup.apply({"op": "rename", "item_id": "costs", "title_he": "ב"})
    new = backups() - before
    assert len(new) == 1
    assert json.loads(new.pop().read_text(encoding="utf-8"))["pages"]["costs"]["title_he"] == "א"


def test_changes_survive_a_fresh_tool(setup: PlanSetup) -> None:
    setup.apply(MERGE)
    setup.client = setup.new_client()
    overview = setup.get("/plan")
    assert "costs" not in overview["pages"] and overview["edited_exists"] is True
    assert overview["file"] == str(setup.edited)
    assert overview["undo"] is None  # undo is for this session only


# --------------------------------------------------------------------- undo


def test_undo_goes_back_one_change_at_a_time(setup: PlanSetup) -> None:
    setup.post("/undo", status=409)
    setup.apply({"op": "rename", "item_id": "costs", "title_he": "עלויות"})
    merged = setup.apply(MERGE)
    assert merged["overview"]["undo"]["label"] == "מיזוג „עלויות” לתוך „כותרת agencies”"
    undone = setup.post("/undo")
    assert undone["label"] == merged["overview"]["undo"]["label"]
    assert undone["focus"] == "agencies"
    assert "costs" in setup.saved()["pages"]
    assert undone["overview"]["undo"]["label"].startswith("שינוי השם")
    setup.post("/undo")
    assert setup.saved()["pages"]["costs"]["title_he"] == "כותרת costs"
    assert setup.get("/plan")["undo"] is None
    setup.post("/undo", status=409)


def test_undo_is_refused_after_an_outside_change(setup: PlanSetup) -> None:
    setup.apply(MERGE)
    rewritten = make_plan()  # e.g. a re-plan (--rebuild) wrote a new plan meanwhile
    rewritten["pages"]["hospital"]["scope"] = "תכנון מחדש"
    write_json_file(rewritten, setup.edited)
    assert setup.get("/plan")["undo"] is None
    detail = setup.post("/undo", status=409)["detail"]
    assert "השתנו מחוץ לכלי" in detail
    assert setup.saved()["pages"]["hospital"]["scope"] == "תכנון מחדש"


# ---------------------------------------------------------- files on disk


def test_an_outside_rewrite_is_picked_up_before_the_next_change(setup: PlanSetup) -> None:
    assert setup.get("/plan")["budget"]["pages"] == 4
    rebuilt = ops.delete(make_plan(), "hospital").plan  # step 6 --rebuild wrote both files
    write_json_file(rebuilt, setup.original)
    write_json_file(rebuilt, setup.edited)
    assert setup.get("/plan")["budget"]["pages"] == 3
    setup.apply(MERGE)
    assert set(setup.saved()["pages"]) == {"agencies", "clinics"}


def test_a_locked_plan_is_423_and_reads_still_work(setup: PlanSetup) -> None:
    with locked_elsewhere(setup.edited):
        response = setup.client.post("/api/plan/operations", json=MERGE)
        assert response.status_code == 423 and "locked" in response.json()["detail"]
        assert setup.client.post("/api/plan/undo").status_code == 423
        assert setup.get("/plan")["budget"]["pages"] == 4
        assert setup.post("/operations/preview", MERGE)["edit"]["removed_pages"] == ["costs"]
    assert not setup.edited.exists()
    setup.apply(MERGE)


def test_an_invalid_plan_on_disk_is_reported_not_overwritten(setup: PlanSetup) -> None:
    broken = make_plan()
    broken["pages"]["costs"]["scope"] = ""
    write_json_file(broken, setup.edited)
    overview = setup.get("/plan")
    assert overview["errors"] == ["page 'costs' needs a non-empty text 'scope'"]
    assert "nodes" in overview
    setup.apply({"op": "rename", "item_id": "agencies", "title_he": "x"}, status=409)
    fixed = setup.apply({"op": "edit", "item_id": "costs", "changes": {"scope": "תקין"}})
    assert fixed["overview"]["errors"] == []
    write_json_file({"pages": [], "tree": "old shape"}, setup.edited)
    overview = setup.get("/plan")
    assert overview["unreadable"] is True and overview["errors"]
