from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from utils.file_lock import file_lock


def test_lock_serialises_critical_section(tmp_path: Path) -> None:
    target = tmp_path / "x.json"
    state = {"inside": 0, "max_inside": 0, "done": 0}

    def worker() -> None:
        with file_lock(target):
            state["inside"] += 1
            state["max_inside"] = max(state["max_inside"], state["inside"])
            time.sleep(0.1)
            state["inside"] -= 1
            state["done"] += 1

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert state["max_inside"] == 1
    assert state["done"] == 2


def test_second_acquire_times_out(tmp_path: Path) -> None:
    target = tmp_path / "x.json"
    held = threading.Event()
    release = threading.Event()

    def holder() -> None:
        with file_lock(target):
            held.set()
            release.wait(timeout=5)

    thread = threading.Thread(target=holder)
    thread.start()
    try:
        assert held.wait(timeout=5)
        with pytest.raises(TimeoutError, match="could not lock"):
            with file_lock(target, timeout=0.2):
                pass
    finally:
        release.set()
        thread.join()

    with file_lock(target, timeout=1.0):
        pass


def test_sidecar_lock_file_next_to_target(tmp_path: Path) -> None:
    target = tmp_path / "sub" / "x.json"

    with file_lock(target):
        assert (tmp_path / "sub" / "x.json.lock").exists()

    assert (tmp_path / "sub" / "x.json.lock").exists()
