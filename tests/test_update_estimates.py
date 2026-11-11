"""Step 10's estimates: the paid steps' estimate modes make no call and write nothing, the
prices come from config, and no test can build a real model client (the conftest guard)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from review_tool.steps import SERVED_STEPS
from step_10_update.estimate import CharTokenCounter, Estimator, embedding_usd
from tests.assign_fixtures import FakeClassifier, Workspace
from tests.plan_fixtures import make_groups
from tests.review_fixtures import FakeReviewer, ReviewWorkspace, valid_answers
from tests.test_extract_run import Corpus, FakeLLM, _message, answer_by_text
from utils.embedder import embed_texts
from utils.llm_client import Classifier, LLMClient, Question
from utils.paths import STEP_3, STEP_6_ASSIGN, STEP_7, STEP_8

STEPS = {step.key: step for step in SERVED_STEPS.steps}


def test_step_3_estimate_counts_the_calls_and_makes_none(tmp_path: Path) -> None:
    corpus = Corpus(tmp_path)
    llm = FakeLLM(answer_by_text)
    first = corpus.run(llm, estimate=True, count_tokens=CharTokenCounter())
    assert (first["model_calls"], first["update_calls"], first["written"]) == (2, 0, False)
    assert first["input_tokens"] > 0 and first["model"] == "fake-extract"
    assert llm.prompts == [] and not corpus.artifact.original.exists()
    corpus.run(FakeLLM(answer_by_text))
    corpus.threads["t-1"].append(_message("W9", "2024-01-07", "x", "alpha answers fast"))
    again = corpus.run(llm, estimate=True, count_tokens=CharTokenCounter())
    assert (again["model_calls"], again["update_calls"], again["changed_threads"]) == (1, 1, 1)
    assert llm.prompts == []


def test_step_6b_estimate_asks_nothing_and_bounds_the_proposals(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path)
    workspace.run()
    before = workspace.artifact.original.stat().st_mtime_ns
    workspace.write_groups(make_groups(16))
    classifier = FakeClassifier()
    result = workspace.run(classifier, estimate=True)
    assert classifier.items == [] and result["written"] is False
    assert result["groups_asked"] == 4 and result["plan_changed"] is False
    assert result["proposal_calls_max"] == 1 and result["proposal_candidates_max"] >= 4
    assert workspace.artifact.original.stat().st_mtime_ns == before
    assert workspace.run()["full"] == 4  # the run asks what the estimate counted


def test_step_8_estimate_says_whether_a_call_is_due(tmp_path: Path) -> None:
    workspace = ReviewWorkspace(tmp_path)
    reviewer = FakeReviewer(valid_answers())
    assert workspace.review(reviewer, estimate=True)["calls"] == 1
    workspace.review(reviewer)
    calls = len(reviewer.calls)
    assert workspace.review(reviewer, estimate=True)["calls"] == 0
    assert len(reviewer.calls) == calls


def _results() -> dict[str, dict[str, Any]]:
    return {
        STEP_3: {"model": "claude-sonnet-5-5", "model_calls": 2, "update_calls": 1,
                 "input_tokens": 1_000_000, "batch_mode": True},
        STEP_6_ASSIGN: {"groups_asked": 10, "plan_changed": False, "proposal_candidates_max": 3,
                        "proposal_calls_max": 1},
        STEP_7: {"estimate": [{"page": "a", "mode": "write", "input_tokens": 500_000},
                              {"page": "b", "mode": "update", "input_tokens": 500_000}],
                 "input_tokens": 1_000_000, "modes": {"write": 1, "update": 1, "keep": 4}},
        STEP_8: {"calls": 0, "input_tokens": 800_000},
    }


@pytest.mark.parametrize(("key", "calls", "usd"), [
    (STEP_3, 2, (1_000_000 * 2 + 2 * 1500 * 10) / 1e6 * 0.5),
    (STEP_6_ASSIGN, 1, 10 * 12200 * 0.042 / 1e6 + (30000 * 2 + 4000 * 10) / 1e6),
    (STEP_7, 2, (500_000 * 2 + 13000 * 10 + 500_000 * 2 + 6000 * 10) / 1e6),
    (STEP_8, 0, 0.0),
])
def test_estimates_are_priced_from_config(key: str, calls: int, usd: float) -> None:
    seen: dict[str, Any] = {}

    def fake_run(**kwargs: Any) -> dict[str, Any]:
        seen.update(kwargs)
        return _results()[key]

    estimate = Estimator(lambda step: fake_run)(STEPS[key])
    assert estimate.paid_calls == calls and estimate.usd == pytest.approx(usd)
    assert seen["estimate"] is True and estimate.line().startswith(estimate.what)
    if key != STEP_6_ASSIGN:
        assert isinstance(seen["count_tokens"], CharTokenCounter)


def test_unpaid_steps_have_no_estimate_and_embeddings_are_cheap() -> None:
    assert Estimator(lambda step: None)(STEPS["step_2_classify"]) is None
    assert CharTokenCounter(3.0)("ab", "cdef") == 2
    assert embedding_usd(["x" * 3000] * 737) == pytest.approx(737 * 1000 * 0.15 / 1e6)


def test_no_test_can_build_a_real_model_client(real_network_attempts: list[str]) -> None:
    with pytest.raises(RuntimeError, match="paid network call"):
        Classifier.for_stage("knowledge").classify("state", [Question("q", "noul", "Is it?")])
    with pytest.raises(RuntimeError, match="paid network call"):
        LLMClient.for_stage("extract").complete_json("system", "user")
    with pytest.raises(RuntimeError, match="paid network call"):
        embed_texts(["a text no cache has"], kind="similarity")
    assert {name.split(".")[-1] for name in real_network_attempts} >= {
        "TypeSafeClient", "Anthropic", "Client"}
    real_network_attempts.clear()
