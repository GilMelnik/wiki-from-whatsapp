"""Chronological-view endpoints of the thread tagger: move by WhatsApp id, window threads."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from step_1_threads_split.review import gold_windows as gold_module
from step_1_threads_split.review.operations import recompute_thread_stats
from step_1_threads_split.review.server import app, configure_store

WINDOW = {"start": "2025-01-01T10:00", "end": "2025-01-01T12:00"}

# Two interleaved conversations a* and b*, and c* later; positional ids differ from WhatsApp ids.
THREADS = {
    "t-a": [("a1", "10:00"), ("a2", "10:10"), ("a3", "10:20"), ("a4", "10:30")],
    "t-b": [("b1", "10:05"), ("b2", "10:15"), ("b3", "10:25")],
    "t-c": [("c1", "13:00")],
}


def _payload() -> dict[str, Any]:
    threads = []
    for thread_id, rows in THREADS.items():
        messages = [
            {"id": wa_id, "datetime": f"2025-01-01T{hhmm}:00", "sender": "s", "content": "c"}
            for wa_id, hhmm in rows
        ]
        thread = {
            "thread_id": thread_id,
            "messages": messages,
            "message_ids": [f"{thread_id}:{i}" for i in range(len(messages))],
        }
        threads.append(recompute_thread_stats(thread))
    return {"threads": threads, "metadata": {}}


@pytest.fixture
def tagger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    threads_path = tmp_path / "threads.json"
    threads_path.write_text(json.dumps(_payload()), encoding="utf-8")
    monkeypatch.setitem(gold_module.THREADS_CONFIG, "gold_windows", [WINDOW])
    monkeypatch.setattr(gold_module, "GOLD_PROGRESS_PATH", tmp_path / "gold_progress.json")
    configure_store(inspect_only=True, threads_path=threads_path)
    yield TestClient(app)
    configure_store(inspect_only=False, threads_path=None)


def _on_disk(tmp_path: Path) -> dict[str, list[str]]:
    payload = json.loads((tmp_path / "threads.json").read_text(encoding="utf-8"))
    return {t["thread_id"]: [m["id"] for m in t["messages"]] for t in payload["threads"]}


def _move(client: TestClient, ids: list[str], target: str | None) -> Any:
    return client.post("/api/threads/move-by-ids", json={"message_ids": ids, "target_id": target})


def test_move_from_two_threads_non_contiguous_into_a_target(
    tagger: TestClient, tmp_path: Path
) -> None:
    response = _move(tagger, ["b3", "a2", "a4"], "t-c")
    assert response.status_code == 200
    body = response.json()
    assert (body["target_id"], body["created"], body["removed_ids"]) == ("t-c", False, [])
    assert [m["id"] for m in body["target"]["messages"]] == ["a2", "b3", "a4", "c1"]
    # Positional ids travel with their messages.
    assert body["target"]["message_ids"] == ["t-a:1", "t-b:2", "t-a:3", "t-c:0"]
    assert _on_disk(tmp_path) == {
        "t-a": ["a1", "a3"],
        "t-b": ["b1", "b2"],
        "t-c": ["a2", "b3", "a4", "c1"],
    }


def test_move_to_a_new_thread_splits_off_the_earliest_source(
    tagger: TestClient, tmp_path: Path
) -> None:
    body = _move(tagger, ["b2", "a1", "a3"], None).json()
    assert (body["target_id"], body["created"]) == ("t-a-split-1", True)
    assert _on_disk(tmp_path) == {
        "t-a": ["a2", "a4"],
        "t-b": ["b1", "b3"],
        "t-c": ["c1"],
        "t-a-split-1": ["a1", "b2", "a3"],
    }


def test_new_thread_reuses_a_wholly_selected_source_and_removes_emptied_threads(
    tagger: TestClient, tmp_path: Path
) -> None:
    body = _move(tagger, ["a1", "a2", "a3", "a4", "c1"], None).json()
    assert (body["target_id"], body["created"], body["removed_ids"]) == ("t-a", False, ["t-c"])
    assert _on_disk(tmp_path) == {"t-a": ["a1", "a2", "a3", "a4", "c1"], "t-b": ["b1", "b2", "b3"]}


@pytest.mark.parametrize(
    ("ids", "target", "detail"),
    [
        (["a1", "zz"], "t-b", "unknown message ids"),
        (["a1"], "t-zz", "unknown target thread id"),
        (["b1", "b2"], "t-b", "already all in t-b"),
        (["c1"], None, "already all in t-c"),
    ],
)
def test_rejected_moves_change_nothing(
    tagger: TestClient, tmp_path: Path, ids: list[str], target: str | None, detail: str
) -> None:
    before = _on_disk(tmp_path)
    response = _move(tagger, ids, target)
    assert response.status_code == 400
    assert detail in response.json()["detail"]
    assert _on_disk(tmp_path) == before


def test_window_threads_have_their_messages_in_window_order(tagger: TestClient) -> None:
    body = tagger.get("/api/gold/windows/0/threads").json()
    assert (body["index"], body["start"], body["end"]) == (0, WINDOW["start"], WINDOW["end"])
    assert [t["thread_id"] for t in body["threads"]] == ["t-a", "t-b"]
    assert [m["id"] for m in body["threads"][1]["messages"]] == ["b1", "b2", "b3"]
    assert set(body["in_window_ids"]) == {"a1", "a2", "a3", "a4", "b1", "b2", "b3"}
    assert tagger.get("/api/gold/windows/1/threads").status_code == 404


def test_window_threads_can_include_extra_threads(tagger: TestClient) -> None:
    body = tagger.get("/api/gold/windows/0/threads?include=t-c&include=t-zz&include=t-a").json()
    assert [t["thread_id"] for t in body["threads"]] == ["t-a", "t-b", "t-c"]
    assert "c1" not in body["in_window_ids"]
