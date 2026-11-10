"""Plan-editing operations: pure, validated, ids stable, and what disappears is reported."""

from __future__ import annotations

import copy

import pytest

from step_6_plan import operations as ops
from step_6_plan.model import PlanError, check_plan, find_node
from tests.plan_fixtures import make_plan


@pytest.fixture
def plan() -> dict:
    return make_plan()


def _children(plan: dict, node_id: str) -> list[str]:
    return [c["id"] for c in find_node(plan, node_id)[0]["children"]]


def test_operations_never_change_their_input(plan: dict) -> None:
    before = copy.deepcopy(plan)
    ops.rename(plan, "costs", "חדש")
    ops.delete(plan, "costs")
    ops.merge(plan, "agencies", ["costs"])
    ops.move(plan, "costs", "medical")
    assert plan == before


def test_add_node_and_page(plan: dict) -> None:
    edit = ops.add_node(plan, "after", title_he="אחרי", parent_id=None)
    edit = ops.add_node(edit.plan, "parenting", title_he="הורות", description="ק", parent_id="after")
    edit = ops.add_page(edit.plan, "school", parent_id="parenting", title_he="בית ספר",
                        description="ת", scope="ס", position=0)
    assert edit.created == ("school",)
    assert edit.plan["pages"]["school"]["path"] == ["after", "parenting"]
    assert edit.plan["pages"]["school"]["aliases"] == {"he": [], "en": []}
    assert _children(edit.plan, "parenting") == ["school"]
    check_plan(edit.plan)


@pytest.mark.parametrize(
    ("call", "expected"),
    [
        (lambda p: ops.add_page(p, "costs", parent_id="medical", title_he="x", description="x",
                                scope="x"), "used 2 times"),
        (lambda p: ops.add_page(p, "Bad Id", parent_id="medical", title_he="x", description="x",
                                scope="x"), "not a slug"),
        (lambda p: ops.add_page(p, "top-page", parent_id="before", title_he="x", description="x",
                                scope="x"), "depth 2"),
        (lambda p: ops.add_page(p, "x-page", parent_id="costs", title_he="x", description="x",
                                scope="x"), "is a page"),
        (lambda p: ops.add_page(p, "x-page", parent_id="nowhere", title_he="x", description="x",
                                scope="x"), "no tree node"),
        (lambda p: ops.add_label(p, "usa", title_he="x", description="x", kind="country"),
         "already exists"),
        (lambda p: ops.rename(p, "costs", " "), "non-empty text 'title_he'"),
        (lambda p: ops.edit(p, "costs", {"title_he": "x"}), "no editable field"),
        (lambda p: ops.edit(p, "costs", {"aliases": {"he": []}}), "aliases"),
        (lambda p: ops.delete(p, "choosing"), "still holds"),
        (lambda p: ops.merge(p, "costs", ["usa"]), "is not a page"),
        (lambda p: ops.merge(p, "costs", ["costs"]), "into itself"),
        (lambda p: ops.move(p, "before", "choosing"), "its own subtree"),
        (lambda p: ops.move(p, "usa", "before"), "is a label"),
        (lambda p: ops.rename(p, "ghost", "x"), "no page, category or label"),
    ],
)
def test_invalid_edits_raise_and_explain(plan: dict, call: object, expected: str) -> None:
    with pytest.raises(PlanError) as info:
        call(plan)  # type: ignore[operator]
    assert any(expected in error for error in info.value.errors), info.value.errors


def test_add_page_respects_max_pages(plan: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    from step_6_plan.model import PLAN_CONFIG

    monkeypatch.setitem(PLAN_CONFIG, "max_pages", 4)
    with pytest.raises(PlanError, match="at most 4"):
        ops.add_page(plan, "extra", parent_id="medical", title_he="x", description="x", scope="x")


def test_rename_keeps_ids_and_syncs_the_tree(plan: dict) -> None:
    new = ops.rename(plan, "costs", "עלויות").plan
    assert new["pages"]["costs"]["title_he"] == "עלויות"
    assert find_node(new, "costs")[0]["title_he"] == "עלויות"
    assert ops.rename(new, "choosing", "בחירות").plan["tree"][0]["children"][0]["title_he"] == "בחירות"
    assert ops.rename(new, "usa", "ארה״ב").plan["labels"]["usa"]["title_he"] == "ארה״ב"


def test_edit_fields_and_scope_change_asks_for_reassignment(plan: dict) -> None:
    edit = ops.edit(plan, "costs", {"description": "חדש", "aliases": {"he": ["א"], "en": ["b"]}})
    assert edit.reassign == ()
    assert find_node(edit.plan, "costs")[0]["description"] == "חדש"
    edit = ops.edit(edit.plan, "costs", {"scope": "צר יותר"})
    assert edit.reassign == ("costs",)
    assert ops.edit(plan, "usa", {"kind": "region"}).plan["labels"]["usa"]["kind"] == "region"
    assert ops.edit(plan, "before", {"description": "ת"}).plan["tree"][0]["description"] == "ת"


def test_delete_page_reports_and_retires_the_id(plan: dict) -> None:
    edit = ops.delete(plan, "costs")
    assert edit.removed_pages == ("costs",) and edit.redirects == {}
    assert "costs" not in edit.plan["pages"] and _children(edit.plan, "choosing") == ["agencies"]
    assert edit.plan["metadata"]["retired_ids"] == ["costs"]
    with pytest.raises(PlanError, match="retired"):
        ops.add_page(edit.plan, "costs", parent_id="choosing", title_he="x", description="x",
                     scope="x")


def test_delete_empty_category_and_label(plan: dict) -> None:
    edit = ops.add_node(plan, "spare", title_he="ריק", parent_id="before")
    edit = ops.delete(edit.plan, "spare")
    assert edit.removed_pages == () and _children(edit.plan, "before") == ["choosing"]
    edit = ops.delete(edit.plan, "usa")
    assert edit.removed_labels == ("usa",) and "usa" not in edit.plan["labels"]


def test_merge_pages_redirects_and_keeps_aliases(plan: dict) -> None:
    first = ops.merge(plan, "agencies", ["costs"])
    assert first.removed_pages == ("costs",) and first.redirects == {"costs": "agencies"}
    survivor = first.plan["pages"]["agencies"]
    assert survivor["merged_from"] == ["costs"]
    assert "כותרת costs" in survivor["aliases"]["he"] and "costs" in survivor["aliases"]["en"]
    assert survivor["example_group_ids"] == ["g-00000000", "g-00000001"]
    second = ops.merge(first.plan, "clinics", ["agencies"])
    assert second.redirects == {"agencies": "clinics", "costs": "clinics"}
    assert second.plan["pages"]["clinics"]["merged_from"] == ["agencies", "costs"]
    assert _children(second.plan, "choosing") == []


def test_merge_labels(plan: dict) -> None:
    edit = ops.merge(plan, "usa", ["georgia"])
    assert edit.removed_labels == ("georgia",) and edit.redirects == {"georgia": "usa"}
    assert edit.plan["labels"]["usa"]["merged_from"] == ["georgia"]


def test_split_page_places_the_new_page_next_to_the_original(plan: dict) -> None:
    edit = ops.split_page(plan, "agencies", "lawyers", title_he="עורכי דין", description="ת",
                          scope="ס")
    assert edit.created == ("lawyers",) and edit.reassign == ("agencies",)
    assert _children(edit.plan, "choosing") == ["agencies", "lawyers", "costs"]
    elsewhere = ops.split_page(plan, "agencies", "lawyers", title_he="עורכי דין",
                               description="ת", scope="ס", parent_id="medical")
    assert _children(elsewhere.plan, "medical") == ["clinics", "hospital", "lawyers"]


def test_move_and_reorder(plan: dict) -> None:
    moved = ops.move(plan, "costs", "medical", position=0)
    assert _children(moved.plan, "medical") == ["costs", "clinics", "hospital"]
    assert moved.plan["pages"]["costs"]["path"] == ["during", "medical"]
    reordered = ops.move(plan, "during", None, position=0)
    assert [n["id"] for n in reordered.plan["tree"]] == ["during", "before"]
    category = ops.move(plan, "choosing", "during")
    assert category.plan["pages"]["agencies"]["path"] == ["during", "choosing"]
    assert ops.kind_of(category.plan, "choosing") == "node"


def test_split_page_can_narrow_the_original_in_the_same_edit(plan: dict) -> None:
    edit = ops.split_page(plan, "agencies", "lawyers", title_he="עורכי דין", description="ת",
                          scope="ס", original_scope="רק סוכנויות")
    assert edit.plan["pages"]["agencies"]["scope"] == "רק סוכנויות"
    assert edit.reassign == ("agencies",)
    with pytest.raises(PlanError, match="non-empty text 'scope'"):
        ops.split_page(plan, "agencies", "lawyers", title_he="x", description="x", scope="x",
                       original_scope=" ")


def test_change_id_keeps_the_page_and_redirects_the_old_id(plan: dict) -> None:
    merged = ops.merge(plan, "agencies", ["costs"]).plan
    edit = ops.change_id(merged, "agencies", "providers")
    assert edit.created == ("providers",) and edit.removed_pages == () and edit.reassign == ()
    assert edit.redirects == {"agencies": "providers", "costs": "providers"}
    page = edit.plan["pages"]["providers"]
    assert page["merged_from"] == ["costs", "agencies"]
    assert page["title_he"] == merged["pages"]["agencies"]["title_he"]
    assert page["example_group_ids"] == merged["pages"]["agencies"]["example_group_ids"]
    assert list(edit.plan["pages"]) == ["providers", "clinics", "hospital"]  # same order
    assert _children(edit.plan, "choosing") == ["providers"]
    with pytest.raises(PlanError, match="retired"):
        ops.change_id(edit.plan, "providers", "agencies")


def test_change_id_of_a_label_and_a_category(plan: dict) -> None:
    label = ops.change_id(plan, "usa", "united-states")
    assert label.redirects == {"usa": "united-states"}
    assert label.plan["labels"]["united-states"]["merged_from"] == ["usa"]
    category = ops.change_id(plan, "choosing", "planning")
    assert category.redirects == {} and "choosing" in category.plan["metadata"]["retired_ids"]
    assert category.plan["pages"]["costs"]["path"] == ["before", "planning"]
    for item_id, taken in (("costs", "agencies"), ("usa", "georgia"), ("usa", "costs")):
        with pytest.raises(PlanError, match="already in use"):
            ops.change_id(plan, item_id, taken)
    with pytest.raises(PlanError, match="not a slug"):
        ops.change_id(plan, "costs", "Bad Id")
