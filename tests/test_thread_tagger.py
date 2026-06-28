"""Tests for thread_tagger package."""

from __future__ import annotations

import json
from pathlib import Path

from step_1_threads_split.review import duration_sec
from step_1_threads_split.review import (
    extract_messages_to_new_thread,
    indices_for_split_mode,
    merge_threads,
    move_messages,
    patch_classification,
    recompute_thread_stats,
    split_by_mode,
    split_thread,
)
from step_1_threads_split.review.store import ThreadStore
from utils.paths import (
    init_edited_files,
    ORIGINAL_CLASSIFIED_PATH,
    ORIGINAL_THREADS_PATH,
    EDITED_CLASSIFIED_PATH,
    EDITED_THREADS_PATH,
    resolve_classified_path,
    resolve_threads_path,
)
from step_1_threads_split.review import compute_stats, enrich_thread, filter_threads, sort_threads


def _msg(i: int, dt: str, sender: str = "a", content: str = "hi") -> dict:
    return {
        "id": str(i),
        "datetime": dt,
        "sender": sender,
        "content": content,
    }


def _thread(tid: str, messages: list[dict]) -> dict:
    t = {
        "thread_id": tid,
        "messages": messages,
        "message_ids": list(range(len(messages))),
    }
    return recompute_thread_stats(t)


def _class(tid: str, knowledge: bool = False) -> dict:
    return {
        "thread_id": tid,
        "is_knowledge_bearing": knowledge,
        "topic_tags": [],
        "emergent_tags": [],
        "entities": [],
        "reason": "test",
        "passed_heuristic": True,
        "num_messages": 0,
        "num_unique_senders": 0,
        "start_time": "",
        "last_time": "",
    }


class TestPaths:
    def test_resolve_original_when_no_edited(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert resolve_threads_path() == ORIGINAL_THREADS_PATH
        assert resolve_classified_path() == ORIGINAL_CLASSIFIED_PATH

    def test_resolve_edited_when_present(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "data").mkdir()
        (tmp_path / "data" / "threads_edited.json").write_text("{}")
        assert resolve_threads_path() == EDITED_THREADS_PATH


class TestInitEdited:
    def test_init_creates_threads_only_without_classified(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        data = tmp_path / "data"
        data.mkdir()
        (data / "threads.json").write_text('{"threads": []}')

        created = init_edited_files(require_classified=False)
        assert created["threads"] == EDITED_THREADS_PATH
        assert "classified" not in created

    def test_init_creates_missing_files(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        data = tmp_path / "data"
        data.mkdir()
        (data / "threads.json").write_text('{"threads": []}')
        (data / "threads_classified.json").write_text('{"threads": []}')

        created = init_edited_files()
        assert created["threads"] == EDITED_THREADS_PATH
        assert created["classified"] == EDITED_CLASSIFIED_PATH
        assert EDITED_THREADS_PATH.is_file()
        assert EDITED_CLASSIFIED_PATH.is_file()

    def test_init_skips_existing(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        data = tmp_path / "data"
        data.mkdir()
        (data / "threads.json").write_text('{"threads": []}')
        (data / "threads_classified.json").write_text('{"threads": []}')
        (data / "threads_edited.json").write_text('{"threads": ["edited"]}')
        (data / "threads_classified_edited.json").write_text('{"threads": []}')

        created = init_edited_files()
        assert created == {}
        assert (data / "threads_edited.json").read_text() == '{"threads": ["edited"]}'


class TestInspectMode:
    def test_load_threads_without_classification(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        data = tmp_path / "data"
        data.mkdir()
        (data / "threads.json").write_text(
            '{"threads": [{"thread_id": "t1", "start_time": "2022-01-01T10:00:00", '
            '"last_time": "2022-01-01T11:00:00", "num_messages": 1, '
            '"num_unique_senders": 1, "messages": []}]}'
        )
        store = ThreadStore(inspect_only=True)
        store.load()
        assert store.has_classification is False
        assert store.inspect_only is True
        assert len(store.threads) == 1
        enriched = store._enrich(store.threads[0])
        assert enriched["has_classification"] is False

    def test_message_context_in_inspect_mode(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        data = tmp_path / "data"
        data.mkdir()
        (data / "threads.json").write_text(
            json.dumps(
                {
                    "threads": [
                        _thread("before", [_msg(0, "2022-01-01T09:00:00")]),
                        _thread(
                            "main",
                            [_msg(1, "2022-01-01T10:00:00"), _msg(2, "2022-01-01T10:05:00")],
                        ),
                        _thread(
                            "main-split-1",
                            [_msg(3, "2022-01-01T10:10:00")],
                        ),
                        _thread("after", [_msg(4, "2022-01-01T11:00:00")]),
                    ]
                }
            )
        )
        store = ThreadStore(inspect_only=True)
        store.load()

        ctx = store.message_context("main-split-1")
        assert ctx["prev"] is None
        assert ctx["next"]["thread_id"] == "after"
        assert [t["thread_id"] for t in ctx["family"]] == ["main", "main-split-1"]

        ctx_main = store.message_context("main")
        assert ctx_main["prev"]["thread_id"] == "before"
        assert ctx_main["next"] is None
        assert [t["thread_id"] for t in ctx_main["family"]] == ["main", "main-split-1"]

        from fastapi.testclient import TestClient

        from step_1_threads_split.review import app, configure_store

        configure_store(inspect_only=True)
        client = TestClient(app)
        response = client.get("/api/threads/main-split-1?filter=all")
        assert response.status_code == 200
        payload = response.json()
        assert payload["context"]["family"][0]["thread_id"] == "main"
        assert payload["context"]["prev"] is None


class TestOperations:
    def test_recompute_stats(self):
        messages = [
            _msg(0, "2022-01-01T10:00:00", "a"),
            _msg(1, "2022-01-01T11:00:00", "b"),
        ]
        t = _thread("thread-0001", messages)
        assert t["num_messages"] == 2
        assert t["num_unique_senders"] == 2
        assert t["start_time"] == "2022-01-01T10:00:00"
        assert t["last_time"] == "2022-01-01T11:00:00"

    def test_merge_threads(self):
        t1 = _thread("thread-0001", [_msg(0, "2022-01-01T10:00:00")])
        t2 = _thread("thread-0002", [_msg(1, "2022-01-01T09:00:00")])
        classifications = {
            "thread-0001": _class("thread-0001"),
            "thread-0002": _class("thread-0002", True),
        }
        threads, cls, survivor = merge_threads(
            [t1, t2], classifications, ["thread-0001", "thread-0002"]
        )
        assert survivor == "thread-0001"
        assert len(threads) == 1
        assert threads[0]["num_messages"] == 2
        assert threads[0]["messages"][0]["datetime"] == "2022-01-01T09:00:00"

    def test_split_thread(self):
        messages = [_msg(i, f"2022-01-01T{10+i}:00:00") for i in range(4)]
        t = _thread("thread-0001", messages)
        classifications = {"thread-0001": _class("thread-0001")}
        threads, cls, new_ids = split_thread(
            [t],
            classifications,
            "thread-0001",
            [
                {"start_index": 0, "end_index": 1},
                {"start_index": 2, "end_index": 3},
            ],
        )
        assert len(new_ids) == 2
        assert len(threads) == 2
        assert threads[0]["num_messages"] == 2
        assert threads[1]["num_messages"] == 2

    def test_extract_sparse_non_contiguous(self):
        messages = [_msg(i, f"2022-01-01T{10+i}:00:00") for i in range(5)]
        t = _thread("thread-0001", messages)
        classifications = {"thread-0001": _class("thread-0001")}
        threads, cls, new_id, remainder = extract_messages_to_new_thread(
            [t], classifications, "thread-0001", [1, 3]
        )
        assert new_id == "thread-0001-split-1"
        assert remainder == "thread-0001"
        by_id = {x["thread_id"]: x for x in threads}
        assert by_id[new_id]["num_messages"] == 2
        assert by_id["thread-0001"]["num_messages"] == 3

    def test_split_by_mode_range(self):
        messages = [_msg(i, f"2022-01-01T{10+i}:00:00") for i in range(6)]
        t = _thread("thread-0001", messages)
        classifications = {"thread-0001": _class("thread-0001")}
        indices = indices_for_split_mode("range", [1, 4])
        assert indices == {1, 2, 3, 4}
        _, _, new_id, _ = split_by_mode(
            [t], classifications, "thread-0001", "range", [1, 4]
        )
        assert new_id == "thread-0001-split-1"

    def test_move_messages_append(self):
        src = _thread("thread-0001", [_msg(0, "2022-01-01T10:00:00")])
        tgt = _thread("thread-0002", [_msg(1, "2022-01-01T12:00:00")])
        classifications = {
            "thread-0001": _class("thread-0001"),
            "thread-0002": _class("thread-0002"),
        }
        threads, cls = move_messages(
            [src, tgt],
            classifications,
            "thread-0001",
            [0],
            "thread-0002",
            "append",
        )
        assert len(threads) == 1
        assert threads[0]["thread_id"] == "thread-0002"
        assert threads[0]["num_messages"] == 2

    def test_patch_emergent_tags(self):
        record = patch_classification(
            {"topic_tags": ["overview", "custom-new"]},
            is_knowledge_bearing=True,
        )
        assert "custom-new" in record["emergent_tags"]
        assert "overview" not in record["emergent_tags"]


class TestStats:
    def test_duration_sec(self):
        assert duration_sec("2022-01-01T10:00:00", "2022-01-01T11:30:00") == 5400.0

    def test_filter_and_sort(self):
        items = [
            {
                "thread_id": "a",
                "is_useless": True,
                "is_knowledge_bearing": False,
                "num_messages": 5,
                "num_unique_senders": 2,
                "start_time": "2022-02-01",
                "duration_sec": 100,
                "topic_tags": [],
                "reason": "",
            },
            {
                "thread_id": "b",
                "is_useless": False,
                "is_knowledge_bearing": True,
                "num_messages": 10,
                "num_unique_senders": 3,
                "start_time": "2022-01-01",
                "duration_sec": 200,
                "topic_tags": ["usa"],
                "reason": "",
            },
        ]
        useless = filter_threads(items, "useless")
        assert len(useless) == 1
        sorted_items = sort_threads(items, "num_messages", "desc")
        assert sorted_items[0]["thread_id"] == "b"
        stats = compute_stats(items, "all")
        assert stats["total"] == 2
        assert stats["useless"] == 1

    def test_enrich_thread(self):
        thread = _thread("thread-0001", [_msg(0, "2022-01-01T10:00:00")])
        cls = _class("thread-0001", True)
        enriched = enrich_thread(thread, cls)
        assert enriched["is_knowledge_bearing"] is True
        assert enriched["is_useless"] is False


class TestStoreMetadata:
    def test_save_updates_thread_count_after_split(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        data = tmp_path / "data"
        data.mkdir()
        messages = [_msg(i, f"2022-01-01T{10+i}:00:00") for i in range(4)]
        t = _thread("thread-0001", messages)
        (data / "threads.json").write_text(
            json.dumps(
                {
                    "threads": [t],
                    "metadata": {"thread_count": 1, "message_count": 4},
                }
            )
        )
        (data / "threads_classified.json").write_text(
            json.dumps(
                {
                    "threads": [_class("thread-0001", True)],
                    "metadata": {
                        "thread_count": 1,
                        "knowledge_bearing_count": 1,
                    },
                }
            )
        )

        store = ThreadStore()
        store.load()
        store.split("thread-0001", "range", [2, 3])
        store.save()

        with EDITED_THREADS_PATH.open(encoding="utf-8") as f:
            threads_payload = json.load(f)
        with EDITED_CLASSIFIED_PATH.open(encoding="utf-8") as f:
            classified_payload = json.load(f)

        assert len(threads_payload["threads"]) == 2
        assert threads_payload["metadata"]["thread_count"] == 2
        assert len(classified_payload["threads"]) == 2
        assert classified_payload["metadata"]["thread_count"] == 2
        assert classified_payload["metadata"]["knowledge_bearing_count"] == 2


    def test_free_port_skips_current_pid(self, monkeypatch):
        import utils.port as port_mod

        monkeypatch.setattr(port_mod, "find_listening_pids", lambda p: {99999, 88888})
        monkeypatch.setattr(port_mod.os, "getpid", lambda: 99999)
        killed: list[int] = []

        def fake_kill(pid, sig):
            killed.append(pid)

        monkeypatch.setattr(port_mod.os, "kill", fake_kill)
        monkeypatch.setattr(port_mod, "time", type("T", (), {"sleep": staticmethod(lambda _: None)})())
        result = port_mod.free_port(8765)
        assert 99999 not in result
        assert 88888 in result
