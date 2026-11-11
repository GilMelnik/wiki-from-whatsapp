"""HTTP side of the step runner: the step and job endpoints and the write guard.

Routes (mounted under ``/api`` by ``review_tool.app``):

- ``GET  /steps``             the step registry: ``[{key, title, produces, incremental_safe,
                              paid, command}]``
- ``GET  /jobs``              every kept job, newest first (``Job.summary``)
- ``POST /jobs``              ``{step, rebuild: false}`` -> 202 with the new job's summary;
                              404 unknown step, 409 not incremental-safe or already running
- ``GET  /jobs/{id}``         summary plus ``log`` (the kept tail) and ``log_start``
- ``POST /jobs/{id}/cancel``  stop a running job; 409 when it already finished

``WriteGuard``: while a job runs, every non-GET request to a tool that writes the artifact
the job produces is answered 423 with a readable ``detail``, so a tool never races the step.
Tools need no code for this; their ``ToolSpec.writes`` decides.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from review_tool.artifacts import ArtifactGraph
from review_tool.jobs import JobRefused, JobRunner
from review_tool.registry import ToolRegistry
from review_tool.settings import API_PREFIX

READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class JobRequest(BaseModel):
    step: str
    rebuild: bool = False


class JobsApi:
    """The ``/steps`` and ``/jobs`` routes of one runner."""

    def __init__(self, runner: JobRunner) -> None:
        self._runner = runner
        self.router = APIRouter()
        self.router.add_api_route("/steps", self.steps, methods=["GET"])
        self.router.add_api_route("/jobs", self.jobs, methods=["GET"])
        self.router.add_api_route("/jobs", self.start, methods=["POST"], status_code=202)
        self.router.add_api_route("/jobs/{job_id}", self.job, methods=["GET"])
        self.router.add_api_route("/jobs/{job_id}/cancel", self.cancel, methods=["POST"])

    def steps(self) -> list[dict[str, Any]]:
        return [step.to_json() for step in self._runner.steps.steps]

    def jobs(self) -> list[dict[str, Any]]:
        return self._runner.jobs()

    def start(self, request: JobRequest) -> dict[str, Any]:
        return self._answer(self._runner.run_step, request.step, rebuild=request.rebuild)

    def job(self, job_id: str) -> dict[str, Any]:
        return self._answer(self._runner.job, job_id)

    def cancel(self, job_id: str) -> dict[str, Any]:
        return self._answer(self._runner.cancel, job_id)

    @staticmethod
    def _answer(call: Callable[..., dict[str, Any]], *args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            return call(*args, **kwargs)
        except JobRefused as exc:
            raise HTTPException(status_code=exc.status, detail=exc.detail) from None


class WriteGuard:
    """ASGI middleware: 423 for a tool's mutations while a step rewrites what it writes."""

    def __init__(
        self, app: ASGIApp, *, registry: ToolRegistry, graph: ArtifactGraph, runner: JobRunner
    ) -> None:
        self._app = app
        self._writes = {spec.name: frozenset(spec.writes) for spec in registry.specs}
        self._graph = graph
        self._runner = runner

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] not in READ_METHODS:
            detail = self._busy_detail(scope["path"])
            if detail is not None:
                await JSONResponse({"detail": detail}, status_code=423)(scope, receive, send)
                return
        await self._app(scope, receive, send)

    def _busy_detail(self, path: str) -> str | None:
        if not path.startswith(f"{API_PREFIX}/"):
            return None
        tool = path[len(API_PREFIX) + 1 :].split("/", 1)[0]
        writes = self._writes.get(tool)
        if not writes:
            return None
        for job in self._runner.running_jobs():
            produced = job.step.spec.produces
            if produced in writes:
                return (
                    f"{job.step.spec.title} is running and rewriting "
                    f"“{self._graph.node(produced).title}”. Changes cannot be saved until it "
                    "finishes; try again after it."
                )
        return None
