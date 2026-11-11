"""The new thread splitter, its incremental mode and the step run (synthetic conversations).

Texts start with a topic word; the fake embedder gives every topic its own direction, so two
messages of one topic have cosine 1 and of different topics cosine 0.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from step_1_threads_split.message import message_record
from step_1_threads_split.reviewer.gold_windows import GoldProgress, GoldWindows
from step_1_threads_split.reviewer.history import EditHistory
from step_1_threads_split.reviewer.store import ThreadsStore
from step_1_threads_split.run import load_export_messages, run
from step_1_threads_split.splitter import SplitParams, ThreadSplitter
from step_2_classify.run import thread_state
from utils.paths import Artifact
from utils.threads_io import render_thread_for_llm, with_export

TOPICS = ("cats", "dogs", "fish", "birds")
PARAMS = SplitParams(quiet_minutes=30, resume_similarity=0.5, recent_messages=3)


def fake_embed(texts: Sequence[str]) -> np.ndarray:
    vectors = np.zeros((len(texts), len(TOPICS)), dtype=np.float32)
    for row, text in enumerate(texts):
        vectors[row, TOPICS.index(text.split()[0])] = 1.0
    return vectors


def entry(
    wa_id: str, minute: int, sender: str, text: str, quote: tuple[str, str] | None = None
) -> dict[str, Any]:
    """An export entry on 2025-03-01, ``minute`` minutes after 10:00."""
    hour, minute = divmod(600 + minute, 60)
    return {
        "id": wa_id,
        "date": f"{1 + hour // 24:02d}/03/2025",
        "time": f"{hour % 24:02d}:{minute:02d}",
        "sender": {"user_name": sender, "phone": f"+{len(sender)}"},
        "text": text,
        "quote": {"sender": {"user_name": quote[0], "phone": ""}, "text": quote[1]}
        if quote
        else None,
        "reactions": [],
        "media_type": None,
    }


def records(*entries: dict[str, Any]) -> list[dict[str, Any]]:
    return [r for r in map(message_record, entries) if r is not None]


def split(*entries: dict[str, Any]) -> list[list[str]]:
    threads = ThreadSplitter(PARAMS, fake_embed).split(records(*entries))
    return [t["message_ids"] for t in threads]


# ------------------------------------------------------------------- the rules
def test_a_short_pause_continues_and_a_long_pause_on_another_topic_splits() -> None:
    assert split(
        entry("a1", 0, "ann", "cats one"),
        entry("a2", 20, "bob", "dogs two"),  # 20 min: same conversation, whatever the topic
        entry("b1", 80, "cid", "fish three"),  # 60 min later, another topic: new thread
    ) == [["a1", "a2"], ["b1"]]


def test_a_long_pause_on_the_same_topic_resumes_the_thread() -> None:
    assert split(
        entry("a1", 0, "ann", "cats one"),
        entry("a2", 5, "bob", "dogs two"),
        entry("a3", 120, "cid", "dogs again"),  # similar to one of the last messages
    ) == [["a1", "a2", "a3"]]


def test_a_reply_joins_the_quoted_thread_after_any_pause() -> None:
    assert split(
        entry("a1", 0, "ann", "cats question"),
        entry("b1", 60, "bob", "fish news"),
        entry("a2", 900, "cid", "birds answer", quote=("ann", "cats  question")),
        entry("a3", 905, "ann", "birds thanks"),  # 5 min after the reply: same thread
    ) == [["a1", "a2", "a3"], ["b1"]]


def test_interleaved_conversations_are_kept_apart_by_their_replies() -> None:
    assert split(
        entry("a1", 0, "ann", "cats q"),
        entry("b1", 40, "bob", "fish q"),
        entry("a2", 41, "cid", "cats a", quote=("ann", "cats q")),
        entry("b2", 42, "dan", "fish a", quote=("bob", "fish q")),
        entry("a3", 43, "ann", "cats b", quote=("cid", "cats a")),
    ) == [["a1", "a2", "a3"], ["b1", "b2"]]


def test_the_latest_matching_message_is_the_quoted_one() -> None:
    assert split(
        entry("a1", 0, "ann", "cats same"),
        entry("x1", 40, "bob", "fish x"),
        entry("b1", 100, "ann", "cats same"),
        entry("c1", 160, "bob", "dogs x"),
        entry("b2", 161, "cid", "birds reply", quote=("ann", "cats same")),
    ) == [["a1"], ["x1"], ["b1", "b2"], ["c1"]]


def test_messages_without_content_are_left_out() -> None:
    assert records(entry("x", 0, "ann", "‎<image omitted>"), entry("y", 1, "ann", " ")) == []


# --------------------------------------------------------------- C1 and records
def test_threads_have_the_c1_shape_and_keep_quote_and_reactions() -> None:
    reacted = entry("a2", 1, "bob", "cats yes", quote=("ann", "cats q"))
    reacted["reactions"] = [{"emoji": "👍", "senders": [{"user_name": "eve", "phone": "+1"}]}]
    (thread,) = ThreadSplitter(PARAMS, fake_embed).split(
        records(entry("a1", 0, "ann", "cats q"), reacted)
    )
    assert thread["thread_id"] == "t-a1"
    assert thread["message_ids"] == ["a1", "a2"]
    assert (thread["start_time"], thread["last_time"]) == (
        "2025-03-01T10:00:00",
        "2025-03-01T10:01:00",
    )
    assert thread["participants"] == ["ann", "bob", "eve"]
    assert (thread["num_messages"], thread["num_unique_senders"], thread["last_sender"]) == (
        2, 3, "bob",
    )  # fmt: skip
    first, second = thread["messages"]
    assert first == {"id": "a1", "datetime": "2025-03-01T10:00:00", "sender": "ann",
                     "content": "cats q"}  # fmt: skip
    assert second["quote"] == {"sender": "ann", "text": "cats q"}
    assert second["reactions"] == [{"emoji": "👍", "senders": ["eve"]}]


# ------------------------------------------------------------------ incremental
def test_new_messages_join_old_threads_or_open_new_ones_and_ids_stay() -> None:
    splitter = ThreadSplitter(PARAMS, fake_embed)
    prior = splitter.split(records(
        entry("a1", 0, "ann", "cats q"), entry("a2", 2, "bob", "cats a"),
        entry("b1", 100, "cid", "fish q"),
    ))  # fmt: skip
    untouched = next(t for t in prior if t["thread_id"] == "t-b1")
    added = splitter.assign_new_messages(prior, records(
        entry("a3", 3000, "dan", "dogs late", quote=("ann", "cats q")),
        entry("c1", 3100, "eve", "birds new"),
        entry("a1", 0, "ann", "cats q"),  # already placed: skipped
    ))  # fmt: skip
    assert [(t["thread_id"], t["message_ids"]) for t in added] == [
        ("t-a1", ["a1", "a2", "a3"]),
        ("t-b1", ["b1"]),
        ("t-c1", ["c1"]),
    ]
    assert next(t for t in added if t["thread_id"] == "t-b1") is untouched
    assert [t["message_ids"] for t in prior] == [["a1", "a2"], ["b1"]]  # inputs unchanged


def test_a_full_run_and_a_run_in_two_parts_agree() -> None:
    chat = records(
        entry("a1", 0, "ann", "cats q"), entry("a2", 10, "bob", "cats a"),
        entry("b1", 90, "cid", "fish q"), entry("a3", 95, "dan", "dogs x", quote=("bob", "cats a")),
        entry("b2", 200, "eve", "fish more"), entry("c1", 400, "ann", "birds q"),
    )  # fmt: skip
    splitter = ThreadSplitter(PARAMS, fake_embed)
    full = splitter.split(chat)
    parts = splitter.assign_new_messages(splitter.split(chat[:3]), chat[3:])
    assert parts == full


# --------------------------------------------------------------------- the step
@pytest.fixture
def step(tmp_path: Path) -> dict[str, Any]:
    folder = tmp_path / "step_1_threads_split"
    folder.mkdir()
    export = tmp_path / "chat_android.json"
    chat = [
        entry("a1", 0, "ann", "cats q"),
        entry("a2", 5, "bob", "cats a", quote=("ann", "cats q")),
        entry("b1", 100, "cid", "fish q"),
        entry("b2", 104, "dan", "fish a"),
    ]
    export.write_text(json.dumps(chat), encoding="utf-8")
    artifact = Artifact(folder / "threads.json", folder / "threads_edited.json")
    return {"export": export, "artifact": artifact, "chat": chat}


def _run(step: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    return run(export_path=step["export"], artifact=step["artifact"], embed=fake_embed, **kwargs)


def _ids(path: Path) -> list[list[str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [t["message_ids"] for t in payload["threads"]]


def test_run_writes_c1_and_a_second_run_writes_nothing(step: dict[str, Any]) -> None:
    first = _run(step)
    artifact = step["artifact"]
    assert first["written"] and first["new"] == 4
    assert not artifact.edited.exists()
    payload = json.loads(artifact.original.read_text(encoding="utf-8"))
    meta = payload["metadata"]
    assert meta["format"] == "threads-c1" and meta["params"] == SplitParams.from_config().to_dict()
    assert set(meta["inputs"]) == {"a1", "a2", "b1", "b2"} and meta["git_commit"]
    stamp = artifact.original.stat().st_mtime_ns
    assert _run(step) == {"new": 0, "changed": 0, "removed": 0, "threads_touched": 0,
                          "written": False, "backup": None}  # fmt: skip
    assert artifact.original.stat().st_mtime_ns == stamp


def test_run_keeps_manual_edits_and_places_new_messages(step: dict[str, Any]) -> None:
    _run(step)
    artifact = step["artifact"]
    payload = json.loads(artifact.original.read_text(encoding="utf-8"))
    by_id = {t["thread_id"]: t for t in payload["threads"]}
    # The owner moves b2 into a thread of its own (a tagger split).
    moved = by_id["t-b1"]["messages"].pop()
    by_id["t-b1"]["message_ids"].pop()
    payload["threads"].append({**by_id["t-b1"], "thread_id": "t-b1-split-1",
                               "messages": [moved], "message_ids": ["b2"]})  # fmt: skip
    artifact.edited.write_text(json.dumps(payload), encoding="utf-8")

    chat = step["chat"]
    chat[1]["reactions"] = [{"emoji": "❤️", "senders": [{"user_name": "eve", "phone": ""}]}]
    chat.append(entry("c1", 300, "eve", "dogs news"))
    chat.append(entry("a3", 301, "ann", "cats late", quote=("bob", "cats a")))
    del chat[0]  # a1 left the export
    step["export"].write_text(json.dumps(chat), encoding="utf-8")

    result = _run(step)
    assert (result["new"], result["changed"], result["removed"]) == (2, 1, 1)
    assert result["backup"]
    edited = _ids(artifact.edited)
    assert edited == _ids(artifact.original)
    assert sorted(edited) == [["a2", "a3"], ["b1"], ["b2"], ["c1"]]
    threads = json.loads(artifact.edited.read_text(encoding="utf-8"))["threads"]
    first = next(t for t in threads if t["thread_id"] == "t-a1")  # keeps its id without a1
    assert first["messages"][0]["reactions"] == [{"emoji": "❤️", "senders": ["eve"]}]


def test_an_old_format_file_is_never_a_starting_point(step: dict[str, Any]) -> None:
    artifact = step["artifact"]
    old = {"threads": [{"thread_id": "thread-0001", "message_ids": [0], "messages": []}],
           "metadata": {"edited_by": "thread_tagger"}}  # fmt: skip
    artifact.edited.write_text(json.dumps(old), encoding="utf-8")
    with pytest.raises(ValueError, match="not a threads file of this step"):
        _run(step)
    result = _run(step, rebuild=True)
    assert result["backup"] and result["new"] == 4
    assert _ids(artifact.edited) == _ids(artifact.original) == [["a1", "a2"], ["b1", "b2"]]


# ------------------------------------------------------------------- readers
def test_steps_two_and_three_read_the_new_file(step: dict[str, Any]) -> None:
    _run(step)
    path = step["artifact"].original
    thread = json.loads(path.read_text(encoding="utf-8"))["threads"][0]
    assert thread_state(thread).count("\n") == 1
    rendered, refs = render_thread_for_llm(with_export(thread, None))
    assert refs == ["a1", "a2"] and "↩[1]" in rendered


def test_an_edit_of_the_conversations_tool_survives_the_next_run(step: dict[str, Any]) -> None:
    _run(step)
    artifact = step["artifact"]
    store = ThreadsStore(artifact.original, artifact.edited, step["export"],
                         GoldWindows([], GoldProgress(artifact.original.with_name("progress.json"))),
                         EditHistory(1))  # fmt: skip
    assert store.views.rows()["total"] == 2
    store.apply(store.editor().move(["b2"], None))
    step["export"].write_text(json.dumps([*step["chat"], entry("c1", 106, "eve", "fish b")]), encoding="utf-8")
    result = _run(step)
    assert result["new"] == 1
    assert _ids(artifact.edited) == [["a1", "a2"], ["b1"], ["b2", "c1"]]


def test_load_export_messages_keeps_time_order(step: dict[str, Any]) -> None:
    assert [m["id"] for m in load_export_messages(step["export"])] == ["a1", "a2", "b1", "b2"]
