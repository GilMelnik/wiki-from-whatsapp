"""The FastAPI application: common endpoints, every tool's router, the built web app.

Routes:
- ``GET /api/health``     identity of this server (``app``, ``data_dir``, ``pid``, ``started_at``)
- ``GET /api/tools``      ``[{name, title, reads, writes, missing}]``
- ``GET /api/artifacts``  the artifact status list (``review_tool.artifacts.ArtifactStatus``)
- ``GET /api/events``     Server-Sent Events (``review_tool.events``)
- ``/api/steps``, ``/api/jobs``  the step runner (``review_tool.jobs_api``)
- ``/api/<tool>/...``     each registered tool's router
- everything else         the built web app (``review_tool/web/dist``) with SPA fallback

Errors are always ``{"detail": ...}``. A tool endpoint may let ``FileNotFoundError`` (its
artifact is not there yet) and ``ArtifactLockTimeout`` escape; they become 409 and 423.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from collections.abc import AsyncIterator
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse

from review_tool.artifacts import ArtifactGraph
from review_tool.events import EventBus, sse_response
from review_tool.fs import iso_utc
from review_tool.guard import OriginGuard, allowed_hosts_for
from review_tool.jobs import JobRunner
from review_tool.jobs_api import JobsApi, WriteGuard
from review_tool.locks import ArtifactLockTimeout
from review_tool.registry import ToolRegistry
from review_tool.settings import API_PREFIX, APP_ID, ReviewSettings
from review_tool.watcher import ArtifactWatcher

logger = logging.getLogger(__name__)

class ReviewApp:
    """Builds the application for one registry, artifact graph and event bus."""

    def __init__(
        self,
        *,
        registry: ToolRegistry,
        graph: ArtifactGraph,
        bus: EventBus,
        jobs: JobRunner,
        settings: ReviewSettings,
        data_dir: Path,
        bind_host: str,
    ) -> None:
        self._registry = registry
        self._graph = graph
        self._bus = bus
        self._jobs = jobs
        self._settings = settings
        self._data_dir = data_dir
        self._bind_host = bind_host
        self._started_at = iso_utc(time.time())

    def build(self) -> FastAPI:
        app = FastAPI(
            title="review_tool",
            lifespan=self._lifespan,
            docs_url=None,
            redoc_url=None,
            openapi_url=f"{API_PREFIX}/openapi.json",
        )
        # Added first, so it runs inside OriginGuard (a cross-site request is refused first).
        app.add_middleware(WriteGuard, registry=self._registry, graph=self._graph, runner=self._jobs)
        app.add_middleware(
            OriginGuard,
            allowed_hosts=allowed_hosts_for(self._bind_host),
            allowed_origins=frozenset(self._settings.dev_origins),
        )
        app.add_exception_handler(FileNotFoundError, self._missing_file)
        app.add_exception_handler(ArtifactLockTimeout, self._locked)
        app.add_api_route(f"{API_PREFIX}/health", self.health, methods=["GET"])
        app.add_api_route(f"{API_PREFIX}/tools", self.tools, methods=["GET"])
        app.add_api_route(f"{API_PREFIX}/artifacts", self.artifacts, methods=["GET"])
        app.add_api_route(f"{API_PREFIX}/events", self.events, methods=["GET"])
        app.include_router(JobsApi(self._jobs).router, prefix=API_PREFIX, tags=["jobs"])
        for spec in self._registry.specs:
            for key in (*spec.reads, *spec.writes):
                try:
                    self._graph.node(key)
                except KeyError as exc:
                    raise ValueError(f"tool {spec.name!r}: {exc.args[0]}") from None
            app.include_router(spec.router, prefix=f"{API_PREFIX}/{spec.name}", tags=[spec.name])
        app.add_api_route("/{path:path}", self.web_app, methods=["GET"], include_in_schema=False)
        return app

    @contextlib.asynccontextmanager
    async def _lifespan(self, app: FastAPI) -> AsyncIterator[None]:
        watcher = ArtifactWatcher(
            self._graph,
            self._bus,
            interval_seconds=self._settings.watch_interval_seconds,
            debounce_seconds=self._settings.watch_debounce_seconds,
        )
        task = asyncio.create_task(watcher.run(), name="artifact-watcher")
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            await asyncio.to_thread(self._jobs.shutdown)  # stop the steps this server started

    def health(self) -> dict[str, Any]:
        return {
            "app": APP_ID,
            "data_dir": str(self._data_dir),
            "pid": os.getpid(),
            "started_at": self._started_at,
        }

    def tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": spec.name,
                "title": spec.title,
                "reads": spec.reads,
                "writes": spec.writes,
                "missing": self._graph.missing(spec.reads),
            }
            for spec in self._registry.specs
        ]

    def artifacts(self) -> list[dict[str, Any]]:
        return [asdict(status) for status in self._graph.status()]

    async def events(self) -> StreamingResponse:
        return sse_response(self._bus)

    def web_app(self, path: str) -> Response:
        """Serve a built file, or ``index.html`` for any app URL (SPA fallback)."""
        url_path = f"/{path}"
        if url_path == API_PREFIX or url_path.startswith(f"{API_PREFIX}/"):
            raise HTTPException(status_code=404, detail=f"no such API endpoint: {url_path}")
        dist = self._settings.web_dist.resolve()
        index = dist / "index.html"
        if not index.is_file():
            return HTMLResponse(self._not_built_page(), status_code=503)
        requested = (dist / path).resolve()
        if path and requested.is_file() and requested.is_relative_to(dist):
            return FileResponse(requested)
        if Path(path).suffix:  # a missing asset must not come back as HTML
            raise HTTPException(status_code=404, detail=f"no such file: {url_path}")
        return FileResponse(index, headers={"Cache-Control": "no-cache"})

    def _not_built_page(self) -> str:
        return (
            "<!doctype html><meta charset='utf-8'><title>review_tool</title>"
            "<body style='font-family: sans-serif; margin: 3rem'>"
            "<h1>The web app is not built yet</h1>"
            f"<p>Run <code>cd {self._settings.web_folder} &amp;&amp; npm install &amp;&amp; "
            "npm run build</code>, then reload this page. The API is already running under "
            "<code>/api</code>.</p>"
        )

    @staticmethod
    def _missing_file(request: Request, exc: FileNotFoundError) -> JSONResponse:
        logger.warning(f"{request.method} {request.url.path}: missing file: {exc}")
        return JSONResponse(
            {"detail": f"A file this needs is missing ({exc}). Run the step that makes it first."},
            status_code=409,
        )

    @staticmethod
    def _locked(request: Request, exc: ArtifactLockTimeout) -> JSONResponse:
        logger.warning(f"{request.method} {request.url.path}: {exc}")
        return JSONResponse({"detail": str(exc)}, status_code=423)
