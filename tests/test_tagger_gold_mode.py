"""Gold-window mode of the thread tagger and ``evaluate --freeze-gold --done-only`` (synthetic)."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from step_1_threads_split import evaluate as evaluate_module
from step_1_threads_split.gold import Window, load_gold
from step_1_threads_split.review import gold_windows as gold_module
from step_1_threads_split.review.gold_windows import GoldProgress
from step_1_threads_split.review.operations import recompute_thread_stats
from step_1_threads_split.review.server import app, configure_store
from utils import logging_setup

W1 = {"start": "2025-01-01T10:00", "end": "2025-01-01T12:00"}
W2 = {"start": "2025-01-02T10:00", "end": "2025-01-02T12:00"}
W0 = {"start": "2024-12-31T10:00", "end": "2024-12-31T11:00"}

# Positional ids are deliberately not the WhatsApp ids: the gold mode must key on ``id``.
THREADS = {
    "t-a": [
        ("a0", "2025-01-01T09:50:00"),  # before W1
        ("a1", "2025-01-01T10:00:00"),  # exactly at W1's start: inside
        ("a2", "2025-01-01T10:30:00"),
        ("a3", "2025-01-01T12:00:00"),  # exactly at W1's end: outside
    ],
    "t-b": [("b1", "2025-01-01T10:05:00"), ("b2", "2025-01-01T11:59:00")],
    "t-c": [("c1", "2025-01-02T10:10:00"), ("c2", "2025-01-02T13:00:00")],
    "t-d": [("d1", "2025-01-03T10:00:00")],
}


def _threads_payload() -> dict[str, Any]:
    threads = []
    for thread_id, rows in THREADS.items():
        messages = [
            {"id": wa_id, "datetime": moment, "sender": "s", "content": "c"}
            for wa_id, moment in rows
        ]
        thread = {
            "thread_id": thread_id,
            "messages": messages,
            "message_ids": [100 + i for i in range(len(messages))],
        }
        threads.append(recompute_thread_stats(thread))
    return {"threads": threads, "metadata": {}}


@pytest.fixture
def tagger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """A tagger in inspect mode on synthetic threads; windows given in non-chronological order."""
    threads_path = tmp_path / "threads.json"
    threads_path.write_text(json.dumps(_threads_payload()), encoding="utf-8")
    monkeypatch.setitem(gold_module.THREADS_CONFIG, "gold_windows", [W2, W1])
    monkeypatch.setattr(gold_module, "GOLD_PROGRESS_PATH", tmp_path / "gold_progress.json")
    configure_store(inspect_only=True, threads_path=threads_path)
    yield TestClient(app)
    configure_store(inspect_only=False, threads_path=None)


def _windows(client: TestClient) -> list[dict[str, Any]]:
    response = client.get("/api/gold/windows")
    assert response.status_code == 200
    return response.json()


def test_windows_list_counts_thread_ids_and_half_open_bounds(tagger: TestClient) -> None:
    first, second = _windows(tagger)
    assert first == {
        "index": 0,
        **W1,
        "message_count": 4,  # a1, a2, b1, b2 — not a0 (before) nor a3 (at the end)
        "thread_ids": ["t-a", "t-b"],
        "done": False,
    }
    assert second == {"index": 1, **W2, "message_count": 1, "thread_ids": ["t-c"], "done": False}


def test_thread_list_filtered_by_window_has_no_cap_and_in_window_counts(
    tagger: TestClient,
) -> None:
    payload = tagger.get("/api/threads?filter=all&limit=1&window=0").json()
    assert [item["thread_id"] for item in payload["items"]] == ["t-a", "t-b"]
    assert [item["in_window"] for item in payload["items"]] == [2, 2]
    assert [item["num_messages"] for item in payload["items"]] == [4, 2]
    assert payload["total"] == 2
    assert tagger.get("/api/threads?window=2").status_code == 404


def test_thread_detail_marks_in_window_messages_and_queues_the_window(
    tagger: TestClient,
) -> None:
    payload = tagger.get("/api/threads/t-a?filter=all&window=0").json()
    gold = payload["gold"]
    assert (gold["index"], gold["start"], gold["end"]) == (0, W1["start"], W1["end"])
    in_window = set(gold["in_window_ids"])
    assert {"a1", "a2"} <= in_window
    assert not {"a0", "a3"} & in_window
    assert payload["queue"] == {"prev_in_queue": None, "next_in_queue": "t-b"}
    assert tagger.get("/api/threads/t-b?window=0").json()["queue"] == {
        "prev_in_queue": "t-a",
        "next_in_queue": None,
    }


def test_split_off_thread_appears_in_its_window(tagger: TestClient) -> None:
    # t-a holds a0, a1, a2, a3 in that order: split off a2 (position 2).
    response = tagger.post(
        "/api/threads/split",
        json={"source_id": "t-a", "mode": "sparse", "message_indices": [2]},
    )
    assert response.status_code == 200
    new_id = response.json()["new_thread_id"]
    first = _windows(tagger)[0]
    assert first["thread_ids"] == ["t-a", "t-b", new_id]
    assert first["message_count"] == 4
    items = tagger.get("/api/threads?window=0").json()["items"]
    assert [(item["thread_id"], item["in_window"]) for item in items] == [
        ("t-a", 1),
        ("t-b", 2),
        (new_id, 1),
    ]


def test_done_flag_persists_and_follows_the_window_when_the_config_changes(
    tagger: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    response = tagger.post("/api/gold/windows/1/done", json={"done": True})
    assert response.status_code == 200
    assert response.json()["done"] is True
    assert tagger.post("/api/gold/windows/5/done", json={"done": True}).status_code == 404

    progress = json.loads((tmp_path / "gold_progress.json").read_text(encoding="utf-8"))
    assert [(e["start"], e["end"], e["done"]) for e in progress["windows"]] == [
        (W2["start"], W2["end"], True)
    ]

    # Reload the store with an extra, earlier window: W2 moves from index 1 to index 2.
    monkeypatch.setitem(gold_module.THREADS_CONFIG, "gold_windows", [W1, W2, W0])
    configure_store(inspect_only=True, threads_path=tmp_path / "threads.json")
    done = {(w["start"], w["done"]) for w in _windows(tagger)}
    assert done == {(W0["start"], False), (W1["start"], False), (W2["start"], True)}

    tagger.post("/api/gold/windows/2/done", json={"done": False})
    assert GoldProgress(tmp_path / "gold_progress.json").done_windows() == set()


def test_empty_gold_windows_keep_the_old_behaviour(
    tagger: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(gold_module.THREADS_CONFIG, "gold_windows", [])
    configure_store(inspect_only=True, threads_path=tmp_path / "threads.json")
    assert _windows(tagger) == []
    listing = tagger.get("/api/threads?filter=all&sort=start_time&order=asc&limit=2").json()
    assert [item["thread_id"] for item in listing["items"]] == ["t-a", "t-b"]
    assert listing["total"] == 4
    assert all("in_window" not in item for item in listing["items"])
    detail = tagger.get("/api/threads/t-a?filter=all").json()
    assert "gold" not in detail
    assert tagger.get("/api/threads?window=0").status_code == 404
    assert not (tmp_path / "gold_progress.json").exists()


def test_broken_progress_file_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "gold_progress.json"
    path.write_text('{"done": []}', encoding="utf-8")
    with pytest.raises(ValueError, match="not a gold progress file"):
        GoldProgress(path).done_windows()


# ------------------------------------------------------------ evaluate --done-only
@pytest.fixture
def freeze_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point the evaluate CLI (files, progress file, step log) at ``tmp_path``."""
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(logging_setup, "DATA_DIR", data)
    monkeypatch.setattr(logging_setup, "_current_step", None)
    monkeypatch.setattr(logging_setup, "_file_handler", None)
    monkeypatch.setattr(evaluate_module, "ORIGINAL_THREADS_PATH", data / "threads.json")
    monkeypatch.setattr(evaluate_module, "EDITED_THREADS_PATH", data / "threads_edited.json")
    monkeypatch.setattr(evaluate_module, "BACKUPS_DIR", data / "backups")
    monkeypatch.setattr(evaluate_module, "GOLD_PROGRESS_PATH", data / "gold_progress.json")
    monkeypatch.setitem(evaluate_module.THREADS_CONFIG, "gold_windows", [W1, W2])
    (data / "threads_edited.json").write_text(json.dumps(_threads_payload()), encoding="utf-8")
    yield data
    handler = logging_setup._file_handler
    if handler is not None:
        logging.getLogger().removeHandler(handler)
        handler.close()


def _frozen_starts(gold_path: Path) -> list[str]:
    return [window["start"] for window in load_gold(gold_path)["windows"]]


def test_done_only_freezes_only_done_windows_and_combines_with_add(freeze_cli: Path) -> None:
    gold_path = freeze_cli / "gold.json"
    progress = GoldProgress(freeze_cli / "gold_progress.json")
    with pytest.raises(ValueError, match="none of the 2 configured gold windows"):
        evaluate_module.main(["--freeze-gold", "--done-only", "--gold", str(gold_path)])

    progress.set_done(Window.from_dict(W2), True)
    evaluate_module.main(["--freeze-gold", "--done-only", "--gold", str(gold_path)])
    assert _frozen_starts(gold_path) == [W2["start"]]

    progress.set_done(Window.from_dict(W1), True)
    evaluate_module.main(["--freeze-gold", "--add", "--done-only", "--gold", str(gold_path)])
    assert _frozen_starts(gold_path) == [W1["start"], W2["start"]]


def test_without_done_only_every_configured_window_is_frozen(freeze_cli: Path) -> None:
    gold_path = freeze_cli / "gold.json"
    evaluate_module.main(["--freeze-gold", "--gold", str(gold_path)])
    assert _frozen_starts(gold_path) == [W1["start"], W2["start"]]


def test_done_only_needs_freeze_gold(freeze_cli: Path) -> None:
    with pytest.raises(SystemExit):
        evaluate_module.main(["--done-only"])
