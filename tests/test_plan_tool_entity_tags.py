"""The Plan page's entity tags: the numbers, the entities waiting for the owner, the decisions
(synthetic records; the names are made up)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from step_6_plan.claim_records import Record
from step_9_site.entity_tags import TAGS_CONFIG
from tests.claim_fixtures import claim_record, write_claim_inputs
from tests.test_plan_tool import PlanSetup
from utils.json_io import write_json_file

MIN_CLAIMS = TAGS_CONFIG["min_claims"]


def _named(entity_id: str, name: str, start: int) -> list[Record]:
    return [claim_record(n, entity_ids=[entity_id], entity_names=[name], stance="positive")
            for n in range(start, start + MIN_CLAIMS)]


@pytest.fixture
def setup(tmp_path: Path) -> PlanSetup:
    """Entity e0001 is free; e0002's name holds the plan's label "גאורגיה": it waits. Every claim
    is assigned to clinics and costs; no page is written yet."""
    setup = PlanSetup(tmp_path)
    records = [*_named("e0001", "Clinica Sol", 0), *_named("e0002", "מרפאה בגאורגיה", 10)]
    write_claim_inputs(records, tmp_path)
    assignments = {r["short_id"]: {"pages": ["clinics", "costs"], "labels": []} for r in records}
    write_json_file({"assignments": assignments, "proposals": [], "metadata": {}}, tmp_path / "assignments.json")
    return setup


def test_the_view_shows_the_numbers_and_the_entity_to_decide(setup: PlanSetup) -> None:
    view = setup.get("/entity-tags")
    assert (view["problem"], view["source"]) == (None, "assignments")
    assert view["thresholds"] == {"min_claims": MIN_CLAIMS, "min_pages": TAGS_CONFIG["min_pages"]}
    assert view["counts"] == {"candidates": 2, "few_pages": 0, "covered": 0, "uncertain": 1, "tagged": 1}
    assert {"min_claims": MIN_CLAIMS, "entities": 2} in view["table"]
    assert view["entities"] == [{
        "entity_id": "e0002",
        "name": "מרפאה בגאורגיה",
        "names": ["מרפאה בגאורגיה"],
        "claim_count": MIN_CLAIMS,
        "pages": [{"id": "costs", "title": "כותרת costs"}, {"id": "clinics", "title": "כותרת clinics"}],
        "matches": [{"kind": "label", "id": "georgia", "title": "גאורגיה", "entity_name": "מרפאה בגאורגיה",
                     "plan_name": "גאורגיה"}],
        "decision": None,
    }]
    assert "SECRET" not in json.dumps(view)


def test_a_decision_is_saved_changed_and_forgotten(setup: PlanSetup) -> None:
    decisions = setup.root / "entity_tag_decisions.json"
    view = setup.post("/entity-tags/e0002", {"decision": "tag"})
    assert view["counts"]["tagged"] == 2 and view["entities"][0]["decision"] == "tag"
    assert json.loads(decisions.read_text(encoding="utf-8"))["decisions"] == {"e0002": "tag"}
    view = setup.post("/entity-tags/e0002", {"decision": "covered"})
    assert (view["counts"]["covered"], view["counts"]["tagged"]) == (1, 1)
    assert setup.new_client().get("/api/plan/entity-tags").json()["entities"][0]["decision"] == "covered"
    view = setup.post("/entity-tags/e0002", {"decision": None})
    assert view["counts"]["uncertain"] == 1
    assert json.loads(decisions.read_text(encoding="utf-8"))["decisions"] == {}


def test_only_an_entity_with_a_partial_match_takes_a_decision(setup: PlanSetup) -> None:
    refused = setup.post("/entity-tags/e0001", {"decision": "covered"}, status=409)
    assert "does not wait for a decision" in refused["detail"]
    setup.post("/entity-tags/e0002", {"decision": "maybe"}, status=422)


def test_without_a_plan_the_view_says_why(tmp_path: Path) -> None:
    setup = PlanSetup(tmp_path, with_plan=False)
    view = setup.get("/entity-tags")
    assert view["problem"].startswith("The entity tags cannot be computed yet")
    setup.post("/entity-tags/e0001", {"decision": "tag"}, status=409)
