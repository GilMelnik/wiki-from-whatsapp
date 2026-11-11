"""Gold-window mode of the conversations tool and ``evaluate --freeze-gold --done-only`` (synthetic)."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

from step_1_threads_split import evaluate as evaluate_module
from step_1_threads_split.gold import Window, load_gold
from step_1_threads_split.reviewer.gold_windows import GoldProgress
from tests.threads_tool_support import LATER_WINDOW, WINDOW, ThreadsSetup, c1_payload
from utils import logging_setup


@pytest.fixture
def tool(tmp_path: Path) -> ThreadsSetup:
    return ThreadsSetup(tmp_path)


def test_windows_are_in_time_order_with_live_membership(tool: ThreadsSetup) -> None:
    windows = tool.get("/summary")["windows"]
    assert [(w["index"], w["start"], w["message_count"], w["done"]) for w in windows] == [
        (0, WINDOW["start"], 4, False), (1, LATER_WINDOW["start"], 1, False)]
    # A conversation split off inside the window shows up in it at once.
    tool.post("/move", {"message_ids": ["a2"]})
    assert tool.get("/summary")["windows"][0]["thread_ids"] == ["t-a1", "t-a1-split-1", "t-b1"]


def test_done_marks_persist_by_window_span(tool: ThreadsSetup) -> None:
    windows = tool.post("/windows/1/done", {"done": True})
    assert [w["done"] for w in windows] == [False, True]
    entries = json.loads(tool.progress.read_text(encoding="utf-8"))["windows"]
    assert [(e["start"], e["end"], e["done"]) for e in entries] == [(LATER_WINDOW["start"], LATER_WINDOW["end"], True)]
    # A new server reads the same marks; undone again on request.
    tool.client = tool.new_client()
    assert [w["done"] for w in tool.get("/summary")["windows"]] == [False, True]
    assert [w["done"] for w in tool.post("/windows/1/done", {"done": False})] == [False, False]
    tool.post("/windows/5/done", {"done": True}, 404)


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
    monkeypatch.setitem(evaluate_module.THREADS_CONFIG, "gold_windows", [WINDOW, LATER_WINDOW])
    (data / "threads_edited.json").write_text(json.dumps(c1_payload()), encoding="utf-8")
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

    progress.set_done(Window.from_dict(LATER_WINDOW), True)
    evaluate_module.main(["--freeze-gold", "--done-only", "--gold", str(gold_path)])
    assert _frozen_starts(gold_path) == [LATER_WINDOW["start"]]

    progress.set_done(Window.from_dict(WINDOW), True)
    evaluate_module.main(["--freeze-gold", "--add", "--done-only", "--gold", str(gold_path)])
    assert _frozen_starts(gold_path) == [WINDOW["start"], LATER_WINDOW["start"]]


def test_without_done_only_every_configured_window_is_frozen(freeze_cli: Path) -> None:
    gold_path = freeze_cli / "gold.json"
    evaluate_module.main(["--freeze-gold", "--gold", str(gold_path)])
    assert _frozen_starts(gold_path) == [WINDOW["start"], LATER_WINDOW["start"]]
    # The frozen labels are the tool's split: one cluster per thread inside each window.
    assert len(set(load_gold(gold_path)["messages"].values())) == 3


def test_done_only_needs_freeze_gold(freeze_cli: Path) -> None:
    with pytest.raises(SystemExit):
        evaluate_module.main(["--done-only"])
