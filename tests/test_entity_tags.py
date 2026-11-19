"""The entity tag rule (``step_11_site.entity_tags``): claims, pages, the plan's coverage, the
owner's decisions and the report. Synthetic names and records only."""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from step_5_same_complaint.claim_records import Record
from step_5_same_complaint.release import withheld_ids
from step_6_plan import operations
from step_6_plan.model import slug_error
from step_11_site.entity_tags import (
    COVERED,
    FEW_PAGES,
    FROM_ASSIGNMENTS,
    FROM_PAGES,
    TAG,
    TAGGED,
    TAGS_CONFIG,
    UNCERTAIN,
    PlanNames,
    TagInputs,
    assigned_claims,
    cited_claims,
    entity_names,
    entity_tags,
    load_decisions,
    report_lines,
    tag_id,
    write_decisions,
)
from tests.claim_fixtures import claim_record, write_claim_inputs
from tests.plan_fixtures import make_plan

MIN_CLAIMS, MIN_PAGES = TAGS_CONFIG["min_claims"], TAGS_CONFIG["min_pages"]


def named(entity_id: str, name: str, start: int, count: int = MIN_CLAIMS, **fields: Any) -> list[Record]:
    """``count`` positive records from number ``start`` on, each naming one entity."""
    return [claim_record(n, entity_ids=[entity_id], entity_names=[name], stance="positive", **fields)
            for n in range(start, start + count)]


def ids(records: Sequence[Record]) -> list[str]:
    return [record["short_id"] for record in records]


def plan_with_label(title: str) -> dict[str, Any]:
    plan = make_plan()
    return operations.add_label(plan, "extra", title_he=title, description="ת", kind="topic").plan


def rule(records: list[Record], page_claims: dict[str, Any], plan: dict[str, Any] | None = None,
         decisions: dict[str, str] | None = None, names: dict[str, list[str]] | None = None) -> Any:
    return entity_tags(records, names or {}, plan or make_plan(), page_claims, decisions or {},
                       withheld_ids(records))


def test_a_tag_needs_enough_claims_and_pages_and_lists_pages_in_the_plans_order() -> None:
    sol = named("e0001", "Clinica Sol", 0)
    few = named("e0002", "Clinica Luna", 10, MIN_CLAIMS - 1)
    lone = named("e0003", "Clinica Mar", 20)
    records = [*sol, *few, *lone]
    cites = {"clinics": set(ids(sol[:1] + few + lone)), "agencies": set(ids(sol[1:2] + few)), "hospital": set()}
    result = rule(records, cites)
    assert [(c.entity_id, c.status, c.pages) for c in result.candidates] == [
        ("e0003", FEW_PAGES, ("clinics",)),
        ("e0001", TAGGED, ("agencies", "clinics")),
    ]
    assert [(tag.tag_id, tag.title) for tag in result.tags] == [("entity_e0001", "Clinica Sol")]
    assert result.claim_counts == {"e0001": MIN_CLAIMS, "e0002": MIN_CLAIMS - 1, "e0003": MIN_CLAIMS}


def test_people_and_every_review_status_count_and_names_come_from_the_entities() -> None:
    payload = {"entities": [
        {"entity_id": "e0001", "canonical_name": "Dr Alpha Person", "status": "suggested", "aliases": ["Alpha"]},
        {"entity_id": "e0002", "canonical_name": "", "status": "ambiguous", "aliases": []},
    ]}
    assert entity_names(payload) == {"e0001": ["Dr Alpha Person", "Alpha"], "e0002": ["e0002"]}


def test_a_withheld_or_left_out_claim_never_puts_a_page_on_a_tag() -> None:
    sol = named("e0001", "Clinica Sol", 0)
    lone_complaint = claim_record(40, entity_ids=["e0001"], entity_names=["Clinica Sol"], stance="negative",
                                  supporter_count=1, opposer_count=0)
    records = [*sol, lone_complaint]
    assert withheld_ids(records) == {lone_complaint["short_id"]}
    pages = {
        "clinics": {"points": [{"kind": "k", "statement": "s", "claims": ids(sol)}], "left_out": []},
        "agencies": {"points": [{"kind": "k", "statement": "s", "claims": [lone_complaint["short_id"]]}]},
        "costs": {"points": [], "left_out": [{"claim": sol[0]["short_id"], "reason": "r"}]},
    }
    result = rule(records, cited_claims(pages))
    assert [(c.status, c.pages) for c in result.candidates] == [(FEW_PAGES, ("clinics",))]
    assert result.claim_counts == {"e0001": MIN_CLAIMS + 1}  # the rule's claim count is every record


def test_pages_outside_the_plan_do_not_count() -> None:
    sol = named("e0001", "Clinica Sol", 0)
    result = rule(sol, {"clinics": set(ids(sol)), "not-planned": set(ids(sol))})
    assert result.candidates[0].pages == ("clinics",)


@pytest.mark.parametrize(
    ("entity", "plan_name", "exact"),
    [
        ("Dr. Sol", "sol", True),  # step 4's normalisation drops the title and the dot
        ("קליניקה סול", "קליניקה סול בקנקון", False),  # whole words inside the longer name
        ("קולומביה", "פונדקאות בקולומביה", False),  # a Hebrew prefix letter on the longer name
        ("מרכז אלפא בחיפה", "אלפא", False),  # the plan's name inside the entity's
    ],
)
def test_a_name_that_equals_or_contains_a_plan_name_matches(entity: str, plan_name: str, exact: bool) -> None:
    [match] = PlanNames(plan_with_label(plan_name)).matches([entity])
    assert (match.kind, match.item_id, match.entity_name, match.plan_name, match.exact) == (
        "label", "extra", entity, plan_name, exact)


@pytest.mark.parametrize(("entity", "plan_name"), [("Sol", "Solana Clinic"), ("ab", "ab cd"), ("לונה", "בלונדון")])
def test_part_of_a_word_or_a_very_short_name_does_not_match(entity: str, plan_name: str) -> None:
    assert PlanNames(plan_with_label(plan_name)).matches([entity]) == []


def test_page_aliases_count_and_an_exact_match_wins_over_a_partial_one() -> None:
    plan = make_plan()  # page agencies has the English alias "agencies", page costs "costs"
    matches = PlanNames(plan).matches(["Best Agencies", "agencies"])
    assert [(m.item_id, m.exact, m.title) for m in matches] == [("agencies", True, "כותרת agencies")]


def test_coverage_and_the_owners_decisions() -> None:
    plan = plan_with_label("סול")
    exact = named("e0001", "Sol", 0)
    partial = named("e0002", "סול קנקון", 10)
    decided_covered = named("e0003", "סול מקסיקו", 20)
    decided_tag = named("e0004", "סול טבליסי", 30)
    free = named("e0005", "Clinica Luna", 40)
    records = [*exact, *partial, *decided_covered, *decided_tag, *free]
    cites = {"clinics": set(ids(records)), "agencies": set(ids(records))}
    names = {"e0001": ["Sol", "סול"]}  # the exact match is through an alias
    result = rule(records, cites, plan, {"e0003": COVERED, "e0004": TAG, "e0001": TAG}, names)
    status = {c.entity_id: c.status for c in result.candidates}
    assert status == {"e0001": COVERED, "e0002": UNCERTAIN, "e0003": COVERED, "e0004": TAGGED, "e0005": TAGGED}
    assert [c.entity_id for c in result.waiting] == ["e0002"]
    assert sorted(c.entity_id for c in result.partial) == ["e0002", "e0003", "e0004"]
    assert result.counts() == {"candidates": 5, FEW_PAGES: 0, COVERED: 2, UNCERTAIN: 1, TAGGED: 2}


def test_a_tag_id_can_never_be_a_plan_id() -> None:
    assert slug_error(tag_id("e0001")) is not None


def test_decisions_round_trip_and_a_wrong_file_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "decisions.json"
    assert load_decisions(path) == {}
    write_decisions({"e0002": TAG, "e0001": COVERED}, path)
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert list(stored["decisions"]) == ["e0001", "e0002"] and stored["metadata"]["decision_count"] == 2
    assert load_decisions(path) == {"e0001": COVERED, "e0002": TAG}
    with pytest.raises(ValueError, match="covered"):
        write_decisions({"e0001": "maybe"}, path)
    path.write_text(json.dumps({"decisions": {"e0001": "maybe"}}), encoding="utf-8")
    with pytest.raises(ValueError, match="not the entity tag decisions file"):
        load_decisions(path)


def test_the_assignments_estimate_the_pages() -> None:
    assignments = {"assignments": {"g-1": {"pages": ["clinics", "costs"], "labels": []},
                                   "g-2": {"pages": [], "labels": ["usa"]}}}
    assert assigned_claims(assignments) == {"clinics": {"g-1"}, "costs": {"g-1"}}


def _inputs(tmp_path: Path, records: list[Record], pages: dict[str, Any] | None) -> TagInputs:
    sources = write_claim_inputs(records, tmp_path)
    plan_path, pages_path, assignments_path = (tmp_path / name for name in ("plan.json", "pages.json", "a.json"))
    plan_path.write_text(json.dumps(make_plan()), encoding="utf-8")
    assignments = {"assignments": {sid: {"pages": ["clinics", "costs"], "labels": []} for sid in ids(records)}}
    assignments_path.write_text(json.dumps(assignments), encoding="utf-8")
    if pages is not None:
        pages_path.write_text(json.dumps({"pages": pages, "metadata": {}}), encoding="utf-8")
    return TagInputs(sources, plan_path, pages_path, assignments_path, tmp_path / "decisions.json")


def test_the_report_estimates_from_the_assignments_before_pages_exist(tmp_path: Path) -> None:
    records = [*named("e0001", "Clinica Sol", 0), *named("e0002", "Clinica Luna", 10, 2)]
    data = _inputs(tmp_path, records, None).load()
    assert data.source == FROM_ASSIGNMENTS
    lines = list(report_lines(data.source, data.result()))
    assert "ESTIMATED from the assignments" in lines[len(TAGS_CONFIG["report_min_claims"]) + 1]
    assert f"  N = {MIN_CLAIMS:>4}: 1 entities" in lines
    assert "  N =    2: 2 entities" in lines
    assert lines[-1].split() == ["tagged:", "1"]
    assert not any("Clinica" in line for line in lines)  # ids and numbers only


def test_the_report_reads_the_written_pages_when_there_are_some(tmp_path: Path) -> None:
    records = named("e0001", "Clinica Sol", 0)
    pages = {"clinics": {"points": [{"kind": "k", "statement": "s", "claims": ids(records)}]}}
    data = _inputs(tmp_path, records, pages).load()
    assert (data.source, data.result().counts()[FEW_PAGES]) == (FROM_PAGES, 1)


def test_main_logs_the_report(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
                              tmp_path: Path) -> None:
    from step_11_site import entity_tags as module

    inputs = _inputs(tmp_path, named("e0001", "Clinica Sol", 0), None)
    monkeypatch.setattr(module, "TagInputs", lambda: inputs)
    with caplog.at_level(logging.INFO, logger=module.__name__):
        module.main(["--report"])
    assert any("tagged:" in record.getMessage() for record in caplog.records)
