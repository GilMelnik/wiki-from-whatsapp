"""Poll the artifact files and announce every change, whoever made it.

The watcher is the single source of ``artifact_changed``: a tool saving a file and a pipeline
step run in a terminal look the same to it. A change is announced once the file has stopped
changing for the debounce period, so a file that is written in several steps (a copy, a
non-atomic write) produces one event.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from review_tool.artifacts import ArtifactGraph
from review_tool.events import ARTIFACT_CHANGED, EventBus
from review_tool.fs import FileStamp, stamp

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Pending:
    stamp: FileStamp | None
    since: float


class ArtifactWatcher:
    def __init__(
        self,
        graph: ArtifactGraph,
        bus: EventBus,
        *,
        interval_seconds: float,
        debounce_seconds: float,
    ) -> None:
        self._graph = graph
        self._bus = bus
        self._interval_seconds = interval_seconds
        self._debounce_seconds = debounce_seconds
        self._announced: dict[Path, FileStamp | None] = {}
        self._pending: dict[Path, _Pending] = {}
        self.poll_once(time.monotonic())  # baseline: the current state is not a change

    def poll_once(self, now: float) -> list[dict[str, Any]]:
        """Compare every artifact file with its last announced state at monotonic time ``now``.

        Returns the events published by this call: one ``artifact_changed`` per artifact key,
        with ``payload.files`` naming the roles (``original`` / ``edited``) that settled.
        """
        settled: dict[str, list[str]] = {}
        for node in self._graph.nodes:
            for role, path in node.files.items():
                if self._settled(path, now):
                    settled.setdefault(node.key, []).append(role)
        return [
            self._bus.publish(ARTIFACT_CHANGED, key=key, payload={"files": roles})
            for key, roles in settled.items()
        ]

    def _settled(self, path: Path, now: float) -> bool:
        current = stamp(path)
        if path not in self._announced:  # first sight of this file: baseline, no event
            self._announced[path] = current
            return False
        if current == self._announced[path]:
            self._pending.pop(path, None)
            return False
        pending = self._pending.get(path)
        if pending is None or pending.stamp != current:
            self._pending[path] = _Pending(current, now)
            return False
        if now - pending.since < self._debounce_seconds:
            return False
        self._announced[path] = current
        del self._pending[path]
        return True

    async def run(self) -> None:
        """Poll until cancelled."""
        logger.info(
            f"Watching {len(self._announced)} artifact files every {self._interval_seconds}s"
        )
        while True:
            await asyncio.sleep(self._interval_seconds)
            try:
                events = self.poll_once(time.monotonic())
            except OSError as exc:  # e.g. a permission problem; keep watching the rest
                logger.error(f"Could not check the artifact files: {exc!r}")
                continue
            for event in events:
                logger.info(f"artifact_changed: {event['key']} {event['payload']['files']}")
