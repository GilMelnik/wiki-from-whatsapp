"""Event bus, artifact locks and store handles."""

from __future__ import annotations

import asyncio
import json
import threading
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from review_tool import locks
from review_tool.events import HELLO, KEEPALIVE_FRAME, EventBus
from review_tool.locks import ArtifactLockTimeout, artifact_lock, save_json
from review_tool.stores import StoreHandle
from tests.review_tool_support import locked_elsewhere, new_bus, write_file
from utils.json_io import write_json_file

SHORT_TIMEOUT = 0.2


def parse_frame(frame: str) -> dict[str, Any]:
    assert frame.startswith("data: ") and frame.endswith("\n\n")
    return json.loads(frame[len("data: ") :])


class ItemStore:
    """Minimal store: a JSON file with an ``items`` list, saved on every change."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.items: list[str] = json.loads(path.read_text(encoding="utf-8"))["items"]

    def add(self, item: str) -> None:
        self.items.append(item)
        save_json(self.path, {"items": self.items})


class CountingFactory:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.loads = 0

    def __call__(self) -> ItemStore:
        self.loads += 1
        return ItemStore(self.path)


@pytest.fixture
def items_path(tmp_path: Path) -> Path:
    path = tmp_path / "items.json"
    write_file(path, {"items": ["a"]})
    return path


# ------------------------------------------------------------------- events


def test_stream_starts_with_hello_then_delivers_published_events() -> None:
    async def scenario() -> list[dict[str, Any]]:
        bus = new_bus()
        stream = bus.stream()
        frames = [await stream.__anext__()]
        bus.publish("artifact_changed", key="claims", payload={"files": ["edited"]})
        bus.publish("job_started", tool="pii")
        frames += [await stream.__anext__(), await stream.__anext__()]
        await stream.aclose()
        return [parse_frame(frame) for frame in frames]

    hello, changed, job = asyncio.run(scenario())
    assert set(hello) == {"type", "at"} and hello["type"] == HELLO
    assert changed == {
        "type": "artifact_changed",
        "at": changed["at"],
        "key": "claims",
        "payload": {"files": ["edited"]},
    }
    assert job == {"type": "job_started", "at": job["at"], "tool": "pii"}


def test_publish_from_another_thread_reaches_the_stream() -> None:
    async def scenario() -> dict[str, Any]:
        bus = new_bus()
        stream = bus.stream()
        await stream.__anext__()
        thread = threading.Thread(target=bus.publish, args=("job_progress",))
        thread.start()
        frame = await asyncio.wait_for(stream.__anext__(), timeout=5)
        thread.join()
        await stream.aclose()
        return parse_frame(frame)

    assert asyncio.run(scenario())["type"] == "job_progress"


def test_idle_stream_sends_keepalive_comments() -> None:
    async def scenario() -> str:
        stream = new_bus(keepalive_seconds=0.05).stream()
        await stream.__anext__()
        frame = await stream.__anext__()
        await stream.aclose()
        return frame

    assert asyncio.run(scenario()) == KEEPALIVE_FRAME


def test_client_that_stops_reading_is_told_to_refetch() -> None:
    async def scenario() -> list[dict[str, Any]]:
        bus = new_bus(queue_size=2)
        stream = bus.stream()
        await stream.__anext__()
        for index in range(4):  # the third one overflows the queue of two
            bus.publish("artifact_changed", key=f"k{index}")
        await asyncio.sleep(0)  # let the deliveries run
        frames = [await stream.__anext__(), await stream.__anext__()]
        await stream.aclose()
        return [parse_frame(frame) for frame in frames]

    resync, after = asyncio.run(scenario())
    assert resync["type"] == HELLO  # k0..k2 were dropped; the client must refetch
    assert after["key"] == "k3"


def test_close_ends_open_streams_and_refuses_new_ones() -> None:
    async def scenario() -> tuple[list[str], list[str]]:
        bus = new_bus(keepalive_seconds=30)
        stream = bus.stream()
        await stream.__anext__()
        bus.close()
        rest = [frame async for frame in stream]
        late = [frame async for frame in bus.stream()]
        return rest, late

    assert asyncio.run(scenario()) == ([], [])


def test_publish_without_subscribers_returns_the_event() -> None:
    event = EventBus(keepalive_seconds=1, queue_size=1).publish("hello")
    assert event["type"] == "hello" and event["at"].endswith("+00:00")


# -------------------------------------------------------------------- locks


def test_artifact_lock_is_reentrant_within_a_thread(tmp_path: Path) -> None:
    first, second = tmp_path / "a.json", tmp_path / "b.json"
    with artifact_lock(second, first, timeout=SHORT_TIMEOUT):
        with artifact_lock(first, timeout=SHORT_TIMEOUT):
            save_json(first, {"ok": True})  # takes the same lock a third time
    assert json.loads(first.read_text(encoding="utf-8")) == {"ok": True}
    with locked_elsewhere(first):  # fully released above: another thread can take it
        pass


def test_lock_contention_times_out_cleanly(tmp_path: Path) -> None:
    target = tmp_path / "claims.json"
    with locked_elsewhere(target):
        with pytest.raises(ArtifactLockTimeout, match="claims.json is locked"):
            with artifact_lock(target, timeout=SHORT_TIMEOUT):
                pytest.fail("the lock must not be granted")
    with artifact_lock(target, timeout=SHORT_TIMEOUT):  # free again, nothing left behind
        pass
    assert issubclass(ArtifactLockTimeout, TimeoutError)


def test_timeout_on_second_file_releases_the_first(tmp_path: Path) -> None:
    first, second = tmp_path / "a.json", tmp_path / "b.json"
    with locked_elsewhere(second):
        with pytest.raises(ArtifactLockTimeout):
            with artifact_lock(first, second, timeout=SHORT_TIMEOUT):
                pytest.fail("the lock must not be granted")
        with locked_elsewhere(first):  # would block if the failed attempt still held it
            pass


def test_save_json_waits_for_the_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        locks, "SETTINGS", replace(locks.SETTINGS, lock_timeout_seconds=SHORT_TIMEOUT)
    )
    target = tmp_path / "plan.json"
    write_file(target, {"version": 1})
    with locked_elsewhere(target):
        with pytest.raises(ArtifactLockTimeout):
            save_json(target, {"version": 2})
    assert json.loads(target.read_text(encoding="utf-8")) == {"version": 1}
    save_json(target, {"version": 2, "title": "תוכנית"})
    assert json.loads(target.read_text(encoding="utf-8")) == {"version": 2, "title": "תוכנית"}


# ------------------------------------------------------------------- stores


def test_store_is_loaded_once_while_files_are_unchanged(items_path: Path) -> None:
    factory = CountingFactory(items_path)
    handle = StoreHandle(factory, [items_path])
    assert factory.loads == 0  # lazy
    assert handle.get() is handle.get()
    assert factory.loads == 1


def test_store_reloads_after_an_external_rewrite(items_path: Path) -> None:
    factory = CountingFactory(items_path)
    handle = StoreHandle(factory, [items_path])
    assert handle.get().items == ["a"]

    write_json_file({"items": ["a", "from-a-step"]}, items_path)  # a pipeline step ran
    assert handle.get().items == ["a", "from-a-step"]
    assert handle.get().items == ["a", "from-a-step"]
    assert factory.loads == 2


def test_store_reloads_when_a_watched_file_appears(items_path: Path, tmp_path: Path) -> None:
    factory = CountingFactory(items_path)
    extra = tmp_path / "items_edited.json"
    handle = StoreHandle(factory, [items_path, extra])
    handle.get()
    write_file(extra, {"items": []})
    handle.get()
    assert factory.loads == 2


def test_own_save_inside_mutate_does_not_reload(items_path: Path) -> None:
    factory = CountingFactory(items_path)
    handle = StoreHandle(factory, [items_path])
    with handle.mutate() as store:
        store.add("b")
    assert handle.get().items == ["a", "b"]
    assert factory.loads == 1
    assert json.loads(items_path.read_text(encoding="utf-8")) == {"items": ["a", "b"]}


def test_mutation_is_never_applied_to_stale_memory(items_path: Path) -> None:
    handle = StoreHandle(CountingFactory(items_path), [items_path])
    handle.get()
    write_json_file({"items": ["a", "from-a-step"]}, items_path)
    with handle.mutate() as store:
        store.add("mine")
    assert json.loads(items_path.read_text(encoding="utf-8")) == {
        "items": ["a", "from-a-step", "mine"]
    }


def test_get_inside_mutate_returns_the_store_being_changed(items_path: Path) -> None:
    factory = CountingFactory(items_path)
    handle = StoreHandle(factory, [items_path])
    with handle.mutate() as store:
        store.add("b")  # the file on disk is now newer than what was loaded
        assert handle.get() is store
        with handle.mutate() as nested:
            assert nested is store
    assert factory.loads == 1


def test_failed_mutation_discards_the_in_memory_store(items_path: Path) -> None:
    factory = CountingFactory(items_path)
    handle = StoreHandle(factory, [items_path])
    with pytest.raises(RuntimeError, match="boom"):
        with handle.mutate() as store:
            store.items.append("never-saved")
            raise RuntimeError("boom")
    assert handle.get().items == ["a"]
    assert factory.loads == 2


def test_mutate_times_out_when_the_file_is_locked_elsewhere(items_path: Path) -> None:
    handle = StoreHandle(CountingFactory(items_path), [items_path], lock_timeout=SHORT_TIMEOUT)
    with locked_elsewhere(items_path):
        with pytest.raises(ArtifactLockTimeout):
            with handle.mutate():
                pytest.fail("the store must not be handed out")
        assert handle.get().items == ["a"]  # reading needs no lock
    with handle.mutate() as store:
        store.add("b")
    assert handle.get().items == ["a", "b"]


def test_lock_paths_limit_what_mutate_locks(items_path: Path, tmp_path: Path) -> None:
    read_only_input = tmp_path / "upstream.json"
    write_file(read_only_input, {})
    handle = StoreHandle(
        CountingFactory(items_path),
        [items_path, read_only_input],
        lock_paths=[items_path],
        lock_timeout=SHORT_TIMEOUT,
    )
    with locked_elsewhere(read_only_input):
        with handle.mutate() as store:
            store.add("b")
    assert handle.get().items == ["a", "b"]


def test_factory_returning_none_is_still_cached(tmp_path: Path) -> None:
    calls: list[int] = []
    handle: StoreHandle[None] = StoreHandle(lambda: calls.append(1), [tmp_path / "absent.json"])
    assert handle.get() is None and handle.get() is None
    assert len(calls) == 1


def test_upstream_change_during_a_mutation_still_reloads(items_path: Path, tmp_path: Path) -> None:
    upstream = tmp_path / "upstream.json"
    write_file(upstream, {"v": 1})
    factory = CountingFactory(items_path)
    handle = StoreHandle(factory, [items_path, upstream], lock_paths=[items_path])
    with handle.mutate() as store:
        store.add("b")
        write_json_file({"v": 2}, upstream)  # a step rewrote an input meanwhile
    assert factory.loads == 1
    handle.get()
    assert factory.loads == 2
