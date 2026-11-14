"""Run the review site: ``python -m review_tool [--tool NAME] [--host H] [--port N] [--no-browser]``.

One server, one site with a page per tool (``/NAME``; ``/`` opens the first, the pipeline
page). ``--tool`` picks the page the browser opens. If the server is already running for this
data directory, this command only opens that page and exits; that is how several tools run at
once (one server, many tabs). A port taken by another program is skipped, never freed by
force.
"""

from __future__ import annotations

import argparse
import logging
import sys
import webbrowser
from collections.abc import Sequence

from review_tool.app import ReviewApp
from review_tool.artifacts import GRAPH
from review_tool.events import BUS
from review_tool.guard import WILDCARD_HOSTS
from review_tool.jobs import JOB_SETTINGS, JobRunner
from review_tool.port import PortFinder
from review_tool.registry import ToolRegistry
from review_tool.server import ReviewServer
from review_tool.settings import SETTINGS
from review_tool.steps import SERVED_STEPS
from review_tool.toolset import TOOL_SPECS
from utils.config import DATA_DIR, PROJECT_ROOT
from utils.logging_setup import setup_step_logging


class Launcher:
    """One invocation of the command line: reuse the running server or start one."""

    def __init__(
        self, args: argparse.Namespace, registry: ToolRegistry, logger: logging.Logger
    ) -> None:
        self._args = args
        self._registry = registry
        self._logger = logger
        self._finder = PortFinder(
            args.host,
            DATA_DIR,
            health_timeout_seconds=SETTINGS.health_timeout_seconds,
            search_limit=SETTINGS.port_search_limit,
        )

    def run(self) -> int:
        decision = self._finder.find(self._args.port)
        url = f"{self._finder.base_url(decision.port)}/{self._args.tool or ''}"
        if decision.already_running:
            return self._reuse(decision.port, url)
        self._warn_about_setup()
        app = ReviewApp(
            registry=self._registry,
            graph=GRAPH,
            bus=BUS,
            jobs=JobRunner(
                SERVED_STEPS, BUS, cwd=PROJECT_ROOT, data_dir=DATA_DIR, settings=JOB_SETTINGS
            ),
            settings=SETTINGS,
            data_dir=DATA_DIR,
            bind_host=self._args.host,
        ).build()
        server = ReviewServer(app, BUS, shutdown_timeout_seconds=SETTINGS.shutdown_timeout_seconds)
        self._logger.info(f"Review tools on {url} (data: {DATA_DIR}); Ctrl+C to stop")
        self._open(url)  # the socket already listens, so the browser cannot be too early
        server.run(sockets=[decision.listener])
        return 0

    def _reuse(self, port: int, url: str) -> int:
        health = self._finder.fetch_json(port, "health") or {}
        running = f"the server already running on port {port} (pid {health.get('pid')})"
        served = {tool.get("name") for tool in self._finder.fetch_json(port, "tools") or []}
        if self._args.tool and self._args.tool not in served:
            self._logger.error(
                f"{running} was started from older code and does not have the tool "
                f"{self._args.tool!r}. Stop it with Ctrl+C in its terminal and run this again."
            )
            return 1
        self._logger.info(f"Using {running}: {url}")
        self._open(url)
        return 0

    def _open(self, url: str) -> None:
        if not self._args.no_browser:
            webbrowser.open(url)

    def _warn_about_setup(self) -> None:
        if not (SETTINGS.web_dist / "index.html").is_file():
            self._logger.warning(
                f"The web app is not built ({SETTINGS.web_dist} has no index.html): "
                f"run `npm install && npm run build` in {SETTINGS.web_folder}"
            )
        if self._args.host in WILDCARD_HOSTS:
            self._logger.warning(
                f"Listening on {self._args.host}: the tools and the private data they show "
                "are reachable from the network without a login"
            )
        for spec in self._registry.specs:
            if spec.name == self._args.tool and (missing := GRAPH.missing(spec.reads)):
                self._logger.warning(
                    f"Tool {spec.name!r} needs files that do not exist yet: {', '.join(missing)}"
                )


def main(argv: Sequence[str] | None = None) -> int:
    registry = ToolRegistry(TOOL_SPECS)
    parser = argparse.ArgumentParser(
        prog="python -m review_tool",
        description="The review site: one server, one page per review tool",
    )
    parser.add_argument("--tool", choices=registry.names, help="the tool page to open in the browser")
    parser.add_argument("--host", default=SETTINGS.host)
    parser.add_argument("--port", type=int, default=SETTINGS.port)
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser tab")
    args = parser.parse_args(argv)
    logger = setup_step_logging(SETTINGS.log_folder)
    return Launcher(args, registry, logger).run()


if __name__ == "__main__":
    sys.exit(main())
