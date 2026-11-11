"""Is the review server (``python -m review_tool``) doing work that a step must not race?

The update driver asks before every step. With no server running for this data directory
there is nothing to ask. Otherwise a step is refused while the server's job runner runs a step
(``GET /api/jobs``) or the suggestions tool has applies queued or running
(``GET /api/suggestions/applies``: the job runner cannot see those). A server that does not
answer is treated as busy: the driver never guesses.
"""

from __future__ import annotations

from review_tool.port import PortFinder
from review_tool.settings import SETTINGS
from utils.config import DATA_DIR

SUGGESTIONS_TOOL = "suggestions"


class ReviewServerCheck:
    def __init__(self, finder: PortFinder | None = None, port: int = SETTINGS.port) -> None:
        self._finder = finder or PortFinder(
            SETTINGS.host,
            DATA_DIR,
            health_timeout_seconds=SETTINGS.health_timeout_seconds,
            search_limit=SETTINGS.port_search_limit,
        )
        self._port = port

    def __call__(self) -> str | None:
        """Why a step may not start now, or ``None`` when it may."""
        port = self._finder.running_port(self._port)
        if port is None:
            return None
        server = f"the review server on port {port}"
        jobs = self._finder.fetch_json(port, "jobs")
        if not isinstance(jobs, list):
            return f"{server} did not answer GET /api/jobs"
        running = [job.get("title") for job in jobs if job.get("state") == "running"]
        if running:
            return f"{server} is running {', '.join(map(str, running))}"
        tools = self._finder.fetch_json(port, "tools")
        if not isinstance(tools, list):
            return f"{server} did not answer GET /api/tools"
        if SUGGESTIONS_TOOL not in {tool.get("name") for tool in tools}:
            return None
        applies = self._finder.fetch_json(port, f"{SUGGESTIONS_TOOL}/applies")
        if not isinstance(applies, dict):
            return f"{server} did not answer GET /api/{SUGGESTIONS_TOOL}/applies"
        waiting = len(applies.get("queue") or []) + (1 if applies.get("running") else 0)
        if waiting:
            return f"the suggestions tool has {waiting} suggestion(s) being applied or waiting"
        return None
