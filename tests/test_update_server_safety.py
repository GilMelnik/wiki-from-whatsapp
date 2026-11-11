"""Step 10 next to a running review server: the lease, the job runner's refusal, the write
guard's 423 for the step the update runs, and the driver's check of jobs and applies."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

from review_tool.jobs import JOB_SETTINGS, JobRefused, JobRunner
from review_tool.port import PortFinder
from review_tool.registry import ToolSpec
from review_tool.steps import StepRegistry, StepSpec
from step_10_update.server import ReviewServerCheck
from tests.review_tool_support import BASE_URL, HOST, build_app, build_graph, new_bus, running_server
from utils.config import PROJECT_ROOT
from utils.step_lease import LeaseHeld, StepLease


def test_the_lease_says_who_runs_what_and_is_exclusive(tmp_path: Path) -> None:
    lease = StepLease(tmp_path / "running.json")
    assert lease.current() is None
    with lease.hold():
        assert lease.current()["step"] is None
        with lease.running("step_3", "claims", "Claims step"):
            assert lease.current()["produces"] == "claims"
        assert lease.current()["produces"] is None
        with pytest.raises(LeaseHeld, match="another update is running"):
            with StepLease(tmp_path / "running.json").hold():
                pass
    assert lease.current() is None


def _runner(root: Path, lease: StepLease) -> JobRunner:
    steps = StepRegistry(
        (StepSpec("claims", "Claims step", "tests.fake_step", incremental_safe=True),),
        build_graph(root),
    )
    return JobRunner(steps, new_bus(), cwd=PROJECT_ROOT, data_dir=root, settings=JOB_SETTINGS,
                     lease=lease)


def _writer() -> ToolSpec:
    router = APIRouter()

    @router.post("/claims")
    def write() -> dict[str, str]:
        return {"ok": "written"}

    return ToolSpec("writer", "Writer", router, reads=("claims",), writes=("claims",))


def test_server_refuses_jobs_and_saves_while_the_update_runs_a_step(tmp_path: Path) -> None:
    lease = StepLease(tmp_path / "running.json")
    runner = _runner(tmp_path, lease)
    client = TestClient(build_app(tmp_path, specs=(_writer(),), jobs=runner), base_url=BASE_URL)
    with lease.hold():
        with pytest.raises(JobRefused, match="update") as refused:
            runner.run_step("step_3")
        assert refused.value.status == 409
        assert client.post("/api/writer/claims").json() == {"ok": "written"}  # between steps
        with lease.running("step_3", "claims", "Claims step"):
            blocked = client.post("/api/writer/claims")
            assert blocked.status_code == 423 and "Claims step" in blocked.json()["detail"]
        with lease.running("step_1", "threads", "Threads step"):
            assert client.post("/api/writer/claims").status_code == 200  # writes no threads
    assert runner.running_jobs() == [] and client.post("/api/writer/claims").status_code == 200


class FakeFinder:
    """Stands in for ``PortFinder``: a server on ``port`` (or none) answering ``answers``."""

    def __init__(self, port: int | None, answers: dict[str, Any]) -> None:
        self.port = port
        self.answers = answers

    def running_port(self, preferred_port: int) -> int | None:
        return self.port

    def fetch_json(self, port: int, api_path: str) -> Any | None:
        return self.answers.get(api_path)


IDLE = {"jobs": [{"state": "succeeded", "title": "Step 2"}],
        "tools": [{"name": "suggestions"}], "suggestions/applies": {"running": None, "queue": []}}


@pytest.mark.parametrize(("port", "answers", "expected"), [
    (None, {}, None),
    (8780, IDLE, None),
    (8780, {**IDLE, "jobs": [{"state": "running", "title": "Step 2"}]}, "is running Step 2"),
    (8780, {**IDLE, "suggestions/applies": {"running": {"id": "s"}, "queue": [{"id": "t"}]}},
     "2 suggestion(s)"),
    (8780, {**IDLE, "tools": [{"name": "groups"}], "suggestions/applies": None}, None),
    (8780, {**IDLE, "suggestions/applies": None}, "did not answer"),
    (8780, {}, "did not answer GET /api/jobs"),
])
def test_the_driver_asks_the_server_about_jobs_and_applies(
    port: int | None, answers: dict[str, Any], expected: str | None
) -> None:
    reason = ReviewServerCheck(FakeFinder(port, answers))()  # type: ignore[arg-type]
    assert reason == expected if expected is None else expected in reason


def test_the_check_against_a_real_server_sees_queued_applies(tmp_path: Path) -> None:
    queue: list[dict[str, str]] = []
    router = APIRouter()

    @router.get("/applies")
    def applies() -> dict[str, Any]:
        return {"running": None, "queue": queue, "outcomes": []}

    tool = ToolSpec("suggestions", "Suggestions", router, reads=("claims",), writes=("claims",))
    bus = new_bus()
    app = build_app(tmp_path, specs=(tool,), bus=bus, jobs=_runner(tmp_path, StepLease(tmp_path / "l")))
    with running_server(app, bus) as port:
        finder = PortFinder(HOST, tmp_path, health_timeout_seconds=1.0, search_limit=1)
        check = ReviewServerCheck(finder, port=port)
        assert finder.running_port(port) == port and check() is None
        queue.append({"id": "sug-1"})
        assert "1 suggestion(s)" in check()
    assert check() is None  # the server is gone
