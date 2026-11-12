"""Synthetic C1 threads, chat export and gold windows for the conversations tool tests."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient

from review_tool.events import EventBus
from review_tool.jobs import JOB_SETTINGS, JobRunner
from review_tool.steps import StepRegistry, StepSpec
from step_1_threads_split.gold import Window
from step_1_threads_split.reviewer.gold_windows import GoldProgress, GoldWindows
from step_1_threads_split.reviewer.tool import ThreadsTool
from step_1_threads_split.splitter import thread_record
from tests.review_tool_support import BASE_URL, build_app, build_graph
from utils.json_io import write_json_file

SHORT_LOCK_TIMEOUT = 0.2
DAY = "2025-01-01"
# (wa id, minute after 10:00, sender, text, quoted wa id): the chat in export order.
CHAT = [
    ("a1", 0, "Alice", "Cats are great", None),
    ("a2", 5, "Bob", "I agree about cats", "a1"),
    ("b1", 10, "Carol", "Dogs need walks", None),
    ("b2", 12, "Dave", "Every day", "b1"),
    ("a3", 40, "Alice", "Back to cats", None),
    ("c1", 120, "Erin", "Fish tanks", None),
]
SPLIT = {"t-a1": ["a1", "a2", "a3"], "t-b1": ["b1", "b2"], "t-c1": ["c1"]}
WINDOW = {"start": f"{DAY}T10:00", "end": f"{DAY}T10:30"}
LATER_WINDOW = {"start": f"{DAY}T11:00", "end": f"{DAY}T13:00"}
METADATA = {"format": "threads-c1", "inputs": {"a1": "f1"}, "params": {"quiet_minutes": 25}}


def _time(minute: int) -> str:
    hour, minute = divmod(600 + minute, 60)
    return f"{hour:02d}:{minute:02d}"


def c1_message(wa_id: str) -> dict[str, Any]:
    _, minute, sender, text, quoted = next(row for row in CHAT if row[0] == wa_id)
    message: dict[str, Any] = {"id": wa_id, "datetime": f"{DAY}T{_time(minute)}:00", "sender": sender, "content": text}
    if quoted:
        target = next(row for row in CHAT if row[0] == quoted)
        message["quote"] = {"sender": target[2], "text": target[3]}
    return message


def c1_payload(split: dict[str, list[str]] = SPLIT) -> dict[str, Any]:
    threads = [thread_record(tid, [c1_message(wa_id) for wa_id in ids]) for tid, ids in split.items()]
    return {"threads": threads, "metadata": dict(METADATA)}


def export_entries() -> list[dict[str, Any]]:
    return [
        {"id": wa_id, "date": "01/01/2025", "time": _time(minute), "sender": {"user_name": sender}, "text": text}
        for wa_id, minute, sender, text, _ in CHAT
    ]


RUNNING_STEPS = {"threads": "Step 1 · Split into conversations", "classified": "Step 2 · Classify conversations"}


class BusyRunner(JobRunner):
    """A job runner that reports a step as running (for the frame's write guard): the one that
    produces ``produces``."""

    produces = "threads"

    def running_jobs(self) -> list[Any]:
        spec = StepSpec(self.produces, RUNNING_STEPS[self.produces], None)
        return [SimpleNamespace(step=SimpleNamespace(spec=spec))]


class ThreadsSetup:
    """The tool on synthetic files under ``root`` (export, threads, gold progress)."""

    def __init__(self, root: Path, *, payload: dict[str, Any] | None = None) -> None:
        self.root = root
        self.graph = build_graph(root, optional_reads=True)
        node = self.graph.node("threads")
        self.original, self.edited = node.original, node.edited
        self.progress = root / "gold_progress.json"
        write_json_file(c1_payload() if payload is None else payload, self.original)
        self.graph.node("export").original.write_text(json.dumps(export_entries()), encoding="utf-8")
        self.client = self.new_client()

    def gold(self) -> GoldWindows:
        windows = [Window.from_dict(LATER_WINDOW), Window.from_dict(WINDOW)]
        return GoldWindows(windows, GoldProgress(self.progress))

    def new_client(self, *, busy: str | None = None) -> TestClient:
        """``busy``: the artifact key a running step is rewriting (``None``: nothing runs)."""
        tool = ThreadsTool(self.graph, gold=self.gold(), lock_timeout=SHORT_LOCK_TIMEOUT)
        jobs = None
        if busy:
            jobs = BusyRunner(StepRegistry((), self.graph), EventBus(keepalive_seconds=0.2, queue_size=4),
                              cwd=self.root, data_dir=self.root, settings=JOB_SETTINGS)
            jobs.produces = busy
        app = build_app(self.root, specs=(tool.spec,), graph=self.graph, jobs=jobs)
        return TestClient(app, base_url=BASE_URL)

    def get(self, path: str, status: int = 200, **params: Any) -> Any:
        response = self.client.get(f"/api/threads{path}", params=params)
        assert response.status_code == status, response.text
        return response.json()

    def post(self, path: str, body: dict[str, Any] | None = None, status: int = 200) -> Any:
        response = self.client.post(f"/api/threads{path}", json=body)
        assert response.status_code == status, response.text
        return response.json()

    def saved(self) -> dict[str, Any]:
        return json.loads(self.edited.read_text(encoding="utf-8"))

    def saved_split(self) -> dict[str, list[str]]:
        return {t["thread_id"]: [m["id"] for m in t["messages"]] for t in self.saved()["threads"]}
