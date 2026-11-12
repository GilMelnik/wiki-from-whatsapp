"""The conversations tool (step 1's review): views, edits, undo, file rules (synthetic C1)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from review_tool.tools import TOOL_SPECS
from tests.review_tool_support import locked_elsewhere
from tests.threads_tool_support import ThreadsSetup, c1_payload
from utils.json_io import write_json_file
from utils.paths import BACKUPS_DIR


@pytest.fixture
def tool(tmp_path: Path) -> ThreadsSetup:
    return ThreadsSetup(tmp_path)


def test_the_tool_is_registered() -> None:
    spec = next(spec for spec in TOOL_SPECS if spec.name == "threads")
    assert spec.title == "Conversations"
    assert spec.reads == ("threads", "export") and spec.writes == ("threads", "classified")


def test_summary_of_a_c1_file(tool: ThreadsSetup) -> None:
    summary = tool.get("/summary")
    assert summary["state"] == "ready" and summary["problem"] is None
    assert summary["file"] == "threads.json" and not summary["edited"]
    assert (summary["thread_count"], summary["message_count"]) == (3, 6)
    assert summary["quiet_minutes"] == 25 and summary["undo"] is None
    assert [w["thread_ids"] for w in summary["windows"]] == [["t-a1", "t-b1"], ["t-c1"]]


def test_rows_in_time_order_by_size_and_searched(tool: ThreadsSetup) -> None:
    rows = tool.get("/threads")
    assert [row["thread_id"] for row in rows["items"]] == ["t-a1", "t-b1", "t-c1"]
    assert rows["items"][0] | {"opening": "Cats are great", "size": 3, "people": 2} == rows["items"][0]
    assert [r["thread_id"] for r in tool.get("/threads", sort="size")["items"]] == ["t-a1", "t-b1", "t-c1"]
    found = tool.get("/threads", q="CATS")
    assert [(r["thread_id"], r["matches"]) for r in found["items"]] == [("t-a1", 3)]
    assert [r["thread_id"] for r in tool.get("/threads", q="carol")["items"]] == ["t-b1"]
    assert tool.get("/threads", q="nothing like this")["items"] == []
    tool.get("/threads", status=422, sort="random")


def test_thread_view_shows_the_conversations_right_around_it(tool: ThreadsSetup) -> None:
    view = tool.get("/view", thread="t-b1")
    assert [(b["thread_id"], b["role"]) for b in view["blocks"]] == [("t-a1", "around"), ("t-b1", "current")]
    assert view["chat_size"] == 6 and view["in_window_ids"] is None
    current = view["blocks"][1]["messages"]
    assert [(m["id"], m["seq"], m["position"]) for m in current] == [("b1", 2, 0), ("b2", 3, 1)]
    assert current[1]["reply_to"] | {"id": "b1", "thread_id": "t-b1"} == current[1]["reply_to"]
    view = tool.get("/view", thread="t-c1", window=0)
    assert [(b["thread_id"], b["role"]) for b in view["blocks"]] == [("t-a1", "previous"), ("t-c1", "current")]
    assert view["window"]["start"] == "2025-01-01T10:00" and view["in_window_ids"] == ["a1", "a2"]
    tool.get("/view", status=404, thread="t-zz")
    tool.get("/view", status=422)


def test_window_view_with_context_and_included_threads(tool: ThreadsSetup) -> None:
    view = tool.get("/view", window=0)
    assert view["subject"] == {"kind": "window", "index": 0, "start": "2025-01-01T10:00", "end": "2025-01-01T10:30"}
    assert [(b["thread_id"], b["role"], b["partial"]) for b in view["blocks"]] == [
        ("t-a1", "window", False), ("t-b1", "window", False)]
    assert view["in_window_ids"] == ["a1", "a2", "b1", "b2"]
    view = tool.get("/view", window=0, context=["4-5"], include=["t-b1", "t-zz"])
    assert [(b["thread_id"], b["role"], len(b["messages"])) for b in view["blocks"]] == [
        ("t-a1", "window", 3), ("t-b1", "window", 2), ("t-c1", "context", 1)]
    assert view["blocks"][2]["partial"] and view["blocks"][2]["size"] == 1
    tool.get("/view", status=422, window=0, context=["5-4"])
    tool.get("/view", status=404, window=7)


def test_move_into_another_thread_keeps_metadata_and_the_original(tool: ThreadsSetup) -> None:
    original = tool.original.read_text(encoding="utf-8")
    answer = tool.post("/move", {"message_ids": ["b2", "b1"], "target_id": "t-a1"})
    assert answer["label"] == "Moved 2 messages to t-a1; t-b1 became empty and was removed"
    assert answer["focus"] == "t-a1" and answer["removed"] == ["t-b1"] and answer["summary"]["edited"]
    assert tool.saved_split() == {"t-a1": ["a1", "a2", "b1", "b2", "a3"], "t-c1": ["c1"]}
    metadata = tool.saved()["metadata"]
    assert metadata["format"] == "threads-c1" and metadata["inputs"] == {"a1": "f1"}
    assert metadata["edited_by"] == "threads_reviewer" and metadata["thread_count"] == 2
    thread = tool.saved()["threads"][0]
    assert thread["message_ids"] == ["a1", "a2", "b1", "b2", "a3"] and thread["num_unique_senders"] == 4
    assert tool.original.read_text(encoding="utf-8") == original


def test_new_conversation_from_messages_uses_split_ids(tool: ThreadsSetup) -> None:
    # Messages of two threads: the new one is split off the thread of the earliest.
    answer = tool.post("/move", {"message_ids": ["b2", "a2"], "target_id": None})
    assert answer["created"] == "t-a1-split-1" and answer["label"] == "New conversation t-a1-split-1 from 2 messages"
    assert tool.post("/move", {"message_ids": ["a3"]})["created"] == "t-a1-split-2"
    assert tool.saved_split() == {
        "t-a1": ["a1"], "t-a1-split-1": ["a2", "b2"], "t-b1": ["b1"], "t-a1-split-2": ["a3"], "t-c1": ["c1"]}
    view = tool.get("/view", thread="t-a1-split-2")
    assert [(b["thread_id"], b["role"]) for b in view["blocks"]] == [
        ("t-a1", "part"), ("t-a1-split-1", "part"), ("t-a1-split-2", "current"), ("t-c1", "next")]


def test_refused_edits_change_nothing(tool: ThreadsSetup) -> None:
    assert "already all in t-a1" in tool.post("/move", {"message_ids": ["a1"], "target_id": "t-a1"}, 409)["detail"]
    assert "one whole conversation" in tool.post("/move", {"message_ids": ["b1", "b2"]}, 409)["detail"]
    assert "zz" in tool.post("/move", {"message_ids": ["zz"], "target_id": "t-a1"}, 404)["detail"]
    tool.post("/move", {"message_ids": ["a1"], "target_id": "t-zz"}, 404)
    tool.post("/move", {"message_ids": []}, 422)
    tool.post("/merge", {"thread_ids": ["t-a1"]}, 422)
    tool.post("/merge", {"thread_ids": ["t-a1", "t-a1"]}, 409)
    tool.post("/merge", {"thread_ids": ["t-a1", "t-zz"]}, 404)
    assert not tool.edited.exists()


def test_merge_keeps_the_earliest_id(tool: ThreadsSetup) -> None:
    answer = tool.post("/merge", {"thread_ids": ["t-c1", "t-b1"]})
    assert answer["focus"] == "t-b1" and answer["label"] == "Merged t-c1 into t-b1"
    assert tool.saved_split() == {"t-a1": ["a1", "a2", "a3"], "t-b1": ["b1", "b2", "c1"]}


def test_undo_takes_back_each_edit_then_refuses(tool: ThreadsSetup) -> None:
    tool.post("/move", {"message_ids": ["a3"]})
    tool.post("/merge", {"thread_ids": ["t-b1", "t-c1"]})
    assert tool.get("/summary")["undo"]["label"] == "Merged t-c1 into t-b1"
    assert tool.post("/undo")["focus"] == "t-b1"
    assert tool.saved_split() == {"t-a1": ["a1", "a2"], "t-b1": ["b1", "b2"], "t-a1-split-1": ["a3"], "t-c1": ["c1"]}
    tool.post("/undo")
    assert tool.saved()["threads"] == c1_payload()["threads"]
    assert "nothing to undo" in tool.post("/undo", status=409)["detail"]


def test_an_outside_write_voids_undo_and_is_picked_up(tool: ThreadsSetup) -> None:
    tool.post("/move", {"message_ids": ["a3"]})
    write_json_file(c1_payload({"t-a1": ["a1", "a2", "a3", "b1", "b2", "c1"]}), tool.edited)
    assert tool.get("/summary")["undo"] is None
    assert tool.get("/summary")["thread_count"] == 1
    tool.post("/undo", status=409)


def test_the_edited_file_is_backed_up_before_the_tool_first_overwrites_it(tool: ThreadsSetup) -> None:
    write_json_file(c1_payload(), tool.edited)
    before = set(BACKUPS_DIR.glob("threads_edited_*.json")) if BACKUPS_DIR.is_dir() else set()
    tool.post("/move", {"message_ids": ["a3"]})
    tool.post("/move", {"message_ids": ["a2"]})
    made = set(BACKUPS_DIR.glob("threads_edited_*.json")) - before
    assert len(made) == 1
    assert json.loads(made.pop().read_text(encoding="utf-8"))["threads"] == c1_payload()["threads"]


@pytest.mark.parametrize("which", ["original", "edited"])
def test_an_old_format_file_is_explained_never_edited(tmp_path: Path, which: str) -> None:
    old = {"threads": [{"thread_id": "thread-0001", "messages": [], "message_ids": [0]}]}
    tool = ThreadsSetup(tmp_path, payload=old if which == "original" else None)
    if which == "edited":
        write_json_file(old, tool.edited)
    before = tool.edited.read_bytes() if tool.edited.exists() else None
    summary = tool.get("/summary")
    assert summary["state"] == "old_format" and "old splitter" in summary["problem"]
    assert summary["windows"] == [] and summary["thread_count"] == 0
    assert "run step 1" in tool.get("/threads", status=409)["detail"]
    tool.get("/view", status=409, window=0)
    tool.post("/move", {"message_ids": ["a1"], "target_id": "t-a1"}, 409)
    tool.post("/undo", status=409)
    assert (tool.edited.read_bytes() if tool.edited.exists() else None) == before


def test_edits_are_refused_while_the_file_is_locked_or_step_1_runs(tool: ThreadsSetup) -> None:
    with locked_elsewhere(tool.edited):
        response = tool.client.post("/api/threads/move", json={"message_ids": ["a3"]})
        assert response.status_code == 423 and "being written by another process" in response.json()["detail"]
        tool.get("/view", window=0)
    busy = tool.new_client(busy="threads")
    response = busy.post("/api/threads/move", json={"message_ids": ["a3"]})
    assert response.status_code == 423 and "Step 1" in response.json()["detail"]
    assert busy.get("/api/threads/view", params={"thread": "t-a1"}).status_code == 200
    assert not tool.edited.exists()
