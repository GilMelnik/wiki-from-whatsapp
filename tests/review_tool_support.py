"""Shared helpers for the ``test_review_tool_*`` modules (synthetic data, temp dirs only)."""

from __future__ import annotations

import json
import os
import socket
import threading
import time
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from http.client import HTTPResponse
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from review_tool.app import ReviewApp
from review_tool.artifacts import ArtifactGraph, ArtifactNode
from review_tool.events import EventBus
from review_tool.jobs import JOB_SETTINGS, JobRunner
from review_tool.registry import ToolRegistry, ToolSpec
from review_tool.server import ReviewServer
from review_tool.settings import SETTINGS, ReviewSettings
from review_tool.steps import StepRegistry
from utils.file_lock import file_lock

HOST = "127.0.0.1"
BASE_URL = f"http://{HOST}"
STARTUP_TIMEOUT_SECONDS = 10.0
GRACEFUL_SHUTDOWN_SECONDS = 2  # uvicorn cancels whatever is still running after this long
# Local requests must not go through a system HTTP proxy.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def build_graph(root: Path) -> ArtifactGraph:
    """A three-step pipeline under ``root``: export -> threads -> claims (both reviewed)."""
    return ArtifactGraph(
        [
            ArtifactNode("export", "Export", root / "export.json"),
            ArtifactNode(
                "threads",
                "Threads",
                root / "threads.json",
                root / "threads_edited.json",
                "step_1",
                ("export",),
            ),
            ArtifactNode(
                "claims",
                "Claims",
                root / "claims.json",
                root / "claims_edited.json",
                "step_3",
                ("threads",),
            ),
        ]
    )


def write_file(path: Path, payload: dict[str, Any], *, mtime: float | None = None) -> None:
    """Write ``payload`` as JSON to ``path`` and optionally pin its modification time."""
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))


def fast_settings(root: Path) -> ReviewSettings:
    """The project settings with fast timers and a web build folder under ``root``."""
    return replace(
        SETTINGS,
        watch_interval_seconds=0.05,
        watch_debounce_seconds=0.05,
        sse_keepalive_seconds=0.2,
        health_timeout_seconds=1.0,
        web_dist=root / "dist",
    )


def new_bus(*, keepalive_seconds: float = 0.2, queue_size: int = 16) -> EventBus:
    return EventBus(keepalive_seconds=keepalive_seconds, queue_size=queue_size)


def build_app(
    root: Path,
    *,
    specs: tuple[ToolSpec, ...] = (),
    graph: ArtifactGraph | None = None,
    bus: EventBus | None = None,
    jobs: JobRunner | None = None,
    data_dir: Path | None = None,
    bind_host: str = HOST,
) -> FastAPI:
    graph = build_graph(root) if graph is None else graph
    bus = new_bus() if bus is None else bus
    if jobs is None:
        jobs = JobRunner(
            StepRegistry((), graph), bus, cwd=root, data_dir=root, settings=JOB_SETTINGS
        )
    return ReviewApp(
        registry=ToolRegistry(specs),
        graph=graph,
        bus=bus,
        jobs=jobs,
        settings=fast_settings(root),
        data_dir=root if data_dir is None else data_dir,
        bind_host=bind_host,
    ).build()


def free_port() -> int:
    """A port number that was free a moment ago."""
    with socket.socket() as probe:
        probe.bind((HOST, 0))
        return probe.getsockname()[1]


@contextmanager
def running_server(app: FastAPI, bus: EventBus, *, port: int = 0) -> Iterator[int]:
    """Serve ``app`` on a real socket in a background thread; yield the port.

    Leaving the block stops the server and fails if it does not shut down promptly (for
    example because an open event stream kept it alive).
    """
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind((HOST, port))
    listener.listen()
    server = ReviewServer(app, bus, shutdown_timeout_seconds=GRACEFUL_SHUTDOWN_SECONDS)
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    while not server.started:
        if time.monotonic() > deadline or not thread.is_alive():
            raise RuntimeError("the test server did not start")
        time.sleep(0.01)
    try:
        yield listener.getsockname()[1]
    finally:
        server.should_exit = True
        thread.join(timeout=STARTUP_TIMEOUT_SECONDS)
        if thread.is_alive():
            raise RuntimeError("the test server did not shut down")


def open_url(url: str, *, timeout: float = 5.0) -> HTTPResponse:
    return _OPENER.open(url, timeout=timeout)


def read_event(stream: HTTPResponse) -> dict[str, Any]:
    """Read the next ``data:`` frame of an SSE stream (keep-alive comments are skipped)."""
    while True:
        line = stream.readline().decode("utf-8")
        if not line:
            raise EOFError("the event stream ended")
        if line.startswith("data: "):
            return json.loads(line[len("data: ") :])


@contextmanager
def locked_elsewhere(path: Path) -> Iterator[None]:
    """Hold the lock of ``path`` in another thread for the duration of the block."""
    held = threading.Event()
    release = threading.Event()

    def holder() -> None:
        with file_lock(path):
            held.set()
            release.wait(timeout=10)

    thread = threading.Thread(target=holder)
    thread.start()
    try:
        assert held.wait(timeout=5)
        yield
    finally:
        release.set()
        thread.join()
