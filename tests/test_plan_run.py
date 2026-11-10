"""Step 6 planning: prompt, flat-to-tree conversion, validation, retry, run modes, coverage."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from step_6_plan import coverage
from step_6_plan.model import PLAN_CONFIG, PlanError, check_plan, validation_errors
from step_6_plan.prompt import groups_block, plan_from_proposal, proposal_from_plan
from step_6_plan.run import run
from tests.plan_fixtures import (
    PAGE_IDS,
    FakeClassifier,
    FakeLLM,
    make_groups,
    make_plan,
    make_proposal,
)
from utils.json_io import write_json_file
from utils.paths import BACKUPS_DIR, Artifact

GROUP_IDS = {g["group_id"] for g in make_groups()}


def _even_pick(state: str) -> dict[str, float]:
    """Spread groups over the pages by the topic number in their synthetic text."""

    page = PAGE_IDS[int(state.split("topic ")[1][0])]
    return {**{p: 0.0 for p in PAGE_IDS}, coverage.NONE: 0.0, page: 0.9}


@pytest.fixture
def setup(tmp_path: Path) -> dict[str, Any]:
    groups_path = tmp_path / "groups.json"
    write_json_file({"groups": make_groups(), "metadata": {}}, groups_path)
    artifact = Artifact(tmp_path / f"{tmp_path.name}.json", tmp_path / f"{tmp_path.name}_edited.json")
    return {"groups": groups_path, "artifact": artifact}


def _run(setup: dict[str, Any], llm: FakeLLM, *, tokens: int = 1000, rebuild: bool = False,
         pick: Any = _even_pick) -> dict[str, Any]:
    return run(aggregated_path=setup["groups"], artifact=setup["artifact"], rebuild=rebuild,
               llm=llm, classifier=FakeClassifier(pick), count_tokens=lambda s, u: tokens)


def test_prompt_lists_every_group_once_and_nothing_about_people() -> None:
    groups = make_groups()
    text = groups_block(groups)
    for group in groups:
        assert text.count(group["group_id"]) == 1
        assert group["claim_text"] in text
    assert "SECRET" not in text and "by_month" not in text and "e0001" not in text
    assert "Entity1" in text and "+1 -1" in text and "2023-01..2024-06" in text
    assert "2 threads" in text


def test_flat_proposal_becomes_a_tree_with_paths() -> None:
    plan = make_plan()
    assert [n["id"] for n in plan["tree"]] == ["before", "during"]
    choosing = plan["tree"][0]["children"][0]
    assert [c["id"] for c in choosing["children"]] == ["agencies", "costs"]
    assert choosing["children"][0]["title_he"] == plan["pages"]["agencies"]["title_he"]
    assert plan["pages"]["clinics"]["path"] == ["during", "medical"]
    assert plan["pages"]["agencies"]["aliases"] == {"he": ["כינוי agencies"], "en": ["agencies"]}
    assert plan["pages"]["agencies"]["example_group_ids"] == ["g-00000000"]  # unknown id dropped
    assert plan["labels"]["usa"]["kind"] == "country"
    assert check_plan(plan) is plan
    assert plan_from_proposal(proposal_from_plan(plan), GROUP_IDS)[0] == plan


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (lambda p: p["pages"][0].update(parent_id="before"), "sits at depth 2"),
        (lambda p: p["pages"][0].update(parent_id="costs"), "not a category node"),
        (lambda p: p["pages"][0].update(id="Bad Slug"), "is not a slug"),
        (lambda p: p["pages"][1].update(id="agencies"), "used more than once"),
        (lambda p: p["labels"][0].update(id="clinics"), "used 2 times"),
        (lambda p: p["nodes"][2].update(parent_id="choosing"), "loops"),
        (lambda p: p["pages"][0].update(scope=" "), "non-empty text 'scope'"),
        (lambda p: p["labels"][0].update(kind="Country X"), "kind"),
    ],
)
def test_validation_catches_broken_proposals(change: Any, expected: str) -> None:
    proposal = make_proposal()
    change(proposal)
    _, errors = plan_from_proposal(proposal, GROUP_IDS)
    assert any(expected in error for error in errors), errors


def test_validation_caps_the_page_count(monkeypatch: pytest.MonkeyPatch) -> None:
    plan = make_plan()
    monkeypatch.setitem(PLAN_CONFIG, "max_pages", 3)
    assert any("at most 3" in e for e in validation_errors(plan))


def test_validation_catches_a_leaf_out_of_sync_and_a_retired_id() -> None:
    plan = make_plan()
    plan["tree"][0]["children"][0]["children"][0]["title_he"] = "other"
    plan["metadata"]["retired_ids"] = ["costs"]
    errors = validation_errors(plan)
    assert any("does not match" in e for e in errors)
    assert any("'costs' is retired" in e for e in errors)


def test_invalid_answer_gets_one_retry_with_the_errors(setup: dict[str, Any]) -> None:
    bad = make_proposal()
    bad["pages"][0]["parent_id"] = "before"
    llm = FakeLLM([bad, make_proposal()])
    result = _run(setup, llm)
    assert result["written"] and result["page_count"] == 4
    assert [c["task"] for c in llm.calls] == ["plan", "plan-retry"]
    assert all(c["user"].count("g-00000005") == 1 for c in llm.calls)
    assert "sits at depth 2" in llm.calls[1]["user"]
    assert check_plan(json.loads(setup["artifact"].edited.read_text(encoding="utf-8")))


def test_invalid_twice_raises_and_writes_nothing(setup: dict[str, Any]) -> None:
    bad = make_proposal()
    bad["pages"][0]["id"] = "Bad"
    llm = FakeLLM([bad, bad])
    with pytest.raises(PlanError, match="failed validation twice"):
        _run(setup, llm)
    assert len(llm.calls) == 2 and not setup["artifact"].edited.exists()


def test_token_guard_refuses_before_any_call(setup: dict[str, Any]) -> None:
    llm = FakeLLM([make_proposal()])
    too_many = PLAN_CONFIG["context_window_tokens"] - PLAN_CONFIG["max_output_tokens"] + 1
    with pytest.raises(ValueError, match="does not fit"):
        _run(setup, llm, tokens=too_many)
    assert llm.calls == []


def test_run_on_an_existing_plan_makes_no_call_and_changes_nothing(setup: dict[str, Any]) -> None:
    _run(setup, FakeLLM([make_proposal()]))
    edited = setup["artifact"].edited
    before = (edited.read_bytes(), edited.stat().st_mtime_ns)
    llm = FakeLLM([])
    result = _run(setup, llm)
    assert result == {**result, "written": False, "model_calls": 0, "unplanned_groups": 0}
    assert llm.calls == [] and (edited.read_bytes(), edited.stat().st_mtime_ns) == before


def test_run_refuses_an_invalid_existing_plan(setup: dict[str, Any]) -> None:
    write_json_file({"pages": [{"id": "old-shape"}]}, setup["artifact"].original)
    with pytest.raises(PlanError, match="--rebuild"):
        _run(setup, FakeLLM([]))


def test_rebuild_backs_up_the_edited_plan(setup: dict[str, Any]) -> None:
    _run(setup, FakeLLM([make_proposal()]))
    edited = setup["artifact"].edited
    curated = json.loads(edited.read_text(encoding="utf-8"))
    curated["pages"]["costs"]["scope"] = "hand-edited scope"
    write_json_file(curated, edited)
    result = _run(setup, FakeLLM([make_proposal()]), rebuild=True)
    backup = Path(result["backup"])
    assert backup.parent == BACKUPS_DIR and backup.name.startswith(edited.stem)
    assert json.loads(backup.read_text(encoding="utf-8"))["pages"]["costs"]["scope"] == "hand-edited scope"
    assert json.loads(edited.read_text(encoding="utf-8"))["pages"]["costs"]["scope"] != "hand-edited scope"


def test_planning_run_writes_metadata_and_coverage(setup: dict[str, Any]) -> None:
    result = _run(setup, FakeLLM([make_proposal()]))
    plan = json.loads(setup["artifact"].edited.read_text(encoding="utf-8"))
    assert plan == json.loads(setup["artifact"].original.read_text(encoding="utf-8"))
    meta = plan["metadata"]
    assert meta["model_calls"] == 1 and meta["input_tokens"] == [1000]
    assert meta["planned_group_ids"] == sorted(GROUP_IDS)
    assert meta["top_level_rationale"] == "Phases of the process."
    assert result["coverage"]["confident_share"] == 1.0 and result["coverage"]["findings"] == []


def test_coverage_findings_trigger_one_revision(setup: dict[str, Any]) -> None:
    def everything_on_agencies(state: str) -> dict[str, float]:
        return {**{p: 0.0 for p in PAGE_IDS}, coverage.NONE: 0.0, "agencies": 0.9}

    revised = make_proposal()
    revised["pages"][1]["scope"] = "a sharper scope"
    llm = FakeLLM([make_proposal(), revised])
    result = _run(setup, llm, pick=everything_on_agencies)
    assert [c["task"] for c in llm.calls] == ["plan", "plan-revise"]
    assert "'agencies'" in llm.calls[1]["user"] and "too broad" in llm.calls[1]["user"]
    assert result["coverage"]["revision"]["kept"] is True
    plan = json.loads(setup["artifact"].edited.read_text(encoding="utf-8"))
    assert plan["pages"]["costs"]["scope"] == "a sharper scope"


def test_coverage_arithmetic() -> None:
    plan = make_plan()
    zero = {**{p: 0.0 for p in PAGE_IDS}, coverage.NONE: 0.0}
    picks = (
        [(f"a{i}", {**zero, "agencies": 0.9}) for i in range(6)]
        + [("b0", {**zero, "costs": 0.5, "clinics": 0.4})]
        + [("c0", {**zero, "clinics": 0.8}), ("n0", {**zero, coverage.NONE: 0.7}),
           ("n1", {**zero, coverage.NONE: 0.9})]
    )
    report = coverage.summarize(plan, picks, total_groups=100)
    assert report["sample_size"] == 10
    assert report["confident_share"] == pytest.approx(0.7)
    assert report["none_share"] == pytest.approx(0.2)
    assert report["unsure_share"] == pytest.approx(0.1)
    assert report["page_counts"] == {"agencies": 6, "costs": 1, "clinics": 1, "hospital": 0}
    assert report["projected_groups"] == {"agencies": 60, "costs": 10, "clinics": 10, "hospital": 0}
    assert report["small_pages"] == ["hospital"]  # 0 < 0.25 * 1/4; costs at 0.1 is not
    assert report["large_pages"] == []  # 0.6 is not above 3 * 1/4
    assert report["none_group_ids"] == ["n0", "n1"] and report["unsure_group_ids"] == ["b0"]
    found = coverage.findings(report, plan)
    assert any("30% of the sample" in f for f in found) and any("'hospital'" in f for f in found)


def test_coverage_question_uses_titles_and_scopes_only() -> None:
    plan = make_plan()
    question = coverage.page_question(plan)
    assert question.options == (*PAGE_IDS, coverage.NONE)
    assert plan["pages"]["costs"]["scope"] in question.text
    assert plan["pages"]["costs"]["description"] not in question.text
    sample = coverage.sample_groups(make_groups(500))
    assert len(sample) == coverage.COVERAGE_CONFIG["sample_size"]
    assert sample == coverage.sample_groups(make_groups(500))
