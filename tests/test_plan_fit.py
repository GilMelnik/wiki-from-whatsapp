"""Step 6 on any number of groups: the full-list prompt is pinned byte for byte, and a list
over ``plan.max_input_tokens`` becomes a sample that fits (``step_6_plan.fit``). Synthetic
groups and fake models only; no model or endpoint is called."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from step_6_plan import coverage
from step_6_plan.fit import PlanPrompt, backed_by_many, fitted_prompt
from step_6_plan.model import PLAN_CONFIG
from step_6_plan.prompt import group_line, scaled_guide
from step_6_plan.run import run
from tests.plan_fixtures import PAGE_IDS, FakeClassifier, FakeLLM, make_groups, make_proposal
from utils.json_io import write_json_file
from utils.paths import Artifact

# sha256 of the (task, system, user) of the plan, retry and revision calls below, taken on v2
# at 6606e7c, before the prompt could list a sample: a full list must keep this prompt exactly.
FULL_LIST_PROMPTS_SHA256 = "440b44bb2521b7fd6d781f3893c806e87afa3c0e0c1d32bd0a980888b61cfeaf"


def _spread_pick(state: str) -> dict[str, float]:
    page = PAGE_IDS[int(state.split("topic ")[1][0])]
    return {**{p: 0.0 for p in PAGE_IDS}, coverage.NONE: 0.0, page: 0.9}


def _nowhere(state: str) -> dict[str, float]:
    return {**{p: 0.0 for p in PAGE_IDS}, coverage.NONE: 0.9}


def _run(tmp_path: Path, groups: list[dict[str, Any]], llm: FakeLLM, pick: Any) -> dict[str, Any]:
    groups_path = tmp_path / "groups.json"
    write_json_file({"groups": groups, "metadata": {}}, groups_path)
    artifact = Artifact(tmp_path / "plan.json", tmp_path / "plan_edited.json")
    result = run(aggregated_path=groups_path, artifact=artifact, llm=llm,
                 classifier=FakeClassifier(pick), count_tokens=lambda system, user: 1000)
    return {**result, "plan": json.loads(artifact.edited.read_text(encoding="utf-8"))}


def _dated_groups(count: int = 400) -> list[dict[str, Any]]:
    """``count`` groups over four years in time order; every fourth one backed by three members."""

    groups = make_groups(count)
    for index, group in enumerate(groups):
        month = index * 48 // count
        group["first_date"] = group["last_date"] = f"{2022 + month // 12}-{month % 12 + 1:02d}-01"
        group["supporter_count"] = 3 if index % 4 == 0 else index % 2
    return groups


def _budget_between(monkeypatch: pytest.MonkeyPatch, low: PlanPrompt, high: PlanPrompt) -> int:
    budget = (low.estimated_tokens + high.estimated_tokens) // 2
    monkeypatch.setitem(PLAN_CONFIG, "max_input_tokens", budget)
    return budget


def test_a_full_list_keeps_todays_prompts_byte_for_byte(tmp_path: Path) -> None:
    bad = make_proposal()
    bad["pages"][0]["parent_id"] = "before"
    llm = FakeLLM([bad, make_proposal(), make_proposal()])
    result = _run(tmp_path, make_groups(), llm, _spread_pick)  # every page is small: a revision
    calls = [[call["task"], call["system"], call["user"]] for call in llm.calls]
    assert [call[0] for call in calls] == ["plan", "plan-retry", "plan-revise"]
    digest = hashlib.sha256(json.dumps(calls, ensure_ascii=False).encode()).hexdigest()
    assert digest == FULL_LIST_PROMPTS_SHA256
    meta = result["plan"]["metadata"]
    assert meta["group_count"] == meta["shown_group_count"] == 12
    assert meta["sample_seed"] == PLAN_CONFIG["sample_seed"]


def test_over_the_budget_every_many_backed_group_is_in_and_the_rest_is_spread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    groups = _dated_groups()
    many = [group for group in groups if backed_by_many(group)]
    budget = _budget_between(monkeypatch, PlanPrompt(many, groups), PlanPrompt(groups, groups))
    prompt = fitted_prompt(groups)
    shown_ids = [group["group_id"] for group in prompt.shown]
    assert shown_ids == [g["group_id"] for g in groups if g["group_id"] in set(shown_ids)]
    assert all(group in prompt.shown for group in many)
    singles = [group for group in prompt.shown if not backed_by_many(group)]
    assert 0 < len(singles) < len(groups) - len(many)
    line_tokens = max(len(group_line(g)) + 1 for g in groups) / PLAN_CONFIG["chars_per_token"]
    assert budget - 2 * line_tokens < prompt.estimated_tokens <= budget  # full to one group
    per_year = [sum(1 for g in singles if g["first_date"].startswith(str(year)))
                for year in range(2022, 2026)]
    assert max(per_year) - min(per_year) <= 2, per_year
    assert [g["group_id"] for g in fitted_prompt(groups).shown] == shown_ids
    monkeypatch.setitem(PLAN_CONFIG, "sample_seed", PLAN_CONFIG["sample_seed"] + 1)
    assert [g["group_id"] for g in fitted_prompt(groups).shown] != shown_ids


def test_a_sample_says_so_with_the_right_numbers_and_a_scaled_guide(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    groups = _dated_groups()
    many = [group for group in groups if backed_by_many(group)]
    _budget_between(monkeypatch, PlanPrompt(many, groups), PlanPrompt(groups, groups))
    prompt = fitted_prompt(groups)
    shown, total, rest = len(prompt.shown), len(groups), len(groups) - len(many)
    text = " ".join(prompt.user("REQUEST").split())
    assert (f"it shows {shown} of all {total} claim groups, namely all {len(many)} groups that more "
            f"than one member backs and {shown - len(many)} of the {rest} groups that fewer than two "
            "members back") in text
    assert "resemble the shown ones in subject and in proportion" in text
    assert text.index("resemble") < text.index("<claim_groups") < text.index("REQUEST")
    system = " ".join(prompt.system.split())
    guide = PLAN_CONFIG["page_groups"]
    scaled = {key: max(1, round(value * shown / total)) for key, value in guide.items()}
    assert scaled_guide(shown, total) == scaled and scaled != guide
    assert f"rests on roughly {scaled['typical_min']} to {scaled['typical_max']} claim groups" in system
    assert f"more than about {scaled['split_above']} groups" in system
    assert f"fewer than about {scaled['fold_below']} groups" in system
    assert f"which shows {shown} of all {total} groups" in system
    assert f"(roughly {guide['typical_min']} to {guide['typical_max']} groups" in system
    assert "lists a sample of the claim groups" in system
    full = PlanPrompt(groups, groups)
    assert "sample" not in full.system and "sample" not in full.groups_text


def test_when_the_many_backed_groups_alone_are_over_the_budget_they_are_sampled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    groups = _dated_groups()
    many = [group for group in groups if backed_by_many(group)]
    budget = _budget_between(monkeypatch, PlanPrompt(many[: len(many) // 3], groups),
                             PlanPrompt(many, groups))
    prompt = fitted_prompt(groups)
    assert prompt.shown and all(backed_by_many(group) for group in prompt.shown)
    assert len(prompt.shown) < len(many) and prompt.estimated_tokens <= budget
    years = {group["first_date"][:4] for group in prompt.shown}
    assert years == {"2022", "2023", "2024", "2025"}
    assert (f"{len(prompt.shown)} of the {len(many)} groups that more than one member backs and "
            f"0 of the {len(groups) - len(many)} groups") in " ".join(prompt.groups_text.split())


def test_a_budget_below_the_instructions_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    groups = _dated_groups(40)
    monkeypatch.setitem(PLAN_CONFIG, "max_input_tokens", PlanPrompt([], groups).estimated_tokens)
    with pytest.raises(ValueError, match="does not hold the planning instructions"):
        fitted_prompt(groups)


def test_the_guide_scales_but_never_below_one() -> None:
    guide = PLAN_CONFIG["page_groups"]
    assert scaled_guide(50, 50) == guide
    assert set(scaled_guide(1, 10_000).values()) == {1}


def test_a_sampled_run_records_it_and_checks_coverage_and_revises_on_all_groups(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    groups = _dated_groups()
    many = [group for group in groups if backed_by_many(group)]
    _budget_between(monkeypatch, PlanPrompt(many, groups), PlanPrompt(groups, groups))
    shown_ids = {group["group_id"] for group in fitted_prompt(groups).shown}
    llm = FakeLLM([make_proposal(), make_proposal()])
    result = _run(tmp_path, groups, llm, _nowhere)
    meta = result["plan"]["metadata"]
    assert (meta["group_count"], meta["shown_group_count"]) == (len(groups), len(shown_ids))
    assert meta["sample_seed"] == PLAN_CONFIG["sample_seed"]
    assert meta["planned_group_ids"] == sorted(group["group_id"] for group in groups)
    report = result["coverage"]
    assert report["total_groups"] == len(groups)
    assert set(report["none_group_ids"]) - shown_ids  # the check also placed groups not listed
    assert [call["task"] for call in llm.calls] == ["plan", "plan-revise"]
    first, revision = (call["user"] for call in llm.calls)
    assert first.startswith("Not every claim group fits")
    listed, extra = revision.split("The groups named here that the list does not show:\n")
    extra_ids = {line.split(" | ")[0] for line in extra.split("\n\n")[0].splitlines()}
    assert extra_ids == set(report["none_group_ids"]) - shown_ids
    block = listed.split("<claim_groups")[1].split("</claim_groups>")[0]
    assert extra_ids and not any(group_id in block for group_id in extra_ids)
