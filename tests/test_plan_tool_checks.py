"""Warnings the plan tool shows about a valid plan (synthetic C7)."""

from __future__ import annotations

import pytest

from step_6_plan import operations as ops
from step_6_plan.model import PLAN_CONFIG
from review_tool.tools.plan.checks import plan_warnings
from tests.plan_fixtures import make_groups, make_plan

GROUP_IDS = {group["group_id"] for group in make_groups()}


def codes(plan: dict) -> dict[str, list[list[str]]]:
    found: dict[str, list[list[str]]] = {}
    for warning in plan_warnings(plan, group_ids=GROUP_IDS, sizes=None):
        assert warning["text"]
        found.setdefault(warning["code"], []).append(warning["ids"])
    return found


def test_a_label_that_repeats_a_page_is_flagged() -> None:
    plan = ops.rename(make_plan(), "clinics", "מרפאות בגאורגיה").plan
    assert codes(plan)["label_like_page"] == [["georgia", "clinics"]]
    plan = ops.edit(make_plan(), "costs", {"aliases": {"he": ["ארצות הברית"], "en": []}}).plan
    assert codes(plan)["label_like_page"] == [["usa", "costs"]]
    plan = ops.rename(make_plan(), "usa", "ארצות הברית (כיעד)").plan
    plan = ops.rename(plan, "costs", "עלויות בארצות הברית").plan
    assert codes(plan)["label_like_page"] == [["usa", "costs"]]


def test_same_titles_empty_categories_and_lonely_kinds() -> None:
    plan = ops.rename(make_plan(), "georgia", "כותרת costs").plan
    plan = ops.edit(plan, "georgia", {"kind": "region"}).plan
    plan = ops.add_node(plan, "spare", title_he="ריק", parent_id="before").plan
    found = codes(plan)
    assert found["same_title"] == [["costs", "georgia"]]
    assert found["empty_category"] == [["spare"]]
    assert sorted(found["single_label_kind"]) == [["georgia"], ["usa"]]


def test_pages_without_known_examples() -> None:
    assert codes(make_plan())["no_examples"] == [["hospital"]]
    assert plan_warnings(make_plan(), group_ids=set(), sizes=None)[0]["ids"] == ["agencies"]


def test_a_full_page_budget_is_flagged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(PLAN_CONFIG, "max_pages", 4)
    assert codes(make_plan())["page_budget"] == [[]]
