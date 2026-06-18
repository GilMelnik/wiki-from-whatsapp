"""Tests for wiki_build.reclassify_edited."""

from __future__ import annotations

import json
from pathlib import Path

from wiki_build.llm_client import LLMClient
from wiki_build.reclassify_edited import run


def _thread(tid: str, content: str = "פונדקאות בישראל") -> dict:
    return {
        "thread_id": tid,
        "start_time": "2024-01-01T10:00:00",
        "last_time": "2024-01-01T11:00:00",
        "num_messages": 3,
        "num_unique_senders": 2,
        "messages": [
            {
                "id": "1",
                "datetime": "2024-01-01T10:00:00",
                "sender": "a",
                "content": content,
            }
        ],
    }


def _classified(tid: str, *, knowledge: bool, tags: list[str] | None = None) -> dict:
    return {
        "thread_id": tid,
        "start_time": "2024-01-01T10:00:00",
        "last_time": "2024-01-01T11:00:00",
        "num_messages": 3,
        "num_unique_senders": 2,
        "passed_heuristic": True,
        "is_knowledge_bearing": knowledge,
        "topic_tags": tags or (["overview"] if knowledge else []),
        "emergent_tags": [],
        "entities": [],
        "reason": "manual" if not knowledge else "old tags",
    }


def test_reclassify_skips_false_and_updates_true(tmp_path: Path) -> None:
    threads_path = tmp_path / "threads_edited.json"
    classified_path = tmp_path / "threads_classified_edited.json"

    threads_path.write_text(
        json.dumps(
            {
                "threads": [
                    _thread("keep-false", "רכילות"),
                    _thread("reclassify-me", "פונדקאות בישראל ועורך דין"),
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    classified_path.write_text(
        json.dumps(
            {
                "threads": [
                    _classified("keep-false", knowledge=False, tags=[]),
                    _classified("reclassify-me", knowledge=True, tags=["old-topic"]),
                ],
                "metadata": {"source": "test"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    meta = run(
        threads_path=threads_path,
        classified_path=classified_path,
        llm=LLMClient(provider="mock"),
        use_batch=False,
    )

    assert meta["skipped_not_knowledge_bearing"] == 1
    assert meta["reclassified_by_llm"] == 1

    payload = json.loads(classified_path.read_text(encoding="utf-8"))
    by_id = {record["thread_id"]: record for record in payload["threads"]}

    assert by_id["keep-false"]["is_knowledge_bearing"] is False
    assert by_id["keep-false"]["topic_tags"] == []
    assert by_id["keep-false"]["reason"] == "manual"

    assert by_id["reclassify-me"]["is_knowledge_bearing"] is True
    assert "old-topic" not in by_id["reclassify-me"]["topic_tags"]
    assert by_id["reclassify-me"]["topic_tags"]
