"""The plan tool's exact page sizes (assignments against ``plan.page_claims``), the claims that
judge a page's scope (a sample on it, the closest ones it did not get) and the withheld claims,
which are on no page. Synthetic data: see ``test_plan_tool_assignments`` (topic 0 -> agencies,
1 -> costs + agencies, 2 -> clinics, 3 -> no page, a little hospital for topics 2 and 3)."""

from __future__ import annotations

from pathlib import Path

import pytest

from review_tool.tools.plan.sizes import FOLD, GUIDE, LARGE, SMALL, SPLIT, page_sizes, size_flag
from tests.claim_fixtures import claim_record, claim_records, write_claim_inputs
from tests.plan_fixtures import short_id_of
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


def test_only_main_pages_count_and_withheld_claims_count_nowhere() -> None:
    records = {
        "g1": {"pages": ["a", "b"]},  # main page a, mentions b
        "g2": {"pages": ["a"]},
        "g3": {"pages": ["b"]},
        "g4": {"pages": []},
        "g5": {"pages": ["gone"]},  # a page the plan no longer has
    }
    sizes = page_sizes(records, PLAN, withheld={"g2"})
    assert {page: size["claims"] for page, size in sizes.items()} == {"a": 1, "b": 1}
    assert page_sizes(records, PLAN, withheld=set())["a"]["claims"] == 2


def test_the_overview_flags_every_page_by_its_exact_size(setup: AssignSetup) -> None:
    overview = setup.get("/plan")
    sizes = overview["assignments"]["sizes"]
    assert {page: size["claims"] for page, size in sizes.items()} == {
        "agencies": 3, "costs": 3, "clinics": 3, "hospital": 0}
    size_warnings = [w for w in overview["warnings"] if w["code"].endswith("_page")]
    assert {(w["code"], w["ids"][0], w["tone"]) for w in size_warnings} == {
        ("fold_page", page, "warning") for page in sizes}
    hospital = next(w for w in size_warnings if w["ids"] == ["hospital"])
    assert f"main page of 0 claims, fewer than {GUIDE['fold_below']}" in hospital["text"]
    assert all(w["tone"] == "warning" for w in overview["warnings"])


def test_a_withheld_claim_is_marked_and_not_counted(setup: AssignSetup) -> None:
    claims = claim_records()
    # a lone negative report about something named
    claims[1] = claim_record(1, supporter_count=1, contradicts=claims[1]["contradicts"])
    assert claims[1]["stance"] == "negative" and claims[1]["entity_ids"]
    write_claim_inputs(claims, setup.root)
    assert setup.get("/plan")["assignments"]["sizes"]["costs"]["claims"] == 2
    rows = setup.claims(scope="page", id="costs")["items"]
    assert {row["short_id"]: row["withheld"] for row in rows}[short_id_of(1)] is True
    assert not any(row["withheld"] for row in rows if row["short_id"] != short_id_of(1))


def test_a_page_shows_a_sample_and_the_closest_claims_it_did_not_get(setup: AssignSetup) -> None:
    hospital = setup.get("/pages/hospital")
    assert hospital["size"] == {"claims": 0, "flag": FOLD} and hospital["sample"] == []
    assert [(row["short_id"], row["certainty"]) for row in hospital["near"]] == [
        (short_id_of(2), 0.2), (short_id_of(6), 0.2), (short_id_of(10), 0.2),
        (short_id_of(3), 0.15), (short_id_of(7), 0.15)]  # scope_sample = 5, most probable first
    assert hospital["near"][0]["pages"] == ["clinics"] and hospital["near"][0]["claim_text"]
    agencies = setup.get("/pages/agencies")
    assert [row["short_id"] for row in agencies["sample"]] == [
        short_id_of(0), short_id_of(4), short_id_of(8)]
    assert all(row["role"] == "primary" for row in agencies["sample"])
    assert agencies["near"] == []  # claims that mention it have it, so they are not "near"
