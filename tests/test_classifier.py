"""Classifier (contract C4): config wiring, caching, ordering, failure handling,
and the two providers' request/answer mapping — all without network.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Sequence

import pytest
from typesafe_sdk import Choice, Noul, Score, SystemOneResponse, TypeSafeError

from utils.config import DATA_DIR
from utils.llm_client import Answer, Classifier, ClassifierError, Question
from utils.llm_client.providers.anthropic import (
    CLASSIFIER_ANSWERS_SCHEMA,
    AnthropicClassifierProvider,
)
from utils.llm_client.providers.base import ClassifierProvider
from utils.llm_client.providers.typesafe import TypeSafeClassifierProvider
from utils.llm_client.settings import CLASSIFIER_CONFIG

LOGGER = logging.getLogger("test_classifier")

NOUL = Question("is_advice", "noul", "The message gives practical advice.")
CHOICE = Question("topic", "choice", "What is it about?", ("clinic", "greeting", "price"))
SCORE = Question("useful", "score", "How useful is it?", ("small talk", "some", "concrete"))
QUESTIONS = [NOUL, CHOICE, SCORE]


class _FakeProvider(ClassifierProvider):
    """Answers every question with the state's length; fails on chosen states."""

    name = "fake"

    def __init__(self, fail_on: Sequence[str] = (), delay: float = 0.0) -> None:
        super().__init__("fake-1", LOGGER)
        self.calls: list[str] = []
        self.fail_on = set(fail_on)
        self.delay = delay

    def _create_client(self) -> None:
        return None

    def classify(self, state: str, questions: Sequence[Question]) -> list[Answer]:
        self.calls.append(state)
        time.sleep(self.delay)
        if state in self.fail_on:
            raise ClassifierError(f"boom on {state!r}")
        return [Answer(question.id, float(len(state))) for question in questions]


def _classifier(tmp_path: Path, provider: ClassifierProvider, **kwargs: Any) -> Classifier:
    classifier = Classifier(
        "typesafe",
        "jev-latest",
        cache_dir=tmp_path / "cache",
        failure_log=tmp_path / "failures.jsonl",
        **kwargs,
    )
    classifier._provider = provider
    return classifier


# ------------------------------------------------------------------ config
def test_for_stage_reads_provider_and_model_from_config() -> None:
    for stage, resolved in CLASSIFIER_CONFIG["stages"].items():
        classifier = Classifier.for_stage(stage)
        assert classifier.provider == resolved["provider"]
        assert classifier.model == resolved["model"]
    assert set(CLASSIFIER_CONFIG["stages"]) >= {"knowledge", "assign"}


def test_for_stage_rejects_an_unknown_stage() -> None:
    with pytest.raises(ValueError, match="Unknown classifier stage 'nope'"):
        Classifier.for_stage("nope")


def test_fallback_in_config_is_a_usable_provider() -> None:
    fallback = CLASSIFIER_CONFIG["fallback"]

    classifier = Classifier(fallback["provider"], fallback["model"])

    assert isinstance(classifier._provider, AnthropicClassifierProvider)


def test_unknown_provider_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown classifier provider"):
        Classifier("openai", "gpt")


def test_default_cache_and_failure_log_follow_the_data_dir() -> None:
    classifier = Classifier.for_stage("knowledge")

    assert classifier.cache.cache_dir == DATA_DIR / "shared" / "classifier_cache"
    assert classifier.failure_log == DATA_DIR / "shared" / "classifier_failures.jsonl"


# ------------------------------------------------------------------- cache
def test_second_call_is_served_from_the_disk_cache(tmp_path: Path) -> None:
    provider = _FakeProvider()
    classifier = _classifier(tmp_path, provider)

    first = classifier.classify("state", QUESTIONS, task="t")
    second = classifier.classify("state", QUESTIONS, task="other label")

    assert first == second == [Answer(q.id, 5.0) for q in QUESTIONS]
    assert provider.calls == ["state"]
    # A fresh instance (a later run) reads the same entry from disk.
    rerun_provider = _FakeProvider()
    assert _classifier(tmp_path, rerun_provider).classify("state", QUESTIONS) == first
    assert rerun_provider.calls == []


def test_choice_probabilities_survive_the_cache_round_trip(tmp_path: Path) -> None:
    provider = _FakeProvider()
    provider.classify = lambda state, questions: [  # type: ignore[method-assign]
        Answer("topic", {"clinic": 0.7, "greeting": 0.2, "price": 0.1})
    ]
    classifier = _classifier(tmp_path, provider)

    first = classifier.classify("state", [CHOICE])

    assert classifier.classify("state", [CHOICE]) == first
    assert first[0].probabilities == {"clinic": 0.7, "greeting": 0.2, "price": 0.1}


def test_cache_key_covers_state_questions_model_and_provider(tmp_path: Path) -> None:
    classifier = _classifier(tmp_path, _FakeProvider())
    reworded = Question("is_advice", "noul", "The message recommends something.")
    other_model = Classifier("typesafe", "jev-1.13.0", cache_dir=tmp_path / "cache")
    other_provider = Classifier("anthropic", "jev-latest", cache_dir=tmp_path / "cache")

    keys = {
        classifier._cache_key("state", [NOUL]),
        classifier._cache_key("another state", [NOUL]),
        classifier._cache_key("state", [reworded]),
        classifier._cache_key("state", [NOUL, CHOICE]),
        other_model._cache_key("state", [NOUL]),
        other_provider._cache_key("state", [NOUL]),
    }

    assert len(keys) == 6


def test_anthropic_cache_key_covers_effort(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(CLASSIFIER_CONFIG["anthropic"], "effort", "low")
    low = Classifier("anthropic", "claude-haiku-5-5", cache_dir=tmp_path)
    monkeypatch.setitem(CLASSIFIER_CONFIG["anthropic"], "effort", "medium")
    medium = Classifier("anthropic", "claude-haiku-5-5", cache_dir=tmp_path)

    assert low._cache_key("state", [NOUL]) != medium._cache_key("state", [NOUL])


def test_use_cache_false_always_asks_the_provider(tmp_path: Path) -> None:
    provider = _FakeProvider()
    classifier = _classifier(tmp_path, provider, use_cache=False)

    classifier.classify("state", QUESTIONS)
    classifier.classify("state", QUESTIONS)

    assert provider.calls == ["state", "state"]
    assert not (tmp_path / "cache").exists()


# ----------------------------------------------------------- classify_many
def test_classify_many_preserves_item_order_and_sends_duplicates_once(
    tmp_path: Path,
) -> None:
    provider = _FakeProvider(delay=0.01)
    classifier = _classifier(tmp_path, provider)
    states = ["a", "bbbb", "cc", "bbbb", "ddddd", "a", "eee"]

    results = classifier.classify_many([(state, QUESTIONS) for state in states], task="t")

    assert [[answer.probabilities for answer in answers] for answers in results] == [
        [float(len(state))] * len(QUESTIONS) for state in states
    ]
    assert sorted(provider.calls) == ["a", "bbbb", "cc", "ddddd", "eee"]


def test_classify_many_surfaces_the_failing_item_index(tmp_path: Path) -> None:
    provider = _FakeProvider(fail_on=["bad"])
    classifier = _classifier(tmp_path, provider)
    items = [(state, [NOUL]) for state in ("ok-1", "ok-2", "bad", "ok-3")]

    with pytest.raises(ClassifierError, match="boom on 'bad'") as raised:
        classifier.classify_many(items, task="knowledge")

    assert raised.value.index == 2
    lines = (tmp_path / "failures.jsonl").read_text(encoding="utf-8").splitlines()
    entry = json.loads(lines[0])
    assert len(lines) == 1
    assert entry["state"] == "bad" and entry["task"] == "knowledge"
    assert entry["provider"] == "typesafe" and entry["model"] == "jev-latest"
    assert entry["questions"][0]["id"] == "is_advice"
    # Items answered before the failure are cached: a retry does not pay again.
    assert classifier.classify("ok-1", [NOUL]) == [Answer("is_advice", 4.0)]
    assert provider.calls.count("ok-1") == 1


def test_classify_many_stops_starting_requests_after_a_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(CLASSIFIER_CONFIG, "concurrency", 1)
    provider = _FakeProvider(fail_on=["bad"], delay=0.01)
    classifier = _classifier(tmp_path, provider)
    items = [("bad", [NOUL])] + [(f"item-{n}", [NOUL]) for n in range(60)]

    with pytest.raises(ClassifierError) as raised:
        classifier.classify_many(items)

    assert raised.value.index == 0
    assert len(provider.calls) < len(items)


def test_single_classify_failure_is_logged_and_has_no_index(tmp_path: Path) -> None:
    classifier = _classifier(tmp_path, _FakeProvider(fail_on=["bad"]))

    with pytest.raises(ClassifierError) as raised:
        classifier.classify("bad", [NOUL], task="t")

    assert raised.value.index is None
    assert (tmp_path / "failures.jsonl").exists()
    assert not (tmp_path / "cache").exists()  # a failure is never cached


# -------------------------------------------------------------- validation
@pytest.mark.parametrize(
    ("questions", "message"),
    [
        ([], "at least one question"),
        ([NOUL, NOUL], "ids must be unique"),
        ([Question("q", "noul", "t", ("a", "b"))], "must not have options"),
        ([Question("q", "choice", "t")], "needs 2 to"),
        ([Question("q", "choice", "t", ("only",))], "needs 2 to"),
        ([Question("q", "choice", "t", ("same", "same"))], "distinct options"),
        ([Question("q", "score", "t", ("one level",))], "needs 2 to"),
        ([Question("q", "box", "t")], "unknown kind"),  # type: ignore[arg-type]
    ],
)
def test_invalid_questions_are_rejected_before_any_request(
    tmp_path: Path, questions: list[Question], message: str
) -> None:
    provider = _FakeProvider()
    classifier = _classifier(tmp_path, provider)

    with pytest.raises(ValueError, match=message):
        classifier.classify("state", questions)
    with pytest.raises(ValueError, match="item 1: "):
        classifier.classify_many([("state", [NOUL]), ("state", questions)])
    assert provider.calls == []


def test_option_limits_come_from_config(tmp_path: Path) -> None:
    classifier = _classifier(tmp_path, _FakeProvider())
    max_options = CLASSIFIER_CONFIG["max_choice_options"]
    max_levels = CLASSIFIER_CONFIG["max_score_levels"]
    options = tuple(f"option {n}" for n in range(max_options + 1))

    classifier.classify("s", [Question("q", "choice", "t", options[:max_options])])
    classifier.classify("s", [Question("q", "score", "t", options[:max_levels])])
    with pytest.raises(ValueError, match=f"needs 2 to {max_options}"):
        classifier.classify("s", [Question("q", "choice", "t", options)])
    with pytest.raises(ValueError, match=f"needs 2 to {max_levels}"):
        classifier.classify("s", [Question("q", "score", "t", options[: max_levels + 1])])


# ------------------------------------------------- Claude (Haiku) provider
def _haiku() -> AnthropicClassifierProvider:
    return AnthropicClassifierProvider(model="claude-haiku-5-5", logger=LOGGER)


def _haiku_reply(text: str, stop_reason: str = "end_turn") -> SimpleNamespace:
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason=stop_reason,
        stop_details=SimpleNamespace(category="cyber") if stop_reason == "refusal" else None,
    )


def test_haiku_answers_convert_to_c4_probabilities() -> None:
    raw = [
        {"id": "useful", "score": 1.5, "confidence": 0.6},
        {"id": "topic", "choice": "greeting", "confidence": 0.8},
        {"id": "is_advice", "yes": False, "confidence": 0.9},
    ]

    answers = _haiku()._to_answers(QUESTIONS, raw)

    assert [answer.id for answer in answers] == ["is_advice", "topic", "useful"]
    assert answers[0].probabilities == pytest.approx(0.1)  # "no" at 0.9 -> P(yes) 0.1
    assert answers[1].probabilities == {"clinic": 0.0, "greeting": 0.8, "price": 0.0}
    assert answers[2].probabilities == pytest.approx(0.75)  # 1.5 of levels 0..2


def test_haiku_yes_keeps_its_confidence_and_values_are_clamped() -> None:
    raw = [
        {"id": "is_advice", "yes": True, "confidence": 1.7},
        {"id": "useful", "score": 9, "confidence": 0.5},
    ]

    answers = _haiku()._to_answers([NOUL, SCORE], raw)

    assert answers[0].probabilities == 1.0
    assert answers[1].probabilities == 1.0


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ([{"id": "other", "yes": True, "confidence": 0.9}], "did not answer question 'topic'"),
        ([{"id": "topic", "choice": "weather", "confidence": 0.9}], "not one of the options"),
        ([{"id": "topic", "yes": True, "confidence": 0.9}], "unusable choice answer"),
    ],
)
def test_haiku_unusable_answers_raise_classifier_error(
    raw: list[dict[str, Any]], message: str
) -> None:
    with pytest.raises(ClassifierError, match=message):
        _haiku()._to_answers([CHOICE], raw)


def test_haiku_request_disables_thinking_and_constrains_the_output() -> None:
    provider = _haiku()
    sent: dict[str, Any] = {}

    def _create(**params: Any) -> SimpleNamespace:
        sent.update(params)
        return _haiku_reply(
            json.dumps(
                {
                    "answers": [
                        {"id": "is_advice", "yes": True, "confidence": 0.95},
                        {"id": "topic", "choice": "clinic", "confidence": 0.9},
                        {"id": "useful", "score": 2, "confidence": 0.8},
                    ]
                }
            )
        )

    provider._client = SimpleNamespace(messages=SimpleNamespace(create=_create))

    answers = provider.classify("מצב לבדיקה", QUESTIONS)

    assert [answer.probabilities for answer in answers] == [
        0.95,
        {"clinic": 0.9, "greeting": 0.0, "price": 0.0},
        1.0,
    ]
    settings = CLASSIFIER_CONFIG["anthropic"]
    assert sent["model"] == "claude-haiku-5-5"
    assert sent["thinking"] == {"type": "disabled"}
    assert sent["max_tokens"] == settings["max_tokens"]
    assert sent["output_config"] == {
        "effort": settings["effort"],
        "format": {"type": "json_schema", "schema": CLASSIFIER_ANSWERS_SCHEMA},
    }
    questions_block, state_block = sent["messages"][0]["content"]
    assert '"levels"' in questions_block["text"] and '"options"' in questions_block["text"]
    assert state_block == {"type": "text", "text": "<state>\nמצב לבדיקה\n</state>"}


@pytest.mark.parametrize(
    ("reply", "message"),
    [
        (_haiku_reply("", "refusal"), "declined to classify .*category=cyber"),
        (_haiku_reply('{"answers": [', "max_tokens"), "cut off at max_tokens"),
        (_haiku_reply("not json"), "not the expected JSON"),
    ],
)
def test_haiku_bad_replies_raise_classifier_error(
    reply: SimpleNamespace, message: str
) -> None:
    provider = _haiku()
    provider._client = SimpleNamespace(
        messages=SimpleNamespace(create=lambda **params: reply)
    )

    with pytest.raises(ClassifierError, match=message):
        provider.classify("state", [NOUL])


# ----------------------------------------------------- TypeSafe (Jev) provider
# The wire format returned by POST /v1/systemone (captured 2026-11-10).
JEV_RESPONSE = {
    "model": "jev-1.14.0",
    "answers": {
        "is_advice": {"type": "noul", "noul": 0.98},
        "topic": {
            "type": "choice",
            "choice": "clinic",
            "confidence": 0.9,
            "probabilities": {"clinic": 0.95, "greeting": 0.0, "price": 0.05},
        },
        "useful": {
            "type": "score",
            "score": 1.5,
            "confidence": 0.5,
            "legend": {"0": "small talk", "1": "some", "2": "concrete"},
            "probabilities": {"0": 0.0, "1": 0.5, "2": 0.5},
        },
    },
    "usage": {"input_tokens": 400, "output_tokens": 60},
}


def _jev() -> TypeSafeClassifierProvider:
    return TypeSafeClassifierProvider(model="jev-latest", logger=LOGGER)


def test_jev_questions_map_one_to_one() -> None:
    provider = _jev()

    assert provider._sdk_question(NOUL) == Noul(instructions=NOUL.text)
    assert provider._sdk_question(CHOICE) == Choice(
        instructions=CHOICE.text, criteria={"clinic": None, "greeting": None, "price": None}
    )
    assert provider._sdk_question(SCORE) == Score(
        instructions=SCORE.text, criteria=["small talk", "some", "concrete"]
    )


def test_jev_response_converts_to_c4_probabilities() -> None:
    provider = _jev()
    sent: dict[str, Any] = {}

    def _system_one(state: str, questions: dict[str, Any]) -> SystemOneResponse:
        sent.update(state=state, questions=questions)
        return SystemOneResponse.model_validate_json(json.dumps(JEV_RESPONSE))

    provider._client = SimpleNamespace(system_one=_system_one)

    answers = provider.classify("מצב לבדיקה", QUESTIONS)

    assert sent["state"] == "מצב לבדיקה"
    assert list(sent["questions"]) == ["is_advice", "topic", "useful"]
    assert answers == [
        Answer("is_advice", 0.98),
        Answer("topic", {"clinic": 0.95, "greeting": 0.0, "price": 0.05}),
        Answer("useful", 0.75),  # position 1.5 on levels 0..2
    ]


def test_jev_sdk_errors_become_classifier_errors() -> None:
    provider = _jev()

    def _system_one(state: str, questions: dict[str, Any]) -> SystemOneResponse:
        raise TypeSafeError("429 Too Many Requests")

    provider._client = SimpleNamespace(system_one=_system_one)

    with pytest.raises(ClassifierError, match="TypeSafe jev-latest request failed"):
        provider.classify("state", [NOUL])


def test_jev_missing_answer_becomes_a_classifier_error() -> None:
    provider = _jev()
    partial = {**JEV_RESPONSE, "answers": {"is_advice": JEV_RESPONSE["answers"]["is_advice"]}}
    provider._client = SimpleNamespace(
        system_one=lambda state, questions: SystemOneResponse.model_validate_json(
            json.dumps(partial)
        )
    )

    with pytest.raises(ClassifierError, match=r"no answer for \['topic'\]"):
        provider.classify("state", [NOUL, CHOICE])
