"""Step 4's model stages, the review's cases: the estimate's upper bound, a seed alias with two
meanings, names several entities hold, a failed batch, look-alike holders, the signal and a
rerun of the record. Fakes only; every name is invented."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path

import numpy as np
import pytest

from review_tool.steps import SERVED_STEPS
from step_10_update.estimate import Estimator
from step_4_entities import run as step_run
from step_4_entities.match_estimate import EmbeddingsMissing
from step_4_entities.match_judge import StageFailed
from step_4_entities.match_record import MatchRecord
from step_4_entities.run import MatchClients
from tests.entities_match_support import (
    FakeClassifier,
    FakeEmbed,
    FakeLLM,
    MatchStep,
    by_keyword,
    claim,
    grouped,
)
from tests.test_entities_match_run import CLAIMS, KEYWORDS, SEED, THINGS, _step
from utils.llm_client import BatchGone, BatchRequest, BatchUnfinished
from utils.paths import STEP_4


class CacheOnlyEmbed:
    """The live fake's vectors for texts it has embedded; any other text is missing."""

    def __init__(self, live: FakeEmbed) -> None:
        self.live = live

    def __call__(self, texts: Sequence[str], kind: str) -> np.ndarray:
        missing = [t for t in texts if t not in self.live.seen]
        if missing:
            raise EmbeddingsMissing(missing)
        return self.live(texts, kind)


def _with(step: MatchStep, **changes: object) -> None:
    given = step.clients
    step.clients = MatchClients(**{"embed": given.embed, "classifier": given.classifier,
                                   "llm": given.llm, **changes})


# --- 1: the estimate covers what missing embeddings could cause -----------------------------

def test_a_new_name_found_only_by_meaning_is_in_the_estimates_high_end(tmp_path: Path) -> None:
    step = _step(tmp_path)
    base = [c for c in CLAIMS if c["entities"] != ["MoH"]]
    step.run(base)
    live = step.clients.embed
    _with(step, embed=CacheOnlyEmbed(live))
    claims = [*base, claim("t9-a", ["MoH"], "MoH approved the birth certificate", "2024-01-01")]
    estimate = step.run(claims, estimate=True)
    assert estimate["paid_calls"][0] == 0 and estimate["paid_calls"][1] >= 2
    assert estimate["exact"] is False and estimate["usd"][1] > 0
    assert estimate["stages"]["judge"]["calls"][1] >= 1
    assert estimate["stages"]["senses"]["calls"][1] >= 1
    # the update's pay gate reads the high end: the step stops for --pay
    gate = Estimator(lambda s: lambda **kwargs: estimate)(
        next(s for s in SERVED_STEPS.steps if s.key == STEP_4))
    assert gate.paid_calls >= 1
    calls = len(step.clients.llm.calls)
    _with(step, embed=live)
    step.run(claims)
    assert len(step.clients.llm.calls) > calls  # the run did make the judge call


# --- 2: a seed alias with two meanings never leaves its entry silently ----------------------

def _alias_case(tmp_path: Path, senses: object, things: dict | None = None) -> MatchStep:
    step = _step(tmp_path)
    step.clients.llm.judge = grouped(things or THINGS)
    step.clients.llm.senses = senses
    step.run(CLAIMS)
    return step


def test_all_claims_to_the_other_meaning_flags_the_seed_entry(tmp_path: Path) -> None:
    step = _alias_case(tmp_path, by_keyword({"insurer": ["dana"]}))
    seed_entity = step.entity_of("Dana Levi")
    insurer = step.entity_of("Dana")
    assert "Dana" not in seed_entity["aliases"]
    assert seed_entity["status"] == "ambiguous"  # not adopted as accepted
    assert insurer["entity_id"] != seed_entity["entity_id"]
    assert "Dana" in seed_entity["conflict_with"] and "Dana Levi" in insurer["conflict_with"]


def test_all_unclear_claims_stay_in_the_seed_entry_even_when_listed_second(tmp_path: Path) -> None:
    things = {**THINGS, "Dana": ["insurer", "lawyer"]}  # the seed's group comes second
    step = _alias_case(tmp_path, by_keyword({}), things)
    seed_entity = step.entity_of("Dana")
    assert "Dana Levi" in seed_entity["aliases"]
    assert len(step.member(seed_entity, "Dana")["claim_ids"]) == 5
    assert seed_entity["status"] == "ambiguous"
    sense = step.matches()["senses"][0]
    assert len(sense["unclear"]) == 5 and not sense["failed"]


def test_unclear_claims_follow_the_most_answered_meaning(tmp_path: Path) -> None:
    things = {**THINGS, "Dana": ["insurer", "lawyer"]}
    step = _alias_case(tmp_path, by_keyword({"lawyer": ["court"]}), things)
    seed_entity = step.entity_of("Dana Levi")
    assert set(step.member(seed_entity, "Dana")["claim_ids"]) == {
        "t4-a", "t5-a", "t5-b", "t6-a", "t7-a"}


# --- 3: entities that share a name are never joined through it ------------------------------

SPLIT = [
    claim("t1-a", ["Dana Levi"], "Levi won the court case", "2023-01-01"),
    claim("t2-a", ["Dana"], "Dana court order", "2023-02-01"),
    claim("t3-a", ["Dana"], "Dana insurance paid", "2023-03-01"),
    *(claim(f"t{i}-a", ["Dana Insurance"], f"insurance policy {i}", f"2023-04-{i:02d}")
      for i in range(4, 9)),
]
SPLIT_THINGS = {"Dana Levi": ["lawyer"], "Adv Dana Levi": ["lawyer"],
                "Dana Insurance": ["insurer"], "Dana": ["lawyer", "insurer"]}


def _split_step(tmp_path: Path) -> MatchStep:
    clients = MatchClients(embed=FakeEmbed(), classifier=FakeClassifier(),
                           llm=FakeLLM(grouped(SPLIT_THINGS), by_keyword(KEYWORDS)))
    step = MatchStep(tmp_path, clients, None)
    step.run(SPLIT)
    lawyer, insurer = step.entity_of("Dana Levi"), step.entity_of("Dana Insurance")
    assert lawyer is not insurer and len(step.entities_of("Dana")) == 2
    return step


def test_a_new_name_follows_the_entity_its_match_alone_holds_not_the_bigger(
        tmp_path: Path) -> None:
    step = _split_step(tmp_path)
    step.run([*SPLIT, claim("t9-a", ["Adv Dana Levi"], "the lawyer again", "2024-01-01")])
    entity = step.entity_of("Adv Dana Levi")
    assert entity["entity_id"] == step.entity_of("Dana Levi")["entity_id"]
    attached = step.member(entity, "Adv Dana Levi")["attached_by"]
    assert attached == {**attached, "matched": "Dana Levi", "signals": ["model"]}


def test_a_new_name_like_only_the_shared_name_is_unsure_not_attached(tmp_path: Path) -> None:
    step = _split_step(tmp_path)
    step.clients.llm.judge = grouped({**SPLIT_THINGS, "Dana": ["lawyer", "insurer", "dq"],
                                      "Dana Q": ["dq"]})
    step.run([*SPLIT, claim("t9-a", ["Dana Q"], "Dana Q answered", "2024-01-01")])
    entity = step.entity_of("Dana Q")
    assert entity["aliases"] == ["Dana Q"] and "model" not in entity["merge_signals"]
    held = {e["canonical_name"] for e in step.entities_of("Dana")}
    assert held <= set(entity["conflict_with"]) and entity["status"] == "ambiguous"
    assert {"names": ["Dana", "Dana Q"], "reason": "", "source": "shared_name"} in (
        step.matches()["unsure"])


# --- 4: a batch that fails as a whole writes nothing -----------------------------------------

def test_a_failed_batch_stops_the_run_and_writes_nothing(tmp_path: Path) -> None:
    step = _step(tmp_path)
    step.clients.llm.batch_error = RuntimeError("the batch could not be polled")
    with pytest.raises(StageFailed, match="nothing was written"):
        step.run(CLAIMS)
    assert not step.artifact.edited.exists() and not step.artifact.original.exists()
    assert not step.matches_path.exists()
    assert step.clients.llm.resumable == [True]
    step.clients.llm.batch_error = None
    step.run(CLAIMS)
    assert step.artifact.edited.exists() and step.matches_path.exists()


# --- 5: holders that cannot be told apart are not asked about ---------------------------------

def test_new_claims_of_a_name_two_lone_entities_hold_go_to_the_bigger(tmp_path: Path) -> None:
    claims = [claim("t1-a", ["Dana"], "Dana court order"),
              claim("t2-a", ["Dana"], "Dana insurance paid"),
              claim("t2-b", ["Dana"], "Dana insurance late"),
              claim("t3-a", ["Other Name"], "something else")]
    clients = MatchClients(
        embed=FakeEmbed(), classifier=FakeClassifier(lambda a, b: 0.9),
        llm=FakeLLM(grouped({"Dana": ["thing", "thing too"]}),
                    by_keyword({"thing": ["court"], "thing too": ["insur"]})))
    step = MatchStep(tmp_path, clients, None)
    step.run(claims)
    assert len(step.entities_of("Dana")) == 2
    calls = len(step.clients.llm.calls)
    step.run([*claims, claim("t4-a", ["Dana"], "Dana again")])
    assert len(step.clients.llm.calls) == calls
    bigger = max(step.entities_of("Dana"), key=lambda e: len(step.member(e, "Dana")["claim_ids"]))
    assert "t4-a" in step.member(bigger, "Dana")["claim_ids"]
    assert all(e["status"] == "ambiguous" for e in step.entities_of("Dana"))


# --- 7: the signal "model" needs two placed names ----------------------------------------------

def test_an_entity_with_one_name_has_no_model_signal(tmp_path: Path) -> None:
    step = _step(tmp_path)
    step.run(CLAIMS)
    assert all("model" not in e["merge_signals"]
               for e in step.edited()["entities"] if len(e["members"]) == 1)


# --- 8: a rerun does not repeat the record -------------------------------------------------------

def test_the_record_is_written_after_the_entities_and_a_rerun_does_not_repeat_it(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    step = _step(tmp_path, seed=SEED)

    def refuse(*args: object, **kwargs: object) -> None:
        raise OSError("disk full")

    with monkeypatch.context() as patch:
        patch.setattr(step_run, "guarded_write", refuse)
        with pytest.raises(OSError):
            step.run(CLAIMS)
    assert not step.matches_path.exists()
    step.run(CLAIMS)
    assert len(step.matches()["senses"]) == 1
    record = MatchRecord(step.matches_path, fresh=False)
    record.add_senses(step.matches()["senses"])
    assert len(record.data["senses"]) == 1


# --- last round A: an existing name's changed text turns the meaning net off for all ---------

def test_a_changed_existing_text_makes_every_new_name_uncertain(tmp_path: Path) -> None:
    step = _step(tmp_path)
    base = [c for c in CLAIMS if c["entities"] != ["MoH"]]
    step.run(base)
    with_moh = [*base, claim("t9-a", ["MoH"], "MoH approved the birth certificate", "2024-01-01")]
    step.clients.llm.batch_error = RuntimeError("the batch could not be polled")
    with pytest.raises(StageFailed):
        step.run(with_moh)  # the new name was embedded and screened, nothing was written
    step.clients.llm.batch_error = None
    live = step.clients.embed
    _with(step, embed=CacheOnlyEmbed(live))
    newer = [*with_moh, claim("t9-b", ["Ministry of Health"], "Ministry of Health again",
                              "2024-02-01")]
    estimate = step.run(newer, estimate=True)
    assert estimate["paid_calls"][1] >= 1 and estimate["exact"] is False
    calls = len(step.clients.llm.calls)
    _with(step, embed=live)
    step.run(newer)
    assert len(step.clients.llm.calls) > calls


# --- last round B: requests that never ran stop the stage ------------------------------------

def test_an_unfinished_batch_stops_the_run_and_says_what_a_rerun_does(tmp_path: Path) -> None:
    step = _step(tmp_path)
    step.clients.llm.batch_error = BatchUnfinished(["b-1"])
    with pytest.raises(StageFailed, match="submits only the unfinished ones"):
        step.run(CLAIMS)
    assert not step.artifact.edited.exists() and not step.matches_path.exists()
    step.clients.llm.batch_error = BatchGone("batch_9", RuntimeError("not found"))
    with pytest.raises(StageFailed, match="submits a new batch"):
        step.run(CLAIMS)


# --- last round C: room for the judge, and a client of its own per stage ---------------------

def test_the_judge_and_the_per_claim_stage_have_their_own_large_window(tmp_path: Path) -> None:
    from step_4_entities.cluster import EntityClusterer
    from step_4_entities.collect import ClaimCorpus
    from step_4_entities.match import EntityMatcher

    matcher = EntityMatcher(ClaimCorpus([], {}), EntityClusterer(
        {}, similarity_threshold=0.8, seed_path=tmp_path / "none.json"),
        MatchRecord(tmp_path / "m.json", fresh=True), logging.getLogger("test"))
    judge, senses = matcher.llm, matcher.senses_llm
    assert judge is not senses
    assert judge.max_tokens == senses.max_tokens == 128000
    assert judge._grown_max_tokens() is None  # a cut-off answer fails at once: no paid retry
    requests = [BatchRequest("x", "s", "u")]
    before = senses._batch_state_path(requests)
    judge.max_tokens = 64000
    assert senses._batch_state_path(requests) == before
