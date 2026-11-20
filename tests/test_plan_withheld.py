"""Step 6 plans without the claims withheld from the wiki now, refuses to plan while the
same-complaint check (step 5) has a list to answer, and estimates a planning run without a
call (``step_6_plan.claim_list``, ``step_6_plan.estimate``). Synthetic claims and people, fake
models and a fake token counter; no model or endpoint is called."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from step_5_same_complaint.release import Marks, save_marks
from step_5_same_complaint.run import run as check_same_complaints
from step_6_plan.claim_list import PlanningRefused
from step_6_plan.model import PLAN_CONFIG
from step_6_plan.run import run
from tests.plan_fixtures import PAGE_IDS, FakeClassifier, FakeLLM, make_proposal
from tests.same_complaint_support import FakeModel, World, same_text
from utils.model_prices import stage_model, usd
from utils.paths import Artifact

TOKENS = 5000


def _on_agencies(state: str) -> dict[str, float]:
    return {**{page: 0.0 for page in PAGE_IDS}, "none": 0.0, "agencies": 0.9}


class Planning:
    """Lone complaints about two entities that a second person's same complaint releases, one
    about a third that nobody repeats, and two claims no rule withholds."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.world = World(root)
        add = self.world.add
        self.ids = {
            "a": add(["e0001"], [1], text="They kept my deposit."),
            "b": add(["e0001"], [2], text="The deposit never came back."),
            "c": add(["e0002"], [3], text="The clinic answers very slowly."),
            "d": add(["e0002"], [4], text="Nobody at the clinic answers messages."),
            "e": add(["e0002"], [5], text="The clinic answers after weeks."),
            "f": add(["e0003"], [6], text="The lawyer lost the papers."),
            "g": add(["e0001"], [1, 2], stance="positive", text="Good contract terms."),
            "h": add([], [3], stance="factual", text="The process takes about a year."),
        }
        self.sources = self.world.write()
        self.artifact = Artifact(root / "plan.json", root / "plan_edited.json")
        self.counted: list[tuple[str, str]] = []

    def count_tokens(self, system: str, user: str) -> int:
        self.counted.append((system, user))
        return TOKENS

    def check(self) -> None:
        """Step 5 answers every list: the deposit and the slow-answer complaints are the same."""

        model = FakeModel(same_text({"deposit": "kept the deposit", "answers": "slow answers"}))
        check_same_complaints(sources=self.sources, llm=model)  # type: ignore[arg-type]

    def plan(self, llm: FakeLLM, **kwargs: Any) -> dict[str, Any]:
        return run(sources=self.sources, artifact=self.artifact, llm=llm,  # type: ignore[arg-type]
                   classifier=FakeClassifier(_on_agencies), count_tokens=self.count_tokens,
                   **kwargs)

    def short_ids(self, *keys: str) -> list[str]:
        return sorted(self.ids[key] for key in keys)


@pytest.fixture
def planning(tmp_path: Path) -> Planning:
    return Planning(tmp_path)


def test_a_planning_run_refuses_before_any_call_while_a_list_waits_for_the_check(
    planning: Planning,
) -> None:
    llm = FakeLLM([make_proposal()])
    with pytest.raises(PlanningRefused, match=r"2 entity lists wait for the same-complaint check"
                       r".*python -m step_5_same_complaint\.run.*Withheld page"):
        planning.plan(llm)
    assert llm.calls == [] and planning.counted == []
    assert not planning.artifact.original.exists() and not planning.artifact.edited.exists()


def test_after_the_check_the_planner_works_on_the_claims_not_withheld(planning: Planning) -> None:
    planning.check()
    kept = Marks(frozenset([planning.ids["d"]]))  # "keep withheld"
    save_marks(kept, planning.sources.same_complaint_vetoes)
    llm = FakeLLM([make_proposal(), make_proposal()])
    classifier = FakeClassifier(_on_agencies)
    result = run(sources=planning.sources, artifact=planning.artifact, llm=llm,  # type: ignore[arg-type]
                 classifier=classifier, count_tokens=planning.count_tokens)

    assert result["written"] and llm.calls
    # released by the check: a, b (deposit), c, e (slow answers, d kept withheld by the owner);
    # still withheld: d (the owner's mark) and f (nobody repeated it)
    listed = ("a", "b", "c", "e", "g", "h")
    for call in llm.calls:
        assert all(planning.ids[key] in call["user"] for key in listed)
        assert planning.ids["d"] not in call["user"] and planning.ids["f"] not in call["user"]
        assert '<claims count="6">' in call["user"]
    states = " ".join(state for state, _ in classifier.items)
    assert "Nobody at the clinic" not in states and "lost the papers" not in states
    meta = json.loads(planning.artifact.edited.read_text(encoding="utf-8"))["metadata"]
    assert (meta["claim_count"], meta["withheld_left_out"]) == (6, 2)
    assert meta["planned_claims"] == planning.short_ids(*listed)
    assert result["coverage"]["total_claims"] == 6


def test_a_claim_released_by_hand_reaches_the_planner(planning: Planning) -> None:
    planning.check()
    save_marks(Marks(released=frozenset([planning.ids["f"]])), planning.sources.same_complaint_vetoes)
    llm = FakeLLM([])
    estimate = planning.plan(llm, estimate=True)
    assert (estimate["claim_count"], estimate["withheld_left_out"]) == (8, 0)
    (_, user), = planning.counted
    assert planning.ids["f"] in user and llm.calls == []


def test_a_run_that_makes_no_planning_call_is_not_affected(planning: Planning) -> None:
    planning.check()
    planning.plan(FakeLLM([make_proposal(), make_proposal()]))
    planning.world.add(["e0001"], [7], text="They kept the deposit too.")  # e0001's list changed
    planning.sources = planning.world.write()
    llm = FakeLLM([])
    result = planning.plan(llm)
    # every claim record not planned counts: the one withheld at planning (f) and the new one
    assert result == {**result, "written": False, "model_calls": 0, "unplanned_claims": 2}
    assert llm.calls == []
    with pytest.raises(PlanningRefused, match="1 entity list waits"):
        planning.plan(llm, rebuild=True)
    assert llm.calls == []


def test_the_estimate_follows_the_list_makes_no_call_and_writes_nothing(
    planning: Planning,
) -> None:
    llm = FakeLLM([])
    before = sorted(planning.root.iterdir())
    waiting = planning.plan(llm, estimate=True)
    assert llm.calls == [] and sorted(planning.root.iterdir()) == before
    model = stage_model(PLAN_CONFIG["stage"])
    low, high = (PLAN_CONFIG["estimate"]["output_tokens_per_call"][end] for end in ("low", "high"))
    call = [round(usd(model, TOKENS, low), 4), round(usd(model, TOKENS, high), 4)]
    calls = PLAN_CONFIG["max_model_calls"]
    assert waiting == {
        "estimate": True, "written": False, "model_calls": 0, "model": "claude-fable-5-2",
        "claim_count": 2, "withheld_left_out": 6, "shown_claim_count": 2,
        "input_tokens": TOKENS, "output_tokens": [low, high],
        "usd": {"call": call, "revision": call,
                "ceiling": [round(calls * call[0], 4), round(calls * call[1], 4)]},
        "max_model_calls": calls, "waiting_lists": 2, "would_call": True, "refused": True,
    }
    (system, user), = planning.counted  # the first call's prompt, over the list
    assert '<claims count="2">' in user and planning.ids["a"] not in user + system

    planning.check()
    answered = planning.plan(llm, estimate=True)
    assert (answered["claim_count"], answered["withheld_left_out"]) == (7, 1)
    assert answered["refused"] is False and answered["waiting_lists"] == 0

    planning.plan(FakeLLM([make_proposal(), make_proposal()]))
    plan_bytes = planning.artifact.edited.read_bytes()
    kept = planning.plan(llm, estimate=True)
    assert kept["would_call"] is False and kept["refused"] is False
    assert llm.calls == [] and planning.artifact.edited.read_bytes() == plan_bytes
