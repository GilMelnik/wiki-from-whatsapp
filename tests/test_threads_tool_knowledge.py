"""The conversations tool's control for step 2's knowledge label (C2 corrected by hand).

Step 2 runs for real here, with a fake classifier provider that answers 0.6 for a
conversation mentioning cats and 0.1 otherwise (no network: the conftest guard refuses real
clients). The synthetic chat: t-a1 (about cats), t-b1 (about dogs) and t-c1 (about fish).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from step_2_classify import run as step_2
from tests.threads_tool_support import ThreadsSetup
from utils import reconcile
from utils.json_io import write_json_file
from utils.llm_client import Answer, Classifier, Question
from utils.llm_client.providers.base import ClassifierProvider
from utils.paths import Artifact

LOGGER = logging.getLogger("test_threads_tool_knowledge")


class _CatsProvider(ClassifierProvider):
    name = "fake"

    def __init__(self) -> None:
        super().__init__("fake-1", LOGGER)

    def _create_client(self) -> None:
        return None

    def classify(self, state: str, questions: Sequence[Question]) -> list[Answer]:
        return [Answer(question.id, 0.6 if "cats" in state.casefold() else 0.1) for question in questions]


@pytest.fixture
def tool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ThreadsSetup:
    """The tool, and step 2 pointed at the same files with the fake classifier."""

    def for_stage(stage: str, *, logger: logging.Logger | None = None) -> Classifier:
        classifier = Classifier(
            "typesafe", "jev-latest", logger=logger, use_cache=False,
            cache_dir=tmp_path / "cache", failure_log=tmp_path / "failures.jsonl",
        )
        classifier._provider = _CatsProvider()
        return classifier

    setup = ThreadsSetup(tmp_path)
    classified = setup.graph.node("classified")
    monkeypatch.setattr(step_2.Classifier, "for_stage", staticmethod(for_stage))
    monkeypatch.setattr(step_2, "THREADS", Artifact(setup.original, setup.edited))
    monkeypatch.setattr(step_2, "CLASSIFIED", Artifact(classified.original, classified.edited))
    monkeypatch.setattr(reconcile, "BACKUPS_DIR", tmp_path / "backups")
    return setup


def _labels(tool: ThreadsSetup, knowledge: str = "all") -> dict[str, Any]:
    return {row["thread_id"]: row["knowledge"] for row in tool.get("/threads", knowledge=knowledge)["items"]}


def _records(path: Path) -> dict[str, dict[str, Any]]:
    return {r["thread_id"]: r for r in json.loads(path.read_text(encoding="utf-8"))["threads"]}


def test_before_step_2_ran_there_is_no_label_and_one_line_says_why(tool: ThreadsSetup) -> None:
    state = tool.get("/knowledge")
    assert state["ready"] is False and "step_2_classify.run" in state["reason"]
    assert _labels(tool) == {"t-a1": None, "t-b1": None, "t-c1": None}
    assert tool.get("/threads", knowledge="yes")["items"] == []
    assert tool.get("/knowledge/t-a1") == {"thread_id": "t-a1", "label": None}
    assert "step_2_classify.run" in tool.post("/knowledge/t-a1", {"value": True}, status=409)["detail"]


def test_step_2s_answer_is_shown_with_its_probability(tool: ThreadsSetup) -> None:
    step_2.run()
    assert tool.get("/knowledge") == {"ready": True, "reason": None, "threshold": 0.18, "manual_count": 0}
    labels = _labels(tool)
    assert labels["t-a1"] == {
        "value": True, "auto": True, "p_knowledge": 0.6, "passed_heuristic": True, "manual": False
    }
    assert labels["t-b1"] | {"value": False, "auto": False, "p_knowledge": 0.1, "manual": False} == labels["t-b1"]


def test_set_by_hand_filter_and_back_to_the_automatic_answer(tool: ThreadsSetup) -> None:
    step_2.run()
    classified = tool.graph.node("classified")
    original = classified.original.read_bytes()
    answer = tool.post("/knowledge/t-b1", {"value": True})
    assert answer["label"] | {"value": True, "auto": False, "manual": True} == answer["label"]
    assert _records(classified.edited)["t-b1"]["is_knowledge_bearing"] is True
    assert classified.original.read_bytes() == original  # step 2's own answers stay as they are
    assert list(_labels(tool, "yes")) == ["t-a1", "t-b1"]
    assert list(_labels(tool, "no")) == ["t-c1"]
    assert list(_labels(tool, "manual")) == ["t-b1"]
    assert tool.get("/knowledge")["manual_count"] == 1

    back = tool.post("/knowledge/t-b1", {"value": None})["label"]
    assert back["value"] is False and back["manual"] is False
    assert _records(classified.edited)["t-b1"] == _records(classified.original)["t-b1"]
    assert list(_labels(tool, "manual")) == []
    # Setting the answer step 2 gave anyway is the automatic answer, not a correction.
    assert tool.post("/knowledge/t-a1", {"value": True})["label"]["manual"] is False


def test_a_label_set_by_hand_survives_a_step_2_rerun(tool: ThreadsSetup) -> None:
    step_2.run()
    tool.post("/knowledge/t-a1", {"value": False})
    tool.post("/knowledge/t-b1", {"value": True})
    threads = json.loads(tool.original.read_text(encoding="utf-8"))
    threads["threads"][2]["messages"][0]["content"] = "Fish tanks, and cats"  # t-c1 changed
    write_json_file(threads, tool.original)
    step_2.run()  # asks about t-c1 again; the other two keep step 2's answer
    edited = _records(tool.graph.node("classified").edited)
    assert edited["t-a1"]["is_knowledge_bearing"] is False
    assert edited["t-b1"]["is_knowledge_bearing"] is True
    assert edited["t-c1"]["p_knowledge"] == 0.6 and edited["t-c1"]["is_knowledge_bearing"] is True
    fresh = tool.new_client()  # a restarted server reads the same
    labels = {row["thread_id"]: row["knowledge"] for row in fresh.get("/api/threads/threads").json()["items"]}
    assert labels["t-a1"]["manual"] and labels["t-b1"]["manual"] and not labels["t-c1"]["manual"]


def test_labels_are_refused_while_step_2_runs(tool: ThreadsSetup) -> None:
    step_2.run()
    tool.client = tool.new_client(busy="classified")
    detail = tool.post("/knowledge/t-a1", {"value": False}, status=423)["detail"]
    assert "Step 2" in detail
    assert not tool.graph.node("classified").edited.exists()
    assert tool.get("/knowledge/t-a1")["label"]["value"] is True  # reading still works


def test_a_conversation_step_2_has_not_seen_cannot_be_labelled(tool: ThreadsSetup) -> None:
    step_2.run()
    tool.post("/move", {"message_ids": ["a3"], "target_id": None})  # a new conversation
    new_id = next(tid for tid in _labels(tool) if tid not in ("t-a1", "t-b1", "t-c1"))
    assert tool.get(f"/knowledge/{new_id}")["label"] is None
    assert "Run step 2 first" in tool.post(f"/knowledge/{new_id}", {"value": True}, status=404)["detail"]
