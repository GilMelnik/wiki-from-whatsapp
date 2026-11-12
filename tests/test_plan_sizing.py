"""Step 6 page sizing: the prompt's size guide, the safety ceiling, the prompt version and the
coverage flags for pages outside the guide. No model is called anywhere."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from step_6_plan import coverage
from step_6_plan.model import PLAN_CONFIG, check_plan, validation_errors
from step_6_plan.prompt import plan_system
from step_6_plan.run import run
from tests.plan_fixtures import PAGE_IDS, FakeClassifier, FakeLLM, make_groups, make_plan, make_proposal
from utils.json_io import write_json_file
from utils.paths import Artifact

GUIDE = PLAN_CONFIG["page_groups"]
PLAN_SYSTEM = plan_system(100, 100)  # the instructions for a full list


def test_prompt_carries_the_guide_from_config_and_no_page_count() -> None:
    pages_rule = next(line for line in PLAN_SYSTEM.split("\n- ") if line.startswith("Pages:"))
    pages_rule = " ".join(pages_rule.split())
    assert f"roughly {GUIDE['typical_min']} to {GUIDE['typical_max']} claim groups" in pages_rule
    assert f"more than about {GUIDE['split_above']} groups" in pages_rule
    assert f"fewer than about {GUIDE['fold_below']} groups" in pages_rule
    assert "no target number of pages" in pages_rule
    assert not re.search(r"at most \d+ (?:wiki )?pages", PLAN_SYSTEM)
    assert str(PLAN_CONFIG["max_pages"]) not in PLAN_SYSTEM


def test_guide_numbers_are_ordered_and_the_ceiling_is_a_ceiling() -> None:
    assert GUIDE["fold_below"] < GUIDE["typical_min"] < GUIDE["typical_max"] < GUIDE["split_above"]
    assert PLAN_CONFIG["max_pages"] == 80


def test_prompt_labels_are_facets_and_never_for_contact_details() -> None:
    labels = " ".join(PLAN_SYSTEM.split("## Labels")[1].split("## The input")[0].split())
    assert "describes a facet of the subject" in labels
    assert "never exists to find private or contact details" in labels


def test_plan_above_the_ceiling_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    plan = make_plan()
    assert validation_errors(plan) == []
    monkeypatch.setitem(PLAN_CONFIG, "max_pages", len(plan["pages"]) - 1)
    assert any("safety ceiling" in error for error in validation_errors(plan))


def test_a_plan_made_under_the_30_page_config_still_loads_and_validates() -> None:
    plan = make_plan()
    plan["metadata"] = {"model": "old", "config": {"max_pages": 30}, "planned_group_ids": []}
    assert check_plan(plan) is plan


def test_a_planning_run_records_the_prompt_version(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in {"fold_below": 2, "split_above": 8}.items():
        monkeypatch.setitem(GUIDE, key, value)
    groups_path = tmp_path / "groups.json"
    write_json_file({"groups": make_groups(), "metadata": {}}, groups_path)
    artifact = Artifact(tmp_path / "plan.json", tmp_path / "plan_edited.json")

    def spread(state: str) -> dict[str, float]:
        page = PAGE_IDS[int(state.split("topic ")[1][0])]
        return {**{p: 0.0 for p in PAGE_IDS}, coverage.NONE: 0.0, page: 0.9}

    run(aggregated_path=groups_path, artifact=artifact, llm=FakeLLM([make_proposal()]),
        classifier=FakeClassifier(spread), count_tokens=lambda system, user: 1000)
    meta = json.loads(artifact.edited.read_text(encoding="utf-8"))["metadata"]
    assert meta["prompt_version"] == PLAN_CONFIG["prompt_version"] and meta["prompt_version"] >= 2
    assert meta["config"]["page_groups"] == GUIDE


def _report(projected: dict[str, int]) -> dict[str, Any]:
    """The coverage numbers when the "sample" is every group: ``projected`` groups per page."""

    zero = {**{p: 0.0 for p in PAGE_IDS}, coverage.NONE: 0.0}
    picks = [(f"{pid}{i}", {**zero, pid: 0.9}) for pid, size in projected.items() for i in range(size)]
    return coverage.summarize(make_plan(), picks, len(picks))


def test_coverage_flags_pages_outside_the_guide_only() -> None:
    sizes = {"agencies": GUIDE["split_above"] + 80, "costs": GUIDE["typical_min"] + 10,
             "clinics": GUIDE["typical_max"] + 10, "hospital": GUIDE["fold_below"] - 10}
    report = _report(sizes)
    assert report["large_pages"] == ["agencies"]
    assert report["small_pages"] == ["hospital"]


def test_a_page_exactly_on_a_limit_is_not_flagged() -> None:
    padding = GUIDE["typical_min"]
    report = _report({"agencies": GUIDE["split_above"], "costs": GUIDE["fold_below"],
                      "clinics": padding, "hospital": padding})
    assert report["large_pages"] == [] and report["small_pages"] == []


def test_findings_name_the_guide_and_the_action() -> None:
    plan = make_plan()
    report = _report({"agencies": 300, "costs": 60, "clinics": 60, "hospital": 5})
    found = coverage.findings(report, plan)
    assert any("'agencies'" in f and "more than " + str(GUIDE["split_above"]) in f
               and "split it by sub-subject" in f for f in found)
    assert any("'hospital'" in f and "fewer than " + str(GUIDE["fold_below"]) in f
               and "neighbouring page" in f for f in found)
