"""A real server on a real socket: the event stream, port reuse and the command line."""

from __future__ import annotations

import json
import logging
import socket
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from review_tool import __main__ as cli
from review_tool.artifacts import GRAPH
from review_tool.port import PortFinder
from review_tool.tools import TOOL_SPECS
from tests.review_tool_support import (
    GRACEFUL_SHUTDOWN_SECONDS,
    HOST,
    build_app,
    free_port,
    new_bus,
    open_url,
    read_event,
    running_server,
    write_file,
)
from utils.config import DATA_DIR
from utils.json_io import write_json_file


def finder_for(data_dir: Path, *, search_limit: int = 20) -> PortFinder:
    return PortFinder(HOST, data_dir, health_timeout_seconds=1.0, search_limit=search_limit)


class _OtherProgram(BaseHTTPRequestHandler):
    """Some unrelated local web server that happens to sit on the wanted port."""

    def do_GET(self) -> None:  # noqa: N802 - name fixed by http.server
        body = json.dumps({"app": "something-else"}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - inherited name
        return


@pytest.fixture
def other_program_port() -> Iterator[int]:
    server = ThreadingHTTPServer((HOST, 0), _OtherProgram)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.fixture
def launched(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Any]]:
    """Run ``cli.main`` without a browser, a log file or a blocking server loop."""
    record: dict[str, list[Any]] = {"opened": [], "served_on": []}

    class RecordingServer:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def run(self, sockets: list[socket.socket]) -> None:
            record["served_on"].append(sockets[0].getsockname()[1])
            sockets[0].close()

    monkeypatch.setattr(cli.webbrowser, "open", record["opened"].append)
    monkeypatch.setattr(cli, "setup_step_logging", logging.getLogger)
    monkeypatch.setattr(cli, "ReviewServer", RecordingServer)
    return record


# ------------------------------------------------------------ event stream


def test_touched_file_reaches_an_open_event_stream(tmp_path: Path) -> None:
    write_file(tmp_path / "threads.json", {"threads": []})
    bus = new_bus()
    with running_server(build_app(tmp_path, bus=bus), bus) as port:
        with open_url(f"http://{HOST}:{port}/api/events") as stream:
            assert stream.headers["Content-Type"].startswith("text/event-stream")
            assert read_event(stream)["type"] == "hello"

            write_json_file({"threads": [1]}, tmp_path / "threads.json")  # e.g. a step re-run
            event = read_event(stream)
            assert (event["type"], event["key"]) == ("artifact_changed", "threads")
            assert event["payload"] == {"files": ["original"]}

            artifacts = json.load(open_url(f"http://{HOST}:{port}/api/artifacts"))
            assert [a["exists"] for a in artifacts] == [False, True, False]


def test_two_tabs_both_receive_the_event(tmp_path: Path) -> None:
    bus = new_bus()
    with running_server(build_app(tmp_path, bus=bus), bus) as port:
        url = f"http://{HOST}:{port}/api/events"
        with open_url(url) as first, open_url(url) as second:
            assert read_event(first)["type"] == read_event(second)["type"] == "hello"
            write_file(tmp_path / "claims_edited.json", {})
            for stream in (first, second):
                event = read_event(stream)
                assert (event["key"], event["payload"]) == ("claims", {"files": ["edited"]})


def test_shutdown_ends_open_event_streams_instead_of_waiting_for_them(tmp_path: Path) -> None:
    bus = new_bus()
    with running_server(build_app(tmp_path, bus=bus), bus) as port:
        stream = open_url(f"http://{HOST}:{port}/api/events")
        assert read_event(stream)["type"] == "hello"
        stop_requested_at = time.monotonic()
    shutdown_seconds = time.monotonic() - stop_requested_at
    with stream, pytest.raises(EOFError):
        read_event(stream)  # the server closed the stream; the browser tab was still open
    assert shutdown_seconds < GRACEFUL_SHUTDOWN_SECONDS  # did not sit out uvicorn's timeout


# -------------------------------------------------------------- port logic


def test_free_port_is_returned_bound_and_listening(tmp_path: Path) -> None:
    port = free_port()
    decision = finder_for(tmp_path).find(port)
    try:
        assert (decision.port, decision.already_running) == (port, False)
        with socket.create_connection((HOST, port), timeout=2):
            pass  # accepted into the backlog: the port is really held
    finally:
        decision.listener.close()


def test_running_review_server_is_reused(tmp_path: Path) -> None:
    bus = new_bus()
    with running_server(build_app(tmp_path, bus=bus), bus) as port:
        decision = finder_for(tmp_path).find(port)
        assert (decision.port, decision.already_running) == (port, True)
        assert finder_for(tmp_path).fetch_json(port, "health")["app"] == "review_tool"


def test_port_of_another_program_is_skipped_and_nothing_is_killed(
    tmp_path: Path, other_program_port: int
) -> None:
    decision = finder_for(tmp_path).find(other_program_port)
    try:
        assert decision.port > other_program_port and not decision.already_running
    finally:
        decision.listener.close()
    still_there = json.load(open_url(f"http://{HOST}:{other_program_port}/api/health"))
    assert still_there == {"app": "something-else"}


def test_review_server_for_another_data_dir_is_not_reused(tmp_path: Path) -> None:
    bus = new_bus()
    app = build_app(tmp_path, bus=bus, data_dir=tmp_path / "their-data")
    with running_server(app, bus) as port:
        decision = finder_for(tmp_path / "my-data").find(port)
        try:
            assert decision.port > port and not decision.already_running
        finally:
            decision.listener.close()
        assert json.load(open_url(f"http://{HOST}:{port}/api/health"))["app"] == "review_tool"


def test_no_usable_port_is_a_clear_error(tmp_path: Path, other_program_port: int) -> None:
    with pytest.raises(RuntimeError, match=f"no usable port between {other_program_port}"):
        finder_for(tmp_path, search_limit=1).find(other_program_port)


# ------------------------------------------------------------ command line


def test_second_invocation_opens_a_tab_on_the_running_server(
    tmp_path: Path, launched: dict[str, list[Any]]
) -> None:
    bus = new_bus()
    # The hosted tools are built on the pipeline graph (files in the pinned test data dir).
    app = build_app(tmp_path, bus=bus, specs=TOOL_SPECS, graph=GRAPH, data_dir=DATA_DIR)
    with running_server(app, bus) as port:
        assert cli.main(["--port", str(port), "--tool", "status"]) == 0
        assert cli.main(["--port", str(port)]) == 0
        assert cli.main(["--port", str(port), "--tool", "status", "--no-browser"]) == 0
    assert launched["opened"] == [f"http://{HOST}:{port}/?tool=status", f"http://{HOST}:{port}/"]
    assert launched["served_on"] == []  # no second server was started


def test_running_server_without_the_tool_is_reported_not_replaced(
    tmp_path: Path, launched: dict[str, list[Any]], caplog: pytest.LogCaptureFixture
) -> None:
    bus = new_bus()
    older_server = build_app(tmp_path, bus=bus, specs=(), data_dir=DATA_DIR)
    with running_server(older_server, bus) as port:
        assert cli.main(["--port", str(port), "--tool", "status"]) == 1
        assert json.load(open_url(f"http://{HOST}:{port}/api/tools")) == []  # still running
    assert "does not have the tool 'status'" in caplog.text
    assert launched == {"opened": [], "served_on": []}


def test_first_invocation_starts_a_server_on_the_port(launched: dict[str, list[Any]]) -> None:
    port = free_port()
    assert cli.main(["--port", str(port), "--tool", "status"]) == 0
    assert launched["served_on"] == [port]
    assert launched["opened"] == [f"http://{HOST}:{port}/?tool=status"]


def test_taken_port_moves_the_new_server_to_the_next_one(
    launched: dict[str, list[Any]], other_program_port: int
) -> None:
    assert cli.main(["--port", str(other_program_port), "--no-browser"]) == 0
    (served_on,) = launched["served_on"]
    assert served_on > other_program_port
    assert launched["opened"] == []


def test_unknown_tool_is_rejected_by_the_command_line(launched: dict[str, list[Any]]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--tool", "nope"])
    assert exit_info.value.code == 2
    assert launched == {"opened": [], "served_on": []}
