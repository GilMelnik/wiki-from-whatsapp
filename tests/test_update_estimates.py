"""Step 10's estimates: the paid steps' estimate modes make no call and write nothing, the
prices come from config, and no test can build a real model client (the conftest guard)."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pytest

from review_tool.steps import SERVED_STEPS
from step_3_extract.price import extract_price
from step_3_extract.prompt import EXTRACT_SYSTEM
from step_3_extract.split_experiment.prompts import UnitJob
from step_3_extract.split_experiment.units import build_units
from step_3_extract.split_experiment.usage import estimate as experiment_estimate
from step_10_update.estimate import Estimator, embedding_usd
from tests.assign_fixtures import FakeClassifier, Workspace
from tests.plan_fixtures import make_groups
from tests.review_fixtures import FakeReviewer, ReviewWorkspace, valid_answers
from tests.split_experiment_support import NEW_SPLIT, OLD_SPLIT, message_set, threads
from tests.test_extract_run import Corpus, FakeLLM, _message, answer_by_text
from utils.config import section
from utils.embedder import embed_texts
from utils.llm_client import Classifier, LLMClient, Question
from utils.paths import STEP_3, STEP_6_ASSIGN, STEP_7, STEP_8, STEP_8_PROOFREAD
from utils.token_counters import CharTokenCounter

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
    assert workspace.review(reviewer, estimate=True)["calls"] == 3  # two parts and the cross pass
    workspace.review(reviewer)
    calls = len(reviewer.calls)
    assert workspace.review(reviewer, estimate=True)["calls"] == 0
    assert len(reviewer.calls) == calls


# Step 3: the million input tokens of the two calls below (counted at the Hebrew ratio) less
# the system prompt counted with each call are the conversations; every call reads the
# 2,170 instruction tokens from the prompt cache (0.1 of the input price); output is 0.85 times
# the conversations' tokens.
EXTRACT_CONVERSATIONS = 1_000_000 - 2 * CharTokenCounter(1.39)(EXTRACT_SYSTEM, "")
EXTRACT_OUTPUT = math.ceil(EXTRACT_CONVERSATIONS * 0.85)


def _results() -> dict[str, dict[str, Any]]:
    return {
        STEP_3: {"model": "claude-sonnet-5-5", "model_calls": 2, "update_calls": 1,
                 "input_tokens": 1_000_000, "batch_mode": True},
        STEP_6_ASSIGN: {"groups_asked": 10, "plan_changed": False, "proposal_candidates_max": 3,
                        "proposal_calls_max": 1},
        STEP_7: {"estimate": [{"page": "a", "mode": "write", "input_tokens": 500_000},
                              {"page": "b", "mode": "update", "input_tokens": 500_000}],
                 "input_tokens": 1_000_000, "modes": {"write": 1, "update": 1, "keep": 4}},
        STEP_8: {"calls": 2, "input_tokens": 200_000, "batch": True,
                 "passes": [{"id": "a"}, {"id": "b"}, {"id": "cross"}]},
        STEP_8_PROOFREAD: {"calls": 2, "batch": True, "estimate": [
            {"page": "a", "chars": 15_600, "new_sentences": 3},
            {"page": "b", "chars": 15_601, "new_sentences": 1}]},
    }


@pytest.mark.parametrize(("key", "calls", "usd"), [
    (STEP_3, 2, ((EXTRACT_CONVERSATIONS + 2 * 2170 * 0.1) * 2 + EXTRACT_OUTPUT * 10) / 1e6 * 0.5),
    (STEP_6_ASSIGN, 1, 10 * 12200 * 0.042 / 1e6 + (30000 * 2 + 4000 * 10) / 1e6),
    (STEP_7, 2, (500_000 * 2 + 13000 * 10 + 500_000 * 2 + 6000 * 10) / 1e6),
    (STEP_8, 2, (200_000 * 4 + 6000 * 2 * 20) / 1e6 * 0.5),
    # two proofreading calls (Fable, batch): 10,000 and 10,001 input tokens at the review's
    # 1.56 characters per token, 23,000 output tokens each
    (STEP_8_PROOFREAD, 2, ((10_000 + 10_001) * 8 + 2 * 23000 * 40) / 1e6 * 0.5),
])
def test_estimates_are_priced_from_config(key: str, calls: int, usd: float) -> None:
    seen: dict[str, Any] = {}

    def fake_run(**kwargs: Any) -> dict[str, Any]:
        seen.update(kwargs)
        return _results()[key]

    estimate = Estimator(lambda step: fake_run)(STEPS[key])
    assert estimate.paid_calls == calls and estimate.usd == pytest.approx(usd)
    assert seen["estimate"] is True and estimate.line().startswith(estimate.what)
    if key not in (STEP_6_ASSIGN, STEP_8_PROOFREAD):
        assert isinstance(seen["count_tokens"], CharTokenCounter)
    if key == STEP_3:
        assert seen["count_tokens"].chars_per_token == 1.39


def test_step_3_is_priced_as_the_api_reported_it() -> None:
    """Task 52's calibration (36 batch calls of the new split, Sonnet 5.5): 1.39 characters
    per token, 2,170 instruction tokens per call read from the prompt cache; output 0.85 times the
    conversations' tokens (measured at effort high on 11 conversations, 2026-11-12)."""
    assert {key: section("update")["estimate"][key] for key in (
        "extract_chars_per_token", "extract_instruction_tokens", "extract_output_ratio",
        "cache_read_price_factor")} == {"extract_chars_per_token": 1.39,
                                        "extract_instruction_tokens": 2170,
                                        "extract_output_ratio": 0.85,
                                        "cache_read_price_factor": 0.1}
    # Repeated instructions are almost free: about $0.26 for the 1,203 calls of the real input.
    assert extract_price("claude-sonnet-5-5", 1203, 0, 0, True).usd == pytest.approx(0.26, abs=0.005)
    price = extract_price("claude-sonnet-5-5", 36, 100_000, 90_000, True)
    assert (price.prompt_tokens, price.cached_tokens, price.output_tokens) == (
        100_000, 36 * 2170, 76_500)
    assert price.usd == pytest.approx((100_000 + 36 * 2170 * 0.1) * 1e-6 + 76_500 * 5e-6)
    assert extract_price("claude-sonnet-5-5", 36, 100_000, 90_000, False).usd == pytest.approx(
        2 * price.usd)


def test_the_split_experiment_prices_step_3_as_step_10_does() -> None:
    """The same conversations, once as the experiment's units and once as what step 3's
    estimate mode reports for them, cost the same (within the few tokens of the
    "Conversation:" heading, which only step 10 counts as conversation)."""
    messages = message_set()
    jobs = [UnitJob(unit, messages) for unit in
            build_units(messages, threads(NEW_SPLIT), threads(OLD_SPLIT), ["A"])["A"]]
    counter = CharTokenCounter(1.39)
    reported = {"model": "claude-sonnet-5-5", "model_calls": len(jobs), "update_calls": 0,
                "input_tokens": sum(counter(EXTRACT_SYSTEM, job.prompt()) for job in jobs),
                "batch_mode": True}
    step_10 = Estimator(lambda step: lambda **kwargs: reported)(STEPS[STEP_3])
    experiment = experiment_estimate(jobs, use_batch=True)
    assert experiment["calls"] == step_10.paid_calls == 4
    assert experiment["usd"] == pytest.approx(step_10.usd, rel=0.1)


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
