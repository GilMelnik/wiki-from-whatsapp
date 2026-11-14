"""Step 2 (contract C2 + C14): heuristic, threshold, output shape and incremental re-runs.

Offline: the classifier's provider is a fake that scores a thread 0.6 + 0.1 per mention of
"lawyer" (0.1 without one) and counts every state it is asked about.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from step_2_classify import run as step
from step_2_classify.labels import MANUAL_FINGERPRINT, content_fingerprint, hand_record
from utils import reconcile
from utils.json_io import write_json_file
from utils.llm_client import Answer, Classifier, Question
from utils.llm_client.providers.base import ClassifierProvider
from utils.paths import Artifact

LOGGER = logging.getLogger("test_classify_run")
C2_RECORD_KEYS = {"thread_id", "passed_heuristic", "p_knowledge", "is_knowledge_bearing"}


class _FakeProvider(ClassifierProvider):
    name = "fake"

    def __init__(self) -> None:
        super().__init__("fake-1", LOGGER)
        self.calls: list[str] = []

    def _create_client(self) -> None:
        return None

    def classify(self, state: str, questions: Sequence[Question]) -> list[Answer]:
        self.calls.append(state)
        mentions = state.count("lawyer")
        p_yes = round(0.6 + 0.1 * mentions, 2) if mentions else 0.1
        return [Answer(question.id, p_yes) for question in questions]


def _message(message_id: str, sender: str, content: str) -> dict[str, Any]:
    return {"id": message_id, "datetime": "2024-03-01T10:00:00", "sender": sender, "content": content}


def _thread(thread_id: str, *contents: str, senders: int = 2) -> dict[str, Any]:
    messages = [
        _message(f"{thread_id}-{index}", f"person-{index % senders}", content)
        for index, content in enumerate(contents)
    ]
    return {"thread_id": thread_id, "messages": messages}


KNOWLEDGE = _thread("t-1", "which lawyer?", "the lawyer in Haifa was great", "thanks")
CHATTER = _thread("t-2", "good morning", "morning!", "coffee?")
TOO_SHORT = _thread("t-3", "a lawyer", "ok")
MONOLOGUE = _thread("t-4", "lawyer one", "lawyer two", "lawyer three", senders=1)


@pytest.fixture
def provider(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _FakeProvider:
    fake = _FakeProvider()

    def for_stage(stage: str, *, logger: logging.Logger | None = None) -> Classifier:
        classifier = Classifier(
            "typesafe",
            "jev-latest",
            logger=logger,
            use_cache=False,
            cache_dir=tmp_path / "cache",
            failure_log=tmp_path / "failures.jsonl",
        )
        classifier._provider = fake
        return classifier

    monkeypatch.setattr(step.Classifier, "for_stage", staticmethod(for_stage))
    monkeypatch.setattr(step, "THREADS", Artifact(tmp_path / "threads.json", tmp_path / "threads_edited.json"))
    monkeypatch.setattr(
        step, "CLASSIFIED", Artifact(tmp_path / "classified.json", tmp_path / "classified_edited.json")
    )
    monkeypatch.setattr(reconcile, "BACKUPS_DIR", tmp_path / "backups")
    return fake


def _write_threads(*threads: dict[str, Any], edited: bool = False) -> None:
    artifact = step.THREADS
    write_json_file({"threads": list(threads), "metadata": {}}, artifact.edited if edited else artifact.original)


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _records(path: Path) -> dict[str, dict[str, Any]]:
    return {record["thread_id"]: record for record in _read(path)["threads"]}


def _label(thread_id: str, value: bool) -> None:
    """What the thread tagger does: flip one label in the edited copy."""
    path = step.CLASSIFIED.edited
    payload = _read(step.CLASSIFIED.original if not path.exists() else path)
    for record in payload["threads"]:
        if record["thread_id"] == thread_id:
            record["is_knowledge_bearing"] = value
    write_json_file(payload, path)


def _hand_label(thread: dict[str, Any], value: bool) -> None:
    """What the conversations page does: the owner's label for the thread as it is now."""
    path = step.CLASSIFIED.edited
    payload = _read(step.CLASSIFIED.original if not path.exists() else path)
    own = _records(step.CLASSIFIED.original).get(thread["thread_id"])
    others = [r for r in payload["threads"] if r["thread_id"] != thread["thread_id"]]
    write_json_file({**payload, "threads": [*others, hand_record(thread, value, own)]}, path)


def test_output_follows_c2(provider: _FakeProvider) -> None:
    _write_threads(KNOWLEDGE, CHATTER)
    metadata = step.run()

    payload = _read(step.CLASSIFIED.original)
    assert set(payload) == {"threads", "metadata"}
    assert [set(record) for record in payload["threads"]] == [C2_RECORD_KEYS, C2_RECORD_KEYS]
    assert set(payload["metadata"]["inputs"]) == {"t-1", "t-2"}
    assert payload["metadata"]["classifier"] == {
        "stage": "knowledge", "provider": "typesafe", "model": "jev-latest"
    }
    assert payload["metadata"]["threshold"] == step.CLASSIFY_CONFIG["threshold"]
    assert metadata["thread_count"] == 2 and metadata["knowledge_bearing_count"] == 1
    assert not step.CLASSIFIED.edited.exists()


def test_heuristic_rejects_without_asking(
    provider: _FakeProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(step.CLASSIFY_CONFIG, "min_messages", 3)
    monkeypatch.setitem(step.CLASSIFY_CONFIG, "min_senders", 2)
    _write_threads(KNOWLEDGE, TOO_SHORT, MONOLOGUE)
    step.run()

    records = _records(step.CLASSIFIED.original)
    assert len(provider.calls) == 1
    for thread_id in ("t-3", "t-4"):
        assert records[thread_id] == {
            "thread_id": thread_id,
            "passed_heuristic": False,
            "p_knowledge": None,
            "is_knowledge_bearing": False,
        }


def test_threshold_is_applied(provider: _FakeProvider, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(step.CLASSIFY_CONFIG, "threshold", 0.85)
    _write_threads(KNOWLEDGE, CHATTER)
    step.run()

    records = _records(step.CLASSIFIED.original)
    assert records["t-1"]["p_knowledge"] == 0.8
    assert records["t-1"]["is_knowledge_bearing"] is False


def test_new_threshold_keeps_older_hand_labels_and_gives_them_their_fingerprint(
    provider: _FakeProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_threads(KNOWLEDGE, CHATTER)
    step.run()
    _label("t-2", True)  # an older hand label: no manual_fingerprint
    provider.calls.clear()

    monkeypatch.setitem(step.CLASSIFY_CONFIG, "threshold", 0.85)
    step.run()
    assert len(provider.calls) == 1  # t-1 only: t-2 is left to the owner's label
    edited = _records(step.CLASSIFIED.edited)
    assert edited["t-2"]["is_knowledge_bearing"] is True
    assert edited["t-2"][MANUAL_FINGERPRINT] == content_fingerprint(CHATTER)
    assert edited["t-1"]["is_knowledge_bearing"] is False
    assert _read(step.CLASSIFIED.edited)["metadata"]["threshold"] == 0.85

    monkeypatch.setitem(step.CLASSIFY_CONFIG, "threshold", 0.5)
    step.run()
    assert _records(step.CLASSIFIED.edited)["t-2"]["is_knowledge_bearing"] is True


def test_a_hand_label_is_not_asked_and_the_log_says_so(
    provider: _FakeProvider, caplog: pytest.LogCaptureFixture
) -> None:
    _write_threads(KNOWLEDGE, CHATTER)
    step.run()
    _hand_label(CHATTER, True)
    changed = _thread("t-2", "good morning", "morning!", "coffee? lawyer?")
    _write_threads(KNOWLEDGE, changed, _thread("t-5", "new", "chat", "here"))
    _hand_label(changed, False)
    _hand_label(_thread("t-5", "new", "chat", "here"), True)
    provider.calls.clear()

    with caplog.at_level(logging.INFO, logger=step.STEP_2):
        step.run()
    assert provider.calls == []
    assert "left to the owner's labels: 2; asking about 0" in caplog.text
    edited = _records(step.CLASSIFIED.edited)
    assert edited["t-2"]["is_knowledge_bearing"] is False and edited["t-5"]["is_knowledge_bearing"] is True
    original = _records(step.CLASSIFIED.original)
    assert "t-5" not in original and original["t-2"]["is_knowledge_bearing"] is False
    assert all(MANUAL_FINGERPRINT not in record for record in original.values())


def test_a_hand_label_of_a_removed_thread_is_dropped(provider: _FakeProvider) -> None:
    extra = _thread("t-5", "new", "chat", "here")
    _write_threads(KNOWLEDGE, extra)
    step.run(only=["t-1"])
    _hand_label(extra, True)  # step 2 never answered t-5

    _write_threads(KNOWLEDGE)
    step.run()
    for path in (step.CLASSIFIED.original, step.CLASSIFIED.edited):
        assert list(_records(path)) == ["t-1"]


def test_unchanged_threads_are_not_asked_again(provider: _FakeProvider) -> None:
    _write_threads(KNOWLEDGE, CHATTER)
    step.run()
    first = _read(step.CLASSIFIED.original)["threads"]
    provider.calls.clear()

    metadata = step.run()
    assert provider.calls == []
    assert metadata["classified_count"] == 0
    assert _read(step.CLASSIFIED.original)["threads"] == first


def test_changed_thread_is_asked_again(provider: _FakeProvider) -> None:
    _write_threads(KNOWLEDGE, CHATTER)
    step.run()
    provider.calls.clear()

    changed = _thread("t-2", "good morning", "morning!", "any lawyer you trust?")
    _write_threads(KNOWLEDGE, changed, edited=True)
    step.run()
    assert len(provider.calls) == 1 and "any lawyer" in provider.calls[0]
    assert _records(step.CLASSIFIED.original)["t-2"]["is_knowledge_bearing"] is True


def test_manual_label_survives_on_unchanged_thread(provider: _FakeProvider) -> None:
    _write_threads(KNOWLEDGE, CHATTER)
    step.run()
    _label("t-2", True)

    _write_threads(KNOWLEDGE, CHATTER, _thread("t-5", "new", "lawyer", "talk"))
    step.run()
    edited = _records(step.CLASSIFIED.edited)
    assert edited["t-2"]["is_knowledge_bearing"] is True
    assert edited["t-5"]["is_knowledge_bearing"] is True
    assert _records(step.CLASSIFIED.original)["t-2"]["is_knowledge_bearing"] is False


def test_manual_label_is_superseded_when_thread_changes(provider: _FakeProvider) -> None:
    _write_threads(KNOWLEDGE, CHATTER)
    step.run()
    _label("t-1", False)

    _write_threads(_thread("t-1", "which lawyer?", "the lawyer in Haifa", "and a lawyer in Tel Aviv?"), CHATTER)
    step.run()
    assert _records(step.CLASSIFIED.edited)["t-1"]["is_knowledge_bearing"] is True


def test_removed_thread_disappears(provider: _FakeProvider) -> None:
    _write_threads(KNOWLEDGE, CHATTER)
    step.run()
    _label("t-2", True)

    _write_threads(KNOWLEDGE)
    step.run()
    for path in (step.CLASSIFIED.original, step.CLASSIFIED.edited):
        payload = _read(path)
        assert [record["thread_id"] for record in payload["threads"]] == ["t-1"]
        assert set(payload["metadata"]["inputs"]) == {"t-1"}


def test_only_restricts_the_work(provider: _FakeProvider) -> None:
    _write_threads(KNOWLEDGE, CHATTER)
    metadata = step.run(only=["t-1"])
    assert len(provider.calls) == 1
    assert list(_records(step.CLASSIFIED.original)) == ["t-1"]
    assert set(metadata["inputs"]) == {"t-1"}

    provider.calls.clear()
    step.run()
    assert len(provider.calls) == 1 and "coffee" in provider.calls[0]


def test_only_keeps_the_old_fingerprint_of_skipped_threads(provider: _FakeProvider) -> None:
    _write_threads(KNOWLEDGE, CHATTER)
    old = step.run()["inputs"]
    changed = _thread("t-2", "good morning", "morning!", "tea?")
    _write_threads(KNOWLEDGE, changed)
    provider.calls.clear()

    assert step.run(only=["t-1"])["inputs"] == old
    assert provider.calls == []
    step.run()
    assert len(provider.calls) == 1 and "tea" in provider.calls[0]


def test_only_rejects_unknown_ids_and_rebuild(provider: _FakeProvider) -> None:
    _write_threads(KNOWLEDGE)
    with pytest.raises(ValueError, match="t-404"):
        step.run(only=["t-404"])
    with pytest.raises(ValueError, match="rebuild"):
        step.run(rebuild=True, only=["t-1"])


def test_rebuild_asks_everything_and_drops_manual_labels(provider: _FakeProvider) -> None:
    _write_threads(KNOWLEDGE, CHATTER)
    step.run()
    _label("t-2", True)
    provider.calls.clear()

    step.run(rebuild=True)
    assert len(provider.calls) == 2
    assert _records(step.CLASSIFIED.edited)["t-2"]["is_knowledge_bearing"] is False
    assert list((reconcile.BACKUPS_DIR).iterdir())


def test_old_classified_file_is_replaced(provider: _FakeProvider) -> None:
    """A classified file from the old step (topic tags, no metadata.inputs) is re-asked."""
    old_record = {"thread_id": "t-1", "is_knowledge_bearing": False, "topic_tags": ["x"]}
    write_json_file({"threads": [old_record], "metadata": {}}, step.CLASSIFIED.original)
    _write_threads(KNOWLEDGE)
    step.run()
    assert _records(step.CLASSIFIED.original)["t-1"]["is_knowledge_bearing"] is True
    assert set(_records(step.CLASSIFIED.original)["t-1"]) == C2_RECORD_KEYS


def test_c1_shaped_threads(provider: _FakeProvider) -> None:
    """C1 threads (``t-<wa id>``, extra fields, reactions) work; reactions are not sent."""
    thread = _thread("t-ABC123", "which lawyer?", "this one", "thanks")
    thread.update(start_time="2024-03-01T10:00:00", participants=["person-0", "person-1"])
    thread["message_ids"] = [message["id"] for message in thread["messages"]]
    thread["messages"][1]["reactions"] = [{"emoji": "x", "sender": "person-0"}]
    thread["messages"][1]["quote"] = {"id": "t-ABC123-0"}
    _write_threads(thread)
    step.run()
    assert _records(step.CLASSIFIED.original)["t-ABC123"]["is_knowledge_bearing"] is True

    thread["messages"][1]["reactions"].append({"emoji": "y", "sender": "person-1"})
    _write_threads(thread)
    provider.calls.clear()
    step.run()
    assert provider.calls == []
