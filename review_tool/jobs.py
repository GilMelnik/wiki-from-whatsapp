"""Run pipeline steps as subprocesses of the server and stream their output as events.

``JobRunner.run_step(step)`` starts ``python -m <module>`` of an incremental-safe step
(``review_tool.steps``) with this interpreter, the repository root as working directory and the
server's data directory in ``WIKI_DATA_DIR``. Its merged stdout/stderr is kept as a tail of
``review.jobs.log_lines`` lines and published in batches. Events (all with ``key`` = the
artifact the step produces):

- ``job_started``  ``payload`` = the job summary (``Job.summary``)
- ``job_progress`` ``payload`` = ``{id, from, lines, highlights}``: new output lines, ``from``
  is the index of the first of them in the job's whole output
- ``job_finished`` ``payload`` = the final job summary

At most one job runs per produced artifact. The runner only ever signals the process group of a
child it started itself and has not reaped yet: a cancel sends SIGINT (the step's ``finally``
blocks and file locks unwind), then SIGKILL after ``review.jobs.stop_timeout_seconds``.
``shutdown()`` does the same for every running job when the server stops.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from review_tool.events import EventBus
from review_tool.fs import iso_utc
from review_tool.steps import Step, StepRegistry
from utils.config import section

logger = logging.getLogger(__name__)

JOB_STARTED = "job_started"
JOB_PROGRESS = "job_progress"
JOB_FINISHED = "job_finished"

RUNNING = "running"
SUCCEEDED = "succeeded"
FAILED = "failed"
CANCELLED = "cancelled"


@dataclass(frozen=True)
class JobSettings:
    log_lines: int
    progress_interval_seconds: float
    stop_timeout_seconds: float
    history_limit: int
    highlight_patterns: tuple[str, ...]

    @classmethod
    def from_config(cls, raw: dict[str, Any]) -> JobSettings:
        """Build the settings from the raw ``review.jobs`` section; name any missing key."""
        try:
            return cls(
                log_lines=int(raw["log_lines"]),
                progress_interval_seconds=float(raw["progress_interval_seconds"]),
                stop_timeout_seconds=float(raw["stop_timeout_seconds"]),
                history_limit=int(raw["history_limit"]),
                highlight_patterns=tuple(str(pattern) for pattern in raw["highlight_patterns"]),
            )
        except KeyError as exc:
            raise KeyError(
                f"config.json section 'review.jobs' is missing the key {exc.args[0]!r}"
            ) from None


JOB_SETTINGS = JobSettings.from_config(section("review")["jobs"])


class JobRefused(Exception):
    """A job cannot be started or changed; ``status`` is the HTTP status to answer with."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


class Job:
    """One run of one step. All mutable fields are guarded by the runner's lock."""

    def __init__(self, step: Step, rebuild: bool, process: subprocess.Popen, log_lines: int) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.step = step
        self.rebuild = rebuild
        self.process = process
        self.started_at = time.time()
        self.ended_at: float | None = None
        self.state = RUNNING
        self.exit_code: int | None = None
        self.cancel_requested = False
        self.tail: deque[str] = deque(maxlen=log_lines)
        self.line_count = 0
        self.unpublished: list[str] = []
        self.highlights: list[str] = []
        self.output_done = threading.Event()
        self.supervisor: threading.Thread | None = None

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "step": self.step.key,
            "title": self.step.spec.title,
            "produces": self.step.spec.produces,
            "rebuild": self.rebuild,
            "state": self.state,
            "exit_code": self.exit_code,
            "started_at": iso_utc(self.started_at),
            "ended_at": None if self.ended_at is None else iso_utc(self.ended_at),
            "line_count": self.line_count,
            "highlights": list(self.highlights),
            "cancel_requested": self.cancel_requested,
        }

    def detail(self) -> dict[str, Any]:
        """The summary plus the kept tail of the output: ``log_start`` is the index of its first
        line, ``log_limit`` how many lines are kept."""
        return {
            **self.summary(),
            "log": list(self.tail),
            "log_start": self.line_count - len(self.tail),
            "log_limit": self.tail.maxlen,
        }


class JobRunner:
    def __init__(
        self,
        steps: StepRegistry,
        bus: EventBus,
        *,
        cwd: Path,
        data_dir: Path,
        settings: JobSettings,
    ) -> None:
        self.steps = steps
        self._bus = bus
        self._cwd = cwd
        self._data_dir = data_dir
        self._settings = settings
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._closed = False

    # ------------------------------------------------------------------ public API

    def run_step(self, step_key: str, *, rebuild: bool = False) -> dict[str, Any]:
        """Start the step and return the job summary; ``JobRefused`` when it may not run now."""
        try:
            step = self.steps.step(step_key)
        except KeyError as exc:
            raise JobRefused(404, exc.args[0]) from None
        spec = step.spec
        if not spec.incremental_safe:
            hint = f" `python -m {spec.module}`" if spec.module else ""
            raise JobRefused(
                409,
                f'את "{spec.title}" אי אפשר עדיין להריץ מכאן: הוא לא מעבד רק את מה שהשתנה '
                f"ועלול לדרוס עריכות ידניות. הריצו אותו ידנית{hint}.",
            )
        with self._lock:
            if self._closed:
                raise JobRefused(503, "השרת נסגר; אי אפשר להתחיל הרצה חדשה.")
            running = self._running_for(spec.produces)
            if running is not None:
                raise JobRefused(
                    409, f'"{running.step.spec.title}" כבר רץ ומעדכן את אותו הקובץ.'
                )
            job = Job(step, rebuild, self._spawn(step, rebuild), self._settings.log_lines)
            self._jobs[job.id] = job
            summary = job.summary()
        logger.info(
            f"Job {job.id}: started {step.key} (pid {job.process.pid}): "
            f"{' '.join(step.command(rebuild=rebuild))}"
        )
        self._bus.publish(JOB_STARTED, key=spec.produces, payload=summary)
        threading.Thread(target=self._read_output, args=(job,), daemon=True).start()
        job.supervisor = threading.Thread(target=self._supervise, args=(job,), daemon=True)
        job.supervisor.start()
        return summary

    def jobs(self) -> list[dict[str, Any]]:
        """Every kept job, newest first."""
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda job: job.started_at, reverse=True)
            return [job.summary() for job in jobs]

    def job(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            return self._get(job_id).detail()

    def cancel(self, job_id: str) -> dict[str, Any]:
        """Ask a running job to stop (SIGINT now, SIGKILL after the stop timeout)."""
        with self._lock:
            job = self._get(job_id)
            if job.state != RUNNING:
                raise JobRefused(409, f'ההרצה של "{job.step.spec.title}" כבר הסתיימה.')
            job.cancel_requested = True
            self._signal(job, signal.SIGINT)
            summary = job.summary()
        logger.info(f"Job {job.id}: cancel requested")
        timer = threading.Timer(self._settings.stop_timeout_seconds, self._kill, args=(job,))
        timer.daemon = True
        timer.start()
        return summary

    def running_jobs(self) -> list[Job]:
        with self._lock:
            return [job for job in self._jobs.values() if job.state == RUNNING]

    def shutdown(self) -> None:
        """Stop every running job (server shutdown) and wait until they are gone."""
        with self._lock:
            self._closed = True
            running = [job for job in self._jobs.values() if job.state == RUNNING]
            for job in running:
                job.cancel_requested = True
                self._signal(job, signal.SIGINT)
        if running:
            logger.info(f"Stopping {len(running)} running job(s)")
        deadline = time.monotonic() + self._settings.stop_timeout_seconds
        for job in running:
            try:
                job.process.wait(timeout=max(0.0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                self._kill(job)
        for job in running:
            if job.supervisor is not None:
                job.supervisor.join(timeout=self._settings.stop_timeout_seconds)

    # ------------------------------------------------------------------ internals

    def _spawn(self, step: Step, rebuild: bool) -> subprocess.Popen:
        env = {**os.environ, "WIKI_DATA_DIR": str(self._data_dir), "PYTHONUNBUFFERED": "1"}
        try:
            return subprocess.Popen(
                step.command(rebuild=rebuild),
                cwd=self._cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                start_new_session=True,  # its own process group: a cancel reaches its children
            )
        except OSError as exc:
            raise JobRefused(500, f"could not start {step.key}: {exc!r}") from None

    def _get(self, job_id: str) -> Job:
        try:
            return self._jobs[job_id]
        except KeyError:
            raise JobRefused(404, f"no job {job_id!r} (jobs are kept only while the server runs)") from None

    def _running_for(self, artifact_key: str) -> Job | None:
        return next(
            (
                job
                for job in self._jobs.values()
                if job.state == RUNNING and job.step.spec.produces == artifact_key
            ),
            None,
        )

    @staticmethod
    def _signal(job: Job, signum: int) -> None:
        """Signal the job's process group, only while the child has not been reaped."""
        if job.process.poll() is None:
            try:
                os.killpg(job.process.pid, signum)
            except ProcessLookupError:  # exited between the check and the signal
                pass

    def _kill(self, job: Job) -> None:
        with self._lock:
            if job.state == RUNNING:
                logger.warning(f"Job {job.id}: did not stop after SIGINT; sending SIGKILL")
                self._signal(job, signal.SIGKILL)

    def _read_output(self, job: Job) -> None:
        try:
            for raw in job.process.stdout:
                line = raw.rstrip("\n")
                with self._lock:
                    job.tail.append(line)
                    job.unpublished.append(line)
                    job.line_count += 1
                    if self._is_highlight(line):
                        job.highlights.append(line)
        finally:
            job.process.stdout.close()
            job.output_done.set()

    def _is_highlight(self, line: str) -> bool:
        """A line the user must not miss (e.g. manual edits superseded by a reconcile)."""
        return any(pattern in line for pattern in self._settings.highlight_patterns)

    def _supervise(self, job: Job) -> None:
        while not job.output_done.wait(self._settings.progress_interval_seconds):
            self._publish_progress(job)
        exit_code = job.process.wait()
        self._publish_progress(job)
        with self._lock:
            job.exit_code = exit_code
            job.ended_at = time.time()
            if exit_code == 0:
                job.state = SUCCEEDED
            else:
                job.state = CANCELLED if job.cancel_requested else FAILED
            summary = job.summary()
            self._prune()
        logger.log(
            logging.INFO if job.state == SUCCEEDED else logging.WARNING,
            f"Job {job.id}: {job.step.key} {job.state} (exit code {exit_code})",
        )
        self._bus.publish(JOB_FINISHED, key=job.step.spec.produces, payload=summary)

    def _publish_progress(self, job: Job) -> None:
        with self._lock:
            if not job.unpublished:
                return
            lines, job.unpublished = job.unpublished, []
            payload = {
                "id": job.id,
                "from": job.line_count - len(lines),
                "lines": lines,
                "highlights": [line for line in lines if self._is_highlight(line)],
            }
        self._bus.publish(JOB_PROGRESS, key=job.step.spec.produces, payload=payload)

    def _prune(self) -> None:
        """Keep at most ``history_limit`` finished jobs (the oldest go first)."""
        finished = sorted(
            (job for job in self._jobs.values() if job.state != RUNNING),
            key=lambda job: job.started_at,
        )
        for job in finished[: max(0, len(finished) - self._settings.history_limit)]:
            del self._jobs[job.id]
