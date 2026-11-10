from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from utils.json_io import write_json_file


def test_round_trip_creates_parents_and_leaves_no_temp(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "dir" / "x.json"
    payload = {"a": "שלום", "n": [1, 2]}

    write_json_file(payload, target)

    text = target.read_text(encoding="utf-8")
    assert "שלום" in text
    assert text == json.dumps(payload, ensure_ascii=False, indent=2)
    assert json.loads(text) == payload
    assert [p.name for p in target.parent.iterdir()] == ["x.json"]


def test_failed_overwrite_keeps_previous_content(tmp_path: Path) -> None:
    target = tmp_path / "x.json"
    write_json_file({"old": 1}, target)

    with pytest.raises(TypeError):
        write_json_file({"bad": object()}, target)

    assert json.loads(target.read_text(encoding="utf-8")) == {"old": 1}
    assert [p.name for p in tmp_path.iterdir()] == ["x.json"]


def test_concurrent_threads_writing_same_path_do_not_collide(tmp_path: Path) -> None:
    target = tmp_path / "x.json"
    thread_count = 8
    writes_per_thread = 25
    barrier = threading.Barrier(thread_count)
    errors: list[BaseException] = []

    def worker(worker_id: int) -> None:
        try:
            barrier.wait()
            for i in range(writes_per_thread):
                write_json_file({"worker": worker_id, "i": i}, target)
        except BaseException as exc:  # surfaced in the main thread below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(thread_count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    final = json.loads(target.read_text(encoding="utf-8"))
    assert final["i"] == writes_per_thread - 1
    assert [p.name for p in tmp_path.iterdir()] == ["x.json"]
