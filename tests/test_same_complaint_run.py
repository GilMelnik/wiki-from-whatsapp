"""The same-complaint check's runs (``step_7_write_pages.same_complaint``) with a fake model:
what is asked, what is stored, when an entity is asked again, the estimate. Synthetic data."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest

from step_6_plan.claim_records import load_claim_records
from step_7_write_pages import same_complaint
from step_7_write_pages.same_complaint import SCHEMA, SYSTEM, main, parse_answer, run
from step_7_write_pages.same_complaint_release import released_ids
from tests.same_complaint_support import FakeModel, World, same_text
from utils.json_io import read_json_file
from utils.model_prices import ESTIMATE_CONFIG, usd

DEPOSIT = "deposit"
SLOW = "slow"
LABELS = {DEPOSIT: "did not return the deposit", SLOW: "answers very slowly"}


class Setup:
    def __init__(self, root: Path) -> None:
        self.world = World(root)
        self.answers = root / "same_complaint.json"
        self.vetoes = root / "vetoes.json"
        self.model = FakeModel(same_text(LABELS))

    def run(self, **kwargs: Any) -> dict[str, Any]:
        self.sources = self.world.write()
        return run(sources=self.sources, answers_path=self.answers, vetoes_path=self.vetoes,
                   llm=self.model, **kwargs)  # type: ignore[arg-type]  # a fake client

    def stored(self) -> dict[str, Any]:
        return read_json_file(self.answers)

    def released(self) -> frozenset[str]:
        return released_ids(load_claim_records(self.sources), audit=self.sources.audit,
                            answers=self.answers, vetoes=self.vetoes)


@pytest.fixture
def setup(tmp_path: Path) -> Setup:
    return Setup(tmp_path)


def agency_and_clinic(world: World) -> dict[str, str]:
    """Two lone deposit complaints and one slow one about the agency; one complaint about the
    clinic by one person; a positive claim."""

    return {
        "a": world.add(["e0001"], [1], text="They kept my deposit."),
        "b": world.add(["e0001"], [2], text="The deposit never came back."),
        "c": world.add(["e0001"], [3], text="Very slow answers."),
        "d": world.add(["e0002"], [4], text="The clinic lost the deposit."),
        "e": world.add(["e0001"], [5, 6], stance="positive", text="Great deposit terms."),
    }


def test_only_lists_that_can_release_are_asked_and_nothing_private_is_sent(setup: Setup) -> None:
    ids = agency_and_clinic(setup.world)
    result = setup.run()
    assert result["lists"] == 1 and result["due"] == 1 and result["answered"] == 1
    (request,) = setup.model.calls
    assert request.request_id == "e0001"
    assert request.system == SYSTEM and request.response_schema == SCHEMA
    assert request.user.splitlines() == [
        "<thing>Agency One</thing>", "", '<claims count="3">',
        "C1: They kept my deposit.", "C2: The deposit never came back.", "C3: Very slow answers.",
        "</claims>", "", "Find the claims that make the same complaint about this thing."]
    for private in ("SECRET", "g-", "t-", "2024", *ids.values()):
        assert private not in request.user
    assert setup.released() == {ids["a"], ids["b"]}
    assert result["released"] == 2


def test_the_stored_answers_hold_short_ids_and_metadata_only(setup: Setup) -> None:
    ids = agency_and_clinic(setup.world)
    setup.run()
    stored = setup.stored()
    entry = stored["entities"]["e0001"]
    assert entry["groups"] == [{"label": LABELS[DEPOSIT], "claims": [ids["a"], ids["b"]]}]
    assert entry["problem"] is None and len(entry["fingerprint"]) == 40
    assert set(stored["metadata"]) >= {"generated_at", "git_commit", "model", "effort",
                                       "prompt_version", "config", "counts"}
    assert stored["metadata"]["model"] == "claude-opus-5-5"
    text = setup.answers.read_text(encoding="utf-8")
    assert text.startswith('{\n  "entities"')
    for private in ("SECRET", "deposit.", "came back", "slow answers"):
        assert private not in text


def test_an_unchanged_list_is_not_asked_again_and_a_changed_one_is(setup: Setup) -> None:
    ids = agency_and_clinic(setup.world)
    setup.world.add(["e0002"], [7], text="The clinic kept the deposit.")
    first = setup.run()
    assert first["due"] == 2 and len(setup.model.calls) == 2
    stamp = setup.answers.stat().st_mtime_ns
    again = setup.run()
    assert again["due"] == 0 and not again["written"] and len(setup.model.calls) == 2
    assert setup.answers.stat().st_mtime_ns == stamp
    setup.world.records[2]["claim_text"] = "Slow answers, and a slow deposit refund."
    changed = setup.run()
    assert changed["due"] == 1 and changed["written"]
    assert setup.model.calls[-1].request_id == "e0001"
    assert setup.released() >= {ids["a"], ids["b"], ids["c"]}


def test_rebuild_asks_every_list_again(setup: Setup) -> None:
    agency_and_clinic(setup.world)
    setup.world.add(["e0002"], [7], text="The clinic kept the deposit.")
    setup.run()
    result = setup.run(rebuild=True)
    assert result["due"] == 2 and result["answered"] == 2
    assert len(setup.model.calls) == 2  # the repeats came from the cache


def test_only_asks_the_given_entities_and_keeps_the_other_answers(
        setup: Setup, caplog: pytest.LogCaptureFixture) -> None:
    agency_and_clinic(setup.world)
    setup.world.add(["e0002"], [7], text="The clinic kept the deposit.")
    setup.run(only=["e0002"])
    assert [r.request_id for r in setup.model.calls] == ["e0002"]
    setup.run(only=["e0001"])
    assert set(setup.stored()["entities"]) == {"e0001", "e0002"}
    with caplog.at_level(logging.WARNING):
        result = setup.run(only=["e0003"])
    assert result["due"] == 0 and "e0003" in caplog.text


def test_an_unusable_answer_is_stored_with_its_problem_and_releases_nothing(
        setup: Setup, caplog: pytest.LogCaptureFixture) -> None:
    agency_and_clinic(setup.world)
    setup.model.answer = lambda thing, claims: {"groups": [{"label": "x", "claims": ["C1", "C9"]}]}
    with caplog.at_level(logging.WARNING):
        result = setup.run()
    assert result["unusable"] == 1 and result["released"] == 0
    entry = setup.stored()["entities"]["e0001"]
    assert entry["groups"] == [] and "'C9'" in entry["problem"]
    assert "unusable" in caplog.text
    assert setup.released() == frozenset()


@pytest.mark.parametrize("answer, problem", [
    ({"groups": [{"label": "x", "claims": ["C1", "C2"]}, {"label": "y", "claims": ["C2", "C3"]}]},
     "in two groups"),
    ({"groups": [{"label": "x", "claims": ["C1"]}]}, "fewer than two"),
    ({"groups": [{"label": "x", "claims": ["C1", "C1"]}]}, "twice in one group"),
    ({"groups": [{"claims": ["C1", "C2"]}]}, "not {label"),
    ({"groups": "C1 C2"}, "not a list"),
    ([], "not a list"),
])
def test_parse_refuses_what_does_not_fit_the_list(answer: Any, problem: str) -> None:
    local = {"C1": "g-1", "C2": "g-2", "C3": "g-3"}
    groups, found = parse_answer(answer, local)
    assert groups == [] and problem in str(found)


def test_parse_maps_local_ids_and_squashes_labels() -> None:
    answer = {"groups": [{"label": "  kept   the deposit ", "claims": ["C3", "C1"]}]}
    groups, problem = parse_answer(answer, {"C1": "g-1", "C2": "g-2", "C3": "g-3"})
    assert problem is None
    assert groups == [{"label": "kept the deposit", "claims": ["g-3", "g-1"]}]


def test_a_call_without_answer_stores_nothing_and_is_asked_again(setup: Setup) -> None:
    agency_and_clinic(setup.world)
    setup.model.answer = lambda thing, claims: None
    result = setup.run()
    assert result["failed"] == 1 and setup.stored()["entities"] == {}
    setup.model.answer = same_text(LABELS)
    assert setup.run()["answered"] == 1


def test_a_failed_batch_writes_nothing(setup: Setup) -> None:
    agency_and_clinic(setup.world)
    setup.model.error = RuntimeError("batch expired")
    with pytest.raises(RuntimeError, match="nothing was written"):
        setup.run()
    assert not setup.answers.exists()


def test_a_list_that_no_longer_needs_a_call_loses_its_answer(setup: Setup) -> None:
    ids = agency_and_clinic(setup.world)
    setup.run()
    for key in ("a", "b", "c"):  # everyone else's complaints were deleted
        setup.world.records = [r for r in setup.world.records if r["short_id"] != ids[key]]
    setup.world.add(["e0001"], [1], text="They kept my deposit, again.")
    setup.run()
    assert setup.stored()["entities"] == {}


def test_a_list_longer_than_the_guard_stops_the_run_before_any_call(
        setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    agency_and_clinic(setup.world)
    monkeypatch.setitem(same_complaint.CONFIG, "max_claims_per_call", 2)
    with pytest.raises(ValueError, match="e0001 .Agency One. has 3 negative claims"):
        setup.run()
    assert setup.model.calls == [] and not setup.answers.exists()
    with pytest.raises(ValueError, match="max_claims_per_call"):
        setup.run(estimate=True)


def test_the_estimate_counts_uncached_calls_and_prices_a_range(setup: Setup) -> None:
    agency_and_clinic(setup.world)
    setup.world.add(["e0002"], [7], text="The clinic kept the deposit.")
    estimate = setup.run(estimate=True)["estimate"]
    assert setup.model.calls == [] and not setup.answers.exists()
    assert estimate["calls"] == 2 and estimate["cached"] == 0 and estimate["batch"]
    config = same_complaint.ESTIMATE
    low, high = (2 * config["output_tokens_per_call"][end] for end in ("low", "high"))
    assert estimate["output_tokens"] == [low, high]
    assert estimate["input_tokens"] > len(SYSTEM) * 2 / config["chars_per_token"]
    discount = ESTIMATE_CONFIG["batch_discount"]
    assert estimate["usd"] == [round(usd("claude-opus-5-5", estimate["input_tokens"], out)
                                     * discount, 4) for out in (low, high)]
    setup.run(only=["e0001"])
    after = setup.run(estimate=True, rebuild=True)["estimate"]
    assert (after["calls"], after["cached"]) == (1, 1)
    setup.model.batch = False
    assert not setup.run(estimate=True)["estimate"]["batch"]


def test_the_command_line(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}
    monkeypatch.setattr(same_complaint, "run", lambda **kwargs: seen.update(kwargs) or {})
    main(["--estimate", "--only", "e0001", "e0002", "--rebuild"])
    assert seen == {"rebuild": True, "only": ["e0001", "e0002"], "estimate": True}
