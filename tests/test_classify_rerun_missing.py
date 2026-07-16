"""Rerun of failed step-2 classifications, selected by their reason field."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from step_2_classify import rerun_missing
from utils.paths import (
    EDITED_CLASSIFIED_PATH,
    ORIGINAL_CLASSIFIED_PATH,
)


class _FakeLLM:
    """Minimal stand-in: every classify call returns a knowledge-bearing result."""

    provider = "fake"
    model = "fake-1"

    def set_logger(self, _logger: object) -> None:
        pass

    def supports_batch(self) -> bool:
        return False

    def complete_json(
        self, system: str, user: str, task: str = "", **_kwargs: object
    ) -> dict[str, Any]:
        return {"is_knowledge_bearing": True, "topic_tags": ["usa"],
                "entities": [], "reason": "recovered"}


def _thread(tid: str) -> dict:
    return {
        "thread_id": tid,
        "start_time": "2022-01-01T10:00:00",
        "last_time": "2022-01-01T11:00:00",
        "num_messages": 3,
        "num_unique_senders": 2,
        "message_ids": [0, 1],
        "messages": [
            {"id": "0", "datetime": "2022-01-01T10:00:00", "sender": "a",
             "content": "מומלץ מאוד עורך דין בקליפורניה"},
            {"id": "1", "datetime": "2022-01-01T10:05:00", "sender": "b",
             "content": "כמה זה עלה?"},
        ],
    }


def _classified() -> dict:
    return {
        "threads": [
            {"thread_id": "t-failed", "is_knowledge_bearing": None, "topic_tags": [],
             "emergent_tags": [], "entities": [], "passed_heuristic": True,
             "reason": "classification_error: Expecting ',' delimiter: line 11 column 10"},
            {"thread_id": "t-heuristic", "is_knowledge_bearing": False, "topic_tags": [],
             "emergent_tags": [], "entities": [], "passed_heuristic": False,
             "reason": "filtered_by_heuristic"},
        ],
        "metadata": {"knowledge_bearing_count": 0},
    }


def test_only_failed_records_recover_and_both_files_update(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    ORIGINAL_CLASSIFIED_PATH.parent.mkdir(parents=True, exist_ok=True)
    ORIGINAL_CLASSIFIED_PATH.write_text(json.dumps(_classified()), encoding="utf-8")
    EDITED_CLASSIFIED_PATH.write_text(json.dumps(_classified()), encoding="utf-8")

    threads_path = tmp_path / "threads.json"
    threads_path.write_text(
        json.dumps({"threads": [_thread("t-failed"), _thread("t-heuristic")]}),
        encoding="utf-8",
    )

    summary = rerun_missing.run(
        input_path=threads_path, classified_path=None, llm=_FakeLLM(), use_batch=False
    )

    assert summary == {"failed_threads": 1, "recovered_threads": 1}

    for path in (ORIGINAL_CLASSIFIED_PATH, EDITED_CLASSIFIED_PATH):
        payload = json.loads(path.read_text(encoding="utf-8"))
        by_id = {r["thread_id"]: r for r in payload["threads"]}

        recovered = by_id["t-failed"]
        assert recovered["is_knowledge_bearing"] is True
        assert not recovered["reason"].startswith("classification_error")

        untouched = by_id["t-heuristic"]
        assert untouched["is_knowledge_bearing"] is False
        assert untouched["reason"] == "filtered_by_heuristic"

        assert payload["metadata"]["knowledge_bearing_count"] == 1
