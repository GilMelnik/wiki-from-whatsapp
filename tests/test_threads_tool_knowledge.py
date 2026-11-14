"""The conversations tool's knowledge labels: the owner labels by hand, step 2 answers the rest.

Step 2 runs for real here, with a fake classifier provider that answers 0.6 for a
conversation mentioning cats and 0.1 otherwise, and counts what it is asked (no network: the
conftest guard refuses real clients). The synthetic chat: t-a1 (about cats: a1, a2, a3), t-b1
(about dogs: b1, b2) and t-c1 (about fish: c1).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from step_2_classify import run as step_2
from step_2_classify.labels import MANUAL_FINGERPRINT
from tests.threads_tool_support import ThreadsSetup
from utils import reconcile
from utils.json_io import write_json_file
from utils.llm_client import Answer, Classifier, Question
from utils.llm_client.providers.base import ClassifierProvider
from utils.paths import Artifact

LOGGER = logging.getLogger("test_threads_tool_knowledge")
SPLIT_1, SPLIT_2 = "t-a1-split-1", "t-a1-split-2"


class _CatsProvider(ClassifierProvider):
    name = "fake"

    def __init__(self) -> None:
        super().__init__("fake-1", LOGGER)
        self.calls: list[str] = []

    def _create_client(self) -> None:
        return None

    def classify(self, state: str, questions: Sequence[Question]) -> list[Answer]:
        self.calls.append(state)
        return [Answer(question.id, 0.6 if "cats" in state.casefold() else 0.1) for question in questions]


@pytest.fixture
def provider() -> _CatsProvider:
    return _CatsProvider()


@pytest.fixture
def tool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: _CatsProvider) -> ThreadsSetup:
    """The tool, and step 2 pointed at the same files with the fake classifier."""

    def for_stage(stage: str, *, logger: logging.Logger | None = None) -> Classifier:
        classifier = Classifier(
            "typesafe", "jev-latest", logger=logger, use_cache=False,
            cache_dir=tmp_path / "cache", failure_log=tmp_path / "failures.jsonl",
        )
        classifier._provider = provider
        return classifier

    setup = ThreadsSetup(tmp_path)
    classified = setup.graph.node("classified")
    monkeypatch.setattr(step_2.Classifier, "for_stage", staticmethod(for_stage))
    monkeypatch.setattr(step_2, "THREADS", Artifact(setup.original, setup.edited))
    monkeypatch.setattr(step_2, "CLASSIFIED", Artifact(classified.original, classified.edited))
    monkeypatch.setattr(reconcile, "BACKUPS_DIR", tmp_path / "backups")
    return setup


def _labels(tool: ThreadsSetup, knowledge: str = "all", **params: Any) -> dict[str, Any]:
    answer = tool.get("/threads", knowledge=knowledge, **params)
    return {row["thread_id"]: row["knowledge"] for row in answer["items"]}


def _records(path: Path) -> dict[str, dict[str, Any]]:
    return {r["thread_id"]: r for r in json.loads(path.read_text(encoding="utf-8"))["threads"]}


def _counts(tool: ThreadsSetup) -> tuple[int, int, int]:
    state = tool.get("/knowledge")
    return state["needs_label_count"], state["changed_count"], state["manual_count"]


def _asked(provider: _CatsProvider) -> int:
    asked = len(provider.calls)
    provider.calls.clear()
    return asked


def test_before_step_2_ran_there_is_no_label_and_one_line_says_why(tool: ThreadsSetup) -> None:
    state = tool.get("/knowledge")
    assert state["ready"] is False and "step_2_classify.run" in state["reason"]
    assert _labels(tool) == {"t-a1": None, "t-b1": None, "t-c1": None}
    assert tool.get("/threads", knowledge="yes")["items"] == []
    assert tool.get("/knowledge/t-a1") == {"thread_id": "t-a1", "label": None}
    assert "step_2_classify.run" in tool.post("/knowledge/t-a1", {"value": True}, status=409)["detail"]


def test_step_2s_answer_is_shown_with_its_probability(tool: ThreadsSetup) -> None:
    step_2.run()
    assert tool.get("/knowledge") == {
        "ready": True, "reason": None, "threshold": 0.18,
        "manual_count": 0, "needs_label_count": 0, "changed_count": 0,
    }
    labels = _labels(tool)
    assert labels["t-a1"] == {
        "value": True, "manual": False, "current": True,
        "auto": True, "p_knowledge": 0.6, "passed_heuristic": True,
    }
    assert labels["t-b1"] | {"value": False, "auto": False, "p_knowledge": 0.1, "manual": False} == labels["t-b1"]


def test_a_label_by_hand_is_the_owners_also_when_step_2_said_the_same(tool: ThreadsSetup) -> None:
    step_2.run()
    classified = tool.graph.node("classified")
    original = classified.original.read_bytes()
    same = tool.post("/knowledge/t-a1", {"value": True})["label"]
    assert same | {"value": True, "auto": True, "manual": True, "current": True} == same
    flipped = tool.post("/knowledge/t-b1", {"value": True})["label"]
    assert flipped | {"value": True, "auto": False, "manual": True} == flipped
    edited = _records(classified.edited)
    assert MANUAL_FINGERPRINT in edited["t-a1"] and edited["t-b1"]["is_knowledge_bearing"] is True
    assert classified.original.read_bytes() == original  # step 2's own answers stay as they are
    assert list(_labels(tool, "yes")) == ["t-a1", "t-b1"]
    assert list(_labels(tool, "no")) == ["t-c1"]
    assert list(_labels(tool, "manual")) == ["t-a1", "t-b1"]
    assert _counts(tool) == (0, 0, 2)

    back = tool.post("/knowledge/t-b1", {"value": None})["label"]
    assert back["value"] is False and back["manual"] is False and back["current"] is True
    assert _records(classified.edited)["t-b1"] == _records(classified.original)["t-b1"]
    assert list(_labels(tool, "manual")) == ["t-a1"]


def test_unknown_conversations_are_404(tool: ThreadsSetup) -> None:
    step_2.run()
    assert "t-nope" in tool.get("/knowledge/t-nope", status=404)["detail"]
    assert "t-nope" in tool.post("/knowledge/t-nope", {"value": True}, status=404)["detail"]


def test_labels_are_refused_while_step_2_runs(tool: ThreadsSetup) -> None:
    step_2.run()
    tool.client = tool.new_client(busy="classified")
    detail = tool.post("/knowledge/t-a1", {"value": False}, status=423)["detail"]
    assert "Step 2" in detail
    assert not tool.graph.node("classified").edited.exists()
    assert tool.get("/knowledge/t-a1")["label"]["value"] is True  # reading still works


def test_the_owners_flow_split_label_and_let_step_2_answer_the_rest(
    tool: ThreadsSetup, provider: _CatsProvider
) -> None:
    classified = tool.graph.node("classified")
    step_2.run()
    assert _asked(provider) == 3

    # The owner moves a2 and a3 out of A (A changed, A-split-1 new), labels A-split-1 by hand,
    # labels B with the value step 2 gave, and leaves A alone.
    assert tool.post("/move", {"message_ids": ["a2", "a3"], "target_id": None})["created"] == SPLIT_1
    assert tool.get(f"/knowledge/{SPLIT_1}")["label"] is None  # no label yet
    assert tool.post(f"/knowledge/{SPLIT_1}", {"value": False})["label"] == {
        "value": False, "manual": True, "current": True,
        "auto": None, "p_knowledge": None, "passed_heuristic": None,
    }
    assert tool.post("/knowledge/t-b1", {"value": False})["label"]["manual"] is True
    split_record = _records(classified.edited)[SPLIT_1]
    assert split_record == {
        "thread_id": SPLIT_1, "passed_heuristic": False, "p_knowledge": None,
        "is_knowledge_bearing": False, MANUAL_FINGERPRINT: split_record[MANUAL_FINGERPRINT],
    }
    needs = _labels(tool, "needs_label")
    assert list(needs) == ["t-a1"] and needs["t-a1"]["current"] is False and needs["t-a1"]["manual"] is False
    assert sorted(_labels(tool, "changed")) == ["t-a1", SPLIT_1]
    assert _counts(tool) == (1, 2, 2)

    # The owner is done: one step-2 run asks about A only.
    step_2.run()
    assert _asked(provider) == 1
    assert _labels(tool, "needs_label") == {}
    labels = _labels(tool)
    assert labels["t-b1"]["manual"] and labels[SPLIT_1]["manual"] and not labels["t-a1"]["manual"]
    assert all(label["current"] for label in labels.values())
    assert SPLIT_1 not in _records(classified.original)  # the step's own file has no hand label
    assert not any(MANUAL_FINGERPRINT in r for r in _records(classified.original).values())

    # A message of A-split-1 moves again: its hand label no longer holds; step 2 replaces it.
    assert tool.post("/move", {"message_ids": ["a3"], "target_id": None})["created"] == SPLIT_2
    stale = _labels(tool, "needs_label")
    assert sorted(stale) == [SPLIT_1, SPLIT_2]
    assert stale[SPLIT_1] | {"manual": True, "current": False} == stale[SPLIT_1]
    tool.post(f"/knowledge/{SPLIT_2}", {"value": True})
    step_2.run()
    assert _asked(provider) == 1
    edited = _records(classified.edited)
    assert edited[SPLIT_1] == _records(classified.original)[SPLIT_1] and MANUAL_FINGERPRINT not in edited[SPLIT_1]
    assert edited[SPLIT_1]["is_knowledge_bearing"] is True  # "I agree about cats"
    assert MANUAL_FINGERPRINT in edited[SPLIT_2] and SPLIT_2 not in _records(classified.original)

    # Merged back into A: A needs a label; after the run no record of the parts is left.
    tool.post("/merge", {"thread_ids": ["t-a1", SPLIT_1, SPLIT_2]})
    assert list(_labels(tool, "needs_label")) == ["t-a1"]
    step_2.run()
    assert _asked(provider) == 1
    for path in (classified.original, classified.edited):
        assert sorted(_records(path)) == ["t-a1", "t-b1", "t-c1"]
    assert _labels(tool, "needs_label") == {}
    assert tool.new_client().get("/api/threads/knowledge").json()["manual_count"] == 1  # B


def test_a_new_threshold_does_not_make_a_hand_label_stale(
    tool: ThreadsSetup, provider: _CatsProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    step_2.run()
    tool.post("/knowledge/t-b1", {"value": True})
    _asked(provider)
    monkeypatch.setitem(step_2.CLASSIFY_CONFIG, "threshold", 0.7)
    assert _labels(tool, "needs_label") == {}  # the page judges by the settings step 2 used
    step_2.run()
    assert _asked(provider) == 2  # t-a1 and t-c1 are decided again; t-b1 is the owner's
    assert _labels(tool, "needs_label") == {}
    label = _labels(tool)["t-b1"]
    assert label | {"value": True, "manual": True, "current": True} == label
    assert _labels(tool)["t-a1"]["value"] is False  # 0.6 < 0.7


def test_a_hand_label_on_a_conversation_step_1_changed_spares_the_question(
    tool: ThreadsSetup, provider: _CatsProvider
) -> None:
    step_2.run()
    threads = json.loads(tool.original.read_text(encoding="utf-8"))
    threads["threads"][2]["messages"][0]["content"] = "Fish tanks, and cats"  # t-c1, by step 1
    write_json_file(threads, tool.original)
    assert list(_labels(tool, "needs_label")) == ["t-c1"]
    assert _labels(tool, "changed") == {}  # not the owner's edit
    tool.post("/knowledge/t-c1", {"value": False})
    _asked(provider)
    step_2.run()
    assert _asked(provider) == 0
    label = _labels(tool)["t-c1"]
    assert label | {"value": False, "manual": True, "current": True} == label


def test_labelling_under_a_filter_keeps_the_place_in_the_list(tool: ThreadsSetup) -> None:
    step_2.run()
    tool.post("/move", {"message_ids": ["a3"], "target_id": None})
    assert list(_labels(tool, "needs_label")) == ["t-a1", SPLIT_1]
    tool.post("/knowledge/t-a1", {"value": True})
    assert list(_labels(tool, "needs_label")) == [SPLIT_1]
    kept = _labels(tool, "needs_label", keep=["t-a1"])
    assert list(kept) == ["t-a1", SPLIT_1] and kept["t-a1"]["current"] is True


def test_taking_a_label_away_without_step_2s_answer_leaves_none(tool: ThreadsSetup) -> None:
    step_2.run()
    tool.post("/move", {"message_ids": ["a3"], "target_id": None})
    tool.post(f"/knowledge/{SPLIT_1}", {"value": True})
    assert tool.post(f"/knowledge/{SPLIT_1}", {"value": None})["label"] is None
    assert SPLIT_1 not in _records(tool.graph.node("classified").edited)
    assert SPLIT_1 in _labels(tool, "needs_label")
