"""The plan tool's exact page sizes (assignments against ``plan.page_groups``), the groups that
judge a page's scope (a sample on it, the closest ones it did not get) and the withheld groups,
which are on no page. Synthetic data: see ``test_plan_tool_assignments`` (topic 0 -> agencies,
1 -> costs + agencies, 2 -> clinics, 3 -> no page, a little hospital for topics 2 and 3)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from review_tool.tools.plan.sizes import FOLD, GUIDE, LARGE, SMALL, SPLIT, page_sizes, size_flag
from tests.test_plan_tool_assignments import AssignSetup

PLAN = {"pages": {"a": {}, "b": {}}}


@pytest.fixture
def setup(tmp_path: Path) -> AssignSetup:
    return AssignSetup(tmp_path)


@pytest.mark.parametrize(("count", "flag"), [
    (0, FOLD),
    (GUIDE["fold_below"] - 1, FOLD),
    (GUIDE["fold_below"], SMALL),
    (GUIDE["typical_min"] - 1, SMALL),
    (GUIDE["typical_min"], None),
    (GUIDE["typical_max"], None),
    (GUIDE["typical_max"] + 1, LARGE),
    (GUIDE["split_above"], LARGE),
    (GUIDE["split_above"] + 1, SPLIT),
])
def test_a_size_is_flagged_only_outside_the_guide(count: int, flag: str | None) -> None:
    assert size_flag(count) == flag


def test_only_main_pages_count_and_withheld_groups_count_nowhere() -> None:
    records = {
        "g1": {"pages": ["a", "b"]},  # main page a, mentions b
        "g2": {"pages": ["a"]},
        "g3": {"pages": ["b"]},
        "g4": {"pages": []},
        "g5": {"pages": ["gone"]},  # a page the plan no longer has
    }
    sizes = page_sizes(records, PLAN, withheld={"g2"})
    assert {page: size["groups"] for page, size in sizes.items()} == {"a": 1, "b": 1}
    assert page_sizes(records, PLAN, withheld=set())["a"]["groups"] == 2


def test_the_overview_flags_every_page_by_its_exact_size(setup: AssignSetup) -> None:
    overview = setup.get("/plan")
    sizes = overview["assignments"]["sizes"]
    assert {page: size["groups"] for page, size in sizes.items()} == {
        "agencies": 3, "costs": 3, "clinics": 3, "hospital": 0}
    size_warnings = [w for w in overview["warnings"] if w["code"].endswith("_page")]
    assert {(w["code"], w["ids"][0], w["tone"]) for w in size_warnings} == {
        ("fold_page", page, "warning") for page in sizes}
    hospital = next(w for w in size_warnings if w["ids"] == ["hospital"])
    assert f"main page of 0 claim groups, fewer than {GUIDE['fold_below']}" in hospital["text"]
    assert all(w["tone"] == "warning" for w in overview["warnings"])


def test_a_withheld_group_is_marked_and_not_counted(setup: AssignSetup) -> None:
    path = setup.root / "groups.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    lone = next(g for g in payload["groups"] if g["group_id"] == "g-00000001")
    assert lone["stance"] == "negative" and lone["entities"]
    lone["supporter_count"] = 1  # a lone negative report about something named
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert setup.get("/plan")["assignments"]["sizes"]["costs"]["groups"] == 2
    rows = setup.groups(scope="page", id="costs")["items"]
    assert {row["group_id"]: row["withheld"] for row in rows}["g-00000001"] is True
    assert not any(row["withheld"] for row in rows if row["group_id"] != "g-00000001")


def test_a_page_shows_a_sample_and_the_closest_groups_it_did_not_get(setup: AssignSetup) -> None:
    hospital = setup.get("/pages/hospital")
    assert hospital["size"] == {"groups": 0, "flag": FOLD} and hospital["sample"] == []
    assert [(row["group_id"], row["certainty"]) for row in hospital["near"]] == [
        ("g-00000002", 0.2), ("g-00000006", 0.2), ("g-0000000a", 0.2),
        ("g-00000003", 0.15), ("g-00000007", 0.15)]  # scope_sample = 5, most probable first
    assert hospital["near"][0]["pages"] == ["clinics"] and hospital["near"][0]["claim_text"]
    agencies = setup.get("/pages/agencies")
    assert [row["group_id"] for row in agencies["sample"]] == ["g-00000000", "g-00000004", "g-00000008"]
    assert all(row["role"] == "primary" for row in agencies["sample"])
    assert agencies["near"] == []  # groups that mention it have it, so they are not "near"
