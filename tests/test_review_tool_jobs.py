"""The step runner: registry, jobs as subprocesses, events, cancel, write guard, shutdown.

Every job runs ``tests/fake_step.py`` (no pipeline code, no API calls) on files under
``tmp_path``.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

from review_tool.artifacts import GRAPH, ArtifactGraph
from review_tool.events import EventBus
from review_tool.jobs import (
    CANCELLED,
    FAILED,
    JOB_FINISHED,
    JOB_PROGRESS,
    JOB_SETTINGS,
    JOB_STARTED,
    RUNNING,
    SUCCEEDED,
    Job,
    JobRefused,
    JobRunner,
)
from review_tool.registry import ToolSpec
from review_tool.steps import SERVED_STEPS, StepRegistry, StepSpec
from review_tool.watcher import ArtifactWatcher
from tests.review_tool_support import BASE_URL, build_app, build_graph, running_server, write_file
from utils.config import PROJECT_ROOT

FAKE_STEP = "tests.fake_step"
WAIT_SECONDS = 15.0
SUPERSEDED_LINE = "reconciled claims.json: ...; manual edits now only in the backup: superseded=['c1']"


class RecordingBus(EventBus):
    """An event bus that also keeps every published event for the test to inspect."""

    def __init__(self) -> None:
        super().__init__(keepalive_seconds=0.2, queue_size=16)
        self.events: list[dict[str, Any]] = []

    def publish(self, event_type: str, **fields: Any) -> dict[str, Any]:
        event = super().publish(event_type, **fields)
        self.events.append(event)
        return event

    def of_type(self, event_type: str) -> list[dict[str, Any]]:
        return [event for event in self.events if event["type"] == event_type]


def wait_until(condition: Callable[[], Any], what: str) -> Any:
    deadline = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < deadline:
        if result := condition():
            return result
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {what}")


def fake_registry(graph: ArtifactGraph) -> StepRegistry:
    return StepRegistry(
        (
            StepSpec("threads", "Threads step", FAKE_STEP),
            StepSpec("claims", "Claims step", FAKE_STEP, incremental_safe=True, paid=True),
        ),
        graph,
    )


@pytest.fixture
def graph(tmp_path: Path) -> ArtifactGraph:
    return build_graph(tmp_path)


@pytest.fixture
def bus() -> RecordingBus:
    return RecordingBus()


@pytest.fixture
def runner(tmp_path: Path, graph: ArtifactGraph, bus: RecordingBus) -> Iterator[JobRunner]:
    settings = replace(JOB_SETTINGS, progress_interval_seconds=0.05, stop_timeout_seconds=2.0)
    runner = JobRunner(
        fake_registry(graph), bus, cwd=PROJECT_ROOT, data_dir=tmp_path, settings=settings
    )
    yield runner
    runner.shutdown()


@pytest.fixture
def fake_step(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Callable[..., None]:
    """Configure the next fake step run; it rewrites ``claims.json`` by default."""

    def configure(**options: object) -> None:
        values = {"target": tmp_path / "claims.json", **options}
        for name, value in values.items():
            monkeypatch.setenv(f"FAKE_STEP_{name.upper()}", str(value))

    return configure


def finished(runner: JobRunner, job_id: str) -> dict[str, Any]:
    return wait_until(
        lambda: (job := runner.job(job_id))["state"] != RUNNING and job, "the job to finish"
    )


def the_job(runner: JobRunner, job_id: str) -> Job:
    """The live ``Job`` object (for its process)."""
    return next(job for job in runner.running_jobs() if job.id == job_id)


# --------------------------------------------------------------------------- registry


def test_served_registry_matches_the_graph_and_safe_steps_can_run() -> None:
    steps = {step.key: step for step in SERVED_STEPS.steps}
    for step in steps.values():
        assert GRAPH.node(step.spec.produces).step == step.key
    # More steps join this set as they are migrated to incremental runs; these two were first.
    safe = {key for key, step in steps.items() if step.spec.incremental_safe}
    assert {"step_2_classify", "step_3_extract"} <= safe
    for key in safe:
        assert steps[key].command()[1] == "-m"
    assert steps["step_3_extract"].spec.paid
    assert steps["step_2_classify"].command()[1:] == ["-m", "step_2_classify.run"]
    assert steps["step_2_classify"].command(rebuild=True)[-1] == "--rebuild"


def test_registry_rejects_rows_it_cannot_run(graph: ArtifactGraph) -> None:
    with pytest.raises(ValueError, match="no producing step"):
        StepRegistry((StepSpec("export", "Export", FAKE_STEP),), graph)
    with pytest.raises(ValueError, match="no module"):
        StepRegistry((StepSpec("claims", "Claims", None, incremental_safe=True),), graph)
    with pytest.raises(ValueError, match="twice"):
        StepRegistry((StepSpec("claims", "A", None), StepSpec("claims", "B", None)), graph)
    with pytest.raises(KeyError, match="unknown artifact key"):
        StepRegistry((StepSpec("nope", "Nope", None),), graph)


# --------------------------------------------------------------------------- running a job


def test_job_streams_progress_and_finishes(
    runner: JobRunner, bus: RecordingBus, fake_step: Callable[..., None], tmp_path: Path
) -> None:
    fake_step(lines=5, highlight=SUPERSEDED_LINE)
    started = runner.run_step("step_3")
    assert started["state"] == RUNNING and started["produces"] == "claims"
    job = finished(runner, started["id"])

    assert job["state"] == SUCCEEDED and job["exit_code"] == 0 and job["ended_at"]
    assert (tmp_path / "claims.json").read_text(encoding="utf-8").count("fake_step") == 1
    assert any("line 4" in line for line in job["log"])
    assert job["log_start"] == 0 and job["line_count"] == len(job["log"])
    assert job["highlights"] == [line for line in job["log"] if SUPERSEDED_LINE in line]
    assert len(job["highlights"]) == 1

    (start_event,) = bus.of_type(JOB_STARTED)
    assert start_event["key"] == "claims" and start_event["payload"]["id"] == started["id"]
    streamed: list[str] = []
    for event in bus.of_type(JOB_PROGRESS):
        assert event["key"] == "claims" and event["payload"]["from"] == len(streamed)
        streamed.extend(event["payload"]["lines"])
    assert streamed == job["log"]
    assert sum(len(event["payload"]["highlights"]) for event in bus.of_type(JOB_PROGRESS)) == 1
    (finish_event,) = bus.of_type(JOB_FINISHED)
    assert finish_event["payload"]["state"] == SUCCEEDED
    assert runner.jobs()[0]["id"] == started["id"]


def test_rebuild_flag_reaches_the_step(runner: JobRunner, fake_step: Callable[..., None]) -> None:
    fake_step(lines=0)
    job = finished(runner, runner.run_step("step_3", rebuild=True)["id"])
    assert job["rebuild"] and any("rebuild=True" in line for line in job["log"])


def test_watcher_announces_the_rewritten_artifact(
    runner: JobRunner,
    bus: RecordingBus,
    graph: ArtifactGraph,
    fake_step: Callable[..., None],
    tmp_path: Path,
) -> None:
    write_file(tmp_path / "threads.json", {}, mtime=2000)
    write_file(tmp_path / "claims.json", {}, mtime=1000)
    assert [s.stale_because for s in graph.status() if s.key == "claims"] == [("threads",)]
    watcher = ArtifactWatcher(graph, bus, interval_seconds=0.05, debounce_seconds=0.0)
    fake_step(lines=1)
    finished(runner, runner.run_step("step_3")["id"])

    now = time.monotonic()
    watcher.poll_once(now)
    (changed,) = watcher.poll_once(now + 1)
    assert changed["type"] == "artifact_changed" and changed["key"] == "claims"
    assert [s.stale_because for s in graph.status() if s.key == "claims"] == [()]


def test_failed_job_keeps_exit_code_and_log(
    runner: JobRunner, bus: RecordingBus, fake_step: Callable[..., None]
) -> None:
    fake_step(exit=3)
    job = finished(runner, runner.run_step("step_3")["id"])
    assert (job["state"], job["exit_code"]) == (FAILED, 3)
    assert "exit code 3" in job["log"][-1]
    assert bus.of_type(JOB_FINISHED)[0]["payload"]["state"] == FAILED


def test_cancel_stops_the_step(runner: JobRunner, fake_step: Callable[..., None]) -> None:
    fake_step(lines=1, hold=60)
    job_id = runner.run_step("step_3")["id"]
    wait_until(lambda: runner.job(job_id)["line_count"] >= 2, "the step to log")
    assert runner.cancel(job_id)["cancel_requested"]
    job = finished(runner, job_id)
    assert job["state"] == CANCELLED and job["exit_code"] != 0
    with pytest.raises(JobRefused) as refused:
        runner.cancel(job_id)
    assert refused.value.status == 409


def test_a_step_that_ignores_sigint_is_killed(
    runner: JobRunner, fake_step: Callable[..., None]
) -> None:
    fake_step(lines=1, hold=60, ignore_sigint=1)
    job_id = runner.run_step("step_3")["id"]
    wait_until(lambda: runner.job(job_id)["line_count"] >= 2, "the step to log")
    runner.cancel(job_id)
    job = finished(runner, job_id)
    assert job["state"] == CANCELLED and job["exit_code"] == -9


def test_one_job_per_artifact(runner: JobRunner, fake_step: Callable[..., None]) -> None:
    fake_step(hold=60)
    job_id = runner.run_step("step_3")["id"]
    with pytest.raises(JobRefused) as refused:
        runner.run_step("step_3")
    assert refused.value.status == 409 and "already running" in refused.value.detail
    runner.cancel(job_id)
    finished(runner, job_id)
    fake_step(hold=0)
    assert finished(runner, runner.run_step("step_3")["id"])["state"] == SUCCEEDED


def test_a_step_waits_for_the_step_that_feeds_it(
    tmp_path: Path, bus: RecordingBus, fake_step: Callable[..., None]
) -> None:
    graph = build_graph(tmp_path)
    registry = StepRegistry(
        (
            StepSpec("threads", "Threads step", FAKE_STEP, incremental_safe=True),
            StepSpec("claims", "Claims step", FAKE_STEP, incremental_safe=True),
        ),
        graph,
    )
    runner = JobRunner(registry, bus, cwd=PROJECT_ROOT, data_dir=tmp_path, settings=JOB_SETTINGS)
    fake_step(target=tmp_path / "threads.json", hold=60)
    job_id = runner.run_step("step_1")["id"]
    with pytest.raises(JobRefused) as refused:
        runner.run_step("step_3")
    assert refused.value.status == 409 and "Threads step" in refused.value.detail
    runner.shutdown()
    assert runner.job(job_id)["state"] == CANCELLED


def test_steps_that_are_not_incremental_safe_are_refused(runner: JobRunner) -> None:
    with pytest.raises(JobRefused) as refused:
        runner.run_step("step_1")
    assert refused.value.status == 409 and FAKE_STEP in refused.value.detail
    with pytest.raises(JobRefused) as unknown:
        runner.run_step("step_9")
    assert unknown.value.status == 404
    assert runner.jobs() == []


def test_history_is_capped(
    tmp_path: Path, graph: ArtifactGraph, bus: RecordingBus, fake_step: Callable[..., None]
) -> None:
    settings = replace(JOB_SETTINGS, progress_interval_seconds=0.05, history_limit=2)
    runner = JobRunner(fake_registry(graph), bus, cwd=PROJECT_ROOT, data_dir=tmp_path, settings=settings)
    fake_step(lines=0)
    ids = [finished(runner, runner.run_step("step_3")["id"])["id"] for _ in range(3)]
    assert [job["id"] for job in runner.jobs()] == ids[:0:-1]


def test_shutdown_stops_running_jobs(runner: JobRunner, fake_step: Callable[..., None]) -> None:
    fake_step(lines=1, hold=60)
    job_id = runner.run_step("step_3")["id"]
    process = the_job(runner, job_id).process
    runner.shutdown()
    assert process.poll() is not None
    assert runner.job(job_id)["state"] == CANCELLED
    with pytest.raises(JobRefused) as refused:
        runner.run_step("step_3")
    assert refused.value.status == 503


# --------------------------------------------------------------------------- HTTP


def writer_spec() -> ToolSpec:
    router = APIRouter()

    @router.get("/claims")
    def read() -> dict[str, str]:
        return {"ok": "read"}

    @router.post("/claims")
    def write() -> dict[str, str]:
        return {"ok": "written"}

    return ToolSpec("writer", "Writer", router, reads=("claims",), writes=("claims",))


def reader_spec() -> ToolSpec:
    router = APIRouter()

    @router.post("/note")
    def note() -> dict[str, str]:
        return {"ok": "noted"}

    return ToolSpec("reader", "Reader", router, reads=("claims",), writes=("threads",))


def test_http_api_and_write_guard(
    tmp_path: Path, runner: JobRunner, bus: RecordingBus, fake_step: Callable[..., None]
) -> None:
    app = build_app(tmp_path, specs=(writer_spec(), reader_spec()), bus=bus, jobs=runner)
    client = TestClient(app, base_url=BASE_URL)
    steps = client.get("/api/steps").json()
    assert [step["key"] for step in steps] == ["step_1", "step_3"]
    assert steps[1] == {
        "key": "step_3",
        "title": "Claims step",
        "produces": "claims",
        "incremental_safe": True,
        "paid": True,
        "command": f"python -m {FAKE_STEP}",
        "rebuild_command": f"python -m {FAKE_STEP} --rebuild",
    }
    refused = client.post("/api/jobs", json={"step": "step_1"})
    assert refused.status_code == 409 and refused.json()["detail"]
    assert client.post("/api/jobs", json={"step": "nope"}).status_code == 404

    fake_step(lines=1, hold=60)
    started = client.post("/api/jobs", json={"step": "step_3"})
    assert started.status_code == 202
    job_id = started.json()["id"]
    assert client.get("/api/jobs").json()[0]["id"] == job_id

    blocked = client.post("/api/writer/claims")
    assert blocked.status_code == 423 and "Claims step" in blocked.json()["detail"]
    assert client.get("/api/writer/claims").json() == {"ok": "read"}
    assert client.post("/api/reader/note").json() == {"ok": "noted"}

    assert client.post(f"/api/jobs/{job_id}/cancel").json()["cancel_requested"]
    wait_until(lambda: client.get(f"/api/jobs/{job_id}").json()["state"] == CANCELLED, "cancel")
    assert client.post(f"/api/jobs/{job_id}/cancel").status_code == 409
    assert client.get("/api/jobs/unknown").status_code == 404
    assert client.post("/api/writer/claims").json() == {"ok": "written"}


def test_server_shutdown_terminates_its_jobs(
    tmp_path: Path, runner: JobRunner, bus: RecordingBus, fake_step: Callable[..., None]
) -> None:
    fake_step(lines=1, hold=60)
    app = build_app(tmp_path, bus=bus, jobs=runner)
    with running_server(app, bus):
        job_id = runner.run_step("step_3")["id"]
        process = the_job(runner, job_id).process
    assert process.poll() is not None
    assert runner.job(job_id)["state"] == CANCELLED
