"""Step 6 on any number of claims: the full-list prompt is pinned byte for byte, and a list
over ``plan.max_input_tokens`` becomes a sample that fits (``step_6_plan.fit``). Synthetic
claims and fake models only; no model or endpoint is called."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from step_6_plan import coverage
from step_6_plan.fit import PlanPrompt, backed_by_many, fitted_prompt
from step_6_plan.model import PLAN_CONFIG
from step_5_same_complaint.claim_records import Record
from step_6_plan.prompt import claim_line, scaled_guide
from step_6_plan.run import run
from tests.claim_fixtures import claim_record, claim_records, write_claim_inputs
from tests.plan_fixtures import PAGE_IDS, FakeClassifier, FakeLLM, make_proposal
from utils.paths import Artifact

# sha256 of the (task, system, user) of the plan, retry and revision calls below, taken on
# task/71b-switch-to-claims when the prompt switched from claims to claims
# (plan.prompt_version 3): any change to the full-list prompt must bump that version.
FULL_LIST_PROMPTS_SHA256 = "e698cbdf5d795dc83a09c541881ae3e987d8b9b6d5c85a8a5f3f55a3c545c126"


def _spread_pick(state: str) -> dict[str, float]:
    page = PAGE_IDS[int(state.split("topic ")[1][0])]
    return {**{p: 0.0 for p in PAGE_IDS}, coverage.NONE: 0.0, page: 0.9}


def _nowhere(state: str) -> dict[str, float]:
    return {**{p: 0.0 for p in PAGE_IDS}, coverage.NONE: 0.9}


def _run(tmp_path: Path, claims: list[Record], llm: FakeLLM, pick: Any) -> dict[str, Any]:
    sources = write_claim_inputs(claims, tmp_path)
    artifact = Artifact(tmp_path / "plan.json", tmp_path / "plan_edited.json")
    result = run(sources=sources, artifact=artifact, llm=llm,
                 classifier=FakeClassifier(pick), count_tokens=lambda system, user: 1000)
    return {**result, "plan": json.loads(artifact.edited.read_text(encoding="utf-8"))}


def _dated_claims(count: int = 400) -> list[Record]:
    """``count`` claims over four years in time order; every fourth one backed by three members."""

    claims = []
    for index in range(count):
        month = index * 48 // count
        date = f"{2022 + month // 12}-{month % 12 + 1:02d}-01"
        claims.append(claim_record(index, first_date=date, last_date=date,
                                   supporter_count=3 if index % 4 == 0 else index % 2))
    return claims


def _budget_between(monkeypatch: pytest.MonkeyPatch, low: PlanPrompt, high: PlanPrompt) -> int:
    budget = (low.estimated_tokens + high.estimated_tokens) // 2
    monkeypatch.setitem(PLAN_CONFIG, "max_input_tokens", budget)
    return budget


def test_a_full_list_keeps_todays_prompts_byte_for_byte(tmp_path: Path) -> None:
    bad = make_proposal()
    bad["pages"][0]["parent_id"] = "before"
    llm = FakeLLM([bad, make_proposal(), make_proposal()])
    result = _run(tmp_path, claim_records(), llm, _spread_pick)  # every page is small: a revision
    calls = [[call["task"], call["system"], call["user"]] for call in llm.calls]
    assert [call[0] for call in calls] == ["plan", "plan-retry", "plan-revise"]
    digest = hashlib.sha256(json.dumps(calls, ensure_ascii=False).encode()).hexdigest()
    assert digest == FULL_LIST_PROMPTS_SHA256
    meta = result["plan"]["metadata"]
    assert meta["claim_count"] == meta["shown_claim_count"] == 12
    assert meta["sample_seed"] == PLAN_CONFIG["sample_seed"]


def test_over_the_budget_every_many_backed_claim_is_in_and_the_rest_is_spread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claims = _dated_claims()
    many = [claim for claim in claims if backed_by_many(claim)]
    budget = _budget_between(monkeypatch, PlanPrompt(many, claims), PlanPrompt(claims, claims))
    prompt = fitted_prompt(claims)
    shown_ids = [claim["short_id"] for claim in prompt.shown]
    assert shown_ids == [c["short_id"] for c in claims if c["short_id"] in set(shown_ids)]
    assert all(claim in prompt.shown for claim in many)
    singles = [claim for claim in prompt.shown if not backed_by_many(claim)]
    assert 0 < len(singles) < len(claims) - len(many)
    line_tokens = max(len(claim_line(c)) + 1 for c in claims) / PLAN_CONFIG["chars_per_token"]
    assert budget - 2 * line_tokens < prompt.estimated_tokens <= budget  # full to one claim
    per_year = [sum(1 for c in singles if c["first_date"].startswith(str(year)))
                for year in range(2022, 2026)]
    assert max(per_year) - min(per_year) <= 2, per_year
    assert [c["short_id"] for c in fitted_prompt(claims).shown] == shown_ids
    monkeypatch.setitem(PLAN_CONFIG, "sample_seed", PLAN_CONFIG["sample_seed"] + 1)
    assert [c["short_id"] for c in fitted_prompt(claims).shown] != shown_ids


def test_a_sample_says_so_with_the_right_numbers_and_a_scaled_guide(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claims = _dated_claims()
    many = [claim for claim in claims if backed_by_many(claim)]
    _budget_between(monkeypatch, PlanPrompt(many, claims), PlanPrompt(claims, claims))
    prompt = fitted_prompt(claims)
    shown, total, rest = len(prompt.shown), len(claims), len(claims) - len(many)
    text = " ".join(prompt.user("REQUEST").split())
    assert (f"it shows {shown} of all {total} claims, namely all {len(many)} claims that more "
            f"than one member backs and {shown - len(many)} of the {rest} claims that fewer than two "
            "members back") in text
    assert "resemble the shown ones in subject and in proportion" in text
    assert text.index("resemble") < text.index("<claims ") < text.index("REQUEST")
    system = " ".join(prompt.system.split())
    guide = PLAN_CONFIG["page_claims"]
    scaled = {key: max(1, round(value * shown / total)) for key, value in guide.items()}
    assert scaled_guide(shown, total) == scaled and scaled != guide
    assert f"rests on roughly {scaled['typical_min']} to {scaled['typical_max']} claims" in system
    assert f"more than about {scaled['split_above']} claims" in system
    assert f"fewer than about {scaled['fold_below']} claims" in system
    assert f"which shows {shown} of all {total} claims" in system
    assert f"(roughly {guide['typical_min']} to {guide['typical_max']} claims" in system
    assert "lists a sample of the claims" in system
    full = PlanPrompt(claims, claims)
    assert "sample" not in full.system and "sample" not in full.claims_text


def test_when_the_many_backed_claims_alone_are_over_the_budget_they_are_sampled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claims = _dated_claims()
    many = [claim for claim in claims if backed_by_many(claim)]
    budget = _budget_between(monkeypatch, PlanPrompt(many[: len(many) // 3], claims),
                             PlanPrompt(many, claims))
    prompt = fitted_prompt(claims)
    assert prompt.shown and all(backed_by_many(claim) for claim in prompt.shown)
    assert len(prompt.shown) < len(many) and prompt.estimated_tokens <= budget
    years = {claim["first_date"][:4] for claim in prompt.shown}
    assert years == {"2022", "2023", "2024", "2025"}
    assert (f"{len(prompt.shown)} of the {len(many)} claims that more than one member backs and "
            f"0 of the {len(claims) - len(many)} claims") in " ".join(prompt.claims_text.split())


def test_a_budget_below_the_instructions_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    claims = _dated_claims(40)
    monkeypatch.setitem(PLAN_CONFIG, "max_input_tokens", PlanPrompt([], claims).estimated_tokens)
    with pytest.raises(ValueError, match="does not hold the planning instructions"):
        fitted_prompt(claims)


def test_the_guide_scales_but_never_below_one() -> None:
    guide = PLAN_CONFIG["page_claims"]
    assert scaled_guide(50, 50) == guide
    assert set(scaled_guide(1, 10_000).values()) == {1}


def test_a_sampled_run_records_it_and_checks_coverage_and_revises_on_all_claims(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    claims = _dated_claims()
    many = [claim for claim in claims if backed_by_many(claim)]
    _budget_between(monkeypatch, PlanPrompt(many, claims), PlanPrompt(claims, claims))
    shown_ids = {claim["short_id"] for claim in fitted_prompt(claims).shown}
    llm = FakeLLM([make_proposal(), make_proposal()])
    result = _run(tmp_path, claims, llm, _nowhere)
    meta = result["plan"]["metadata"]
    assert (meta["claim_count"], meta["shown_claim_count"]) == (len(claims), len(shown_ids))
    assert meta["sample_seed"] == PLAN_CONFIG["sample_seed"]
    assert meta["planned_claims"] == sorted(claim["short_id"] for claim in claims)
    report = result["coverage"]
    assert report["total_claims"] == len(claims)
    assert set(report["none_claims"]) - shown_ids  # the check also placed claims not listed
    assert [call["task"] for call in llm.calls] == ["plan", "plan-revise"]
    first, revision = (call["user"] for call in llm.calls)
    assert first.startswith("Not every claim fits")
    listed, extra = revision.split("The claims named here that the list does not show:\n")
    extra_ids = {line.split(" | ")[0] for line in extra.split("\n\n")[0].splitlines()}
    assert extra_ids == set(report["none_claims"]) - shown_ids
    block = listed.split("<claims ")[1].split("</claims>")[0]
    assert extra_ids and not any(short_id in block for short_id in extra_ids)
