"""Poll the artifact files and announce every change, whoever made it.

The watcher is the single source of ``artifact_changed``: a tool saving a file and a pipeline
step run in a terminal look the same to it. Only the artifact files themselves are polled, so
the lock sidecars (``X.json.lock``) and the temp files of an atomic write (``.X.json.tmp-*``)
never produce an event.

Changes are debounced per artifact, not per file: an artifact is announced once all of its
files (original and edited) have stopped changing for the debounce period. A step that writes
both files under one lock (``utils.reconcile.write_reconciled``), an atomic replace, or a file
written in several chunks each produce one event.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

from review_tool.artifacts import ArtifactGraph, ArtifactNode
from review_tool.events import ARTIFACT_CHANGED, EventBus
from review_tool.fs import FileStamp, stamp

logger = logging.getLogger(__name__)

# The stamps of one artifact's files, by role ("original", "edited").
Stamps = dict[str, FileStamp | None]


@dataclass(frozen=True)
class _Pending:
    stamps: Stamps
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
        self._announced: dict[str, Stamps] = {}
        self._pending: dict[str, _Pending] = {}
        self.poll_once(time.monotonic())  # baseline: the current state is not a change

    def poll_once(self, now: float) -> list[dict[str, Any]]:
        """Compare every artifact with its last announced state at monotonic time ``now``.

        Returns the events published by this call: one ``artifact_changed`` per settled
        artifact, with ``payload.files`` naming the roles (``original`` / ``edited``) that
        differ from what was announced before.
        """
        events = []
        for node in self._graph.nodes:
            roles = self._settled_roles(node, now)
            if roles:
                payload = {"files": roles}
                events.append(self._bus.publish(ARTIFACT_CHANGED, key=node.key, payload=payload))
        return events

    def _settled_roles(self, node: ArtifactNode, now: float) -> list[str]:
        current = {role: stamp(path) for role, path in node.files.items()}
        announced = self._announced.get(node.key)
        if announced is None:  # first sight of this node (also one added later): baseline
            self._announced[node.key] = current
            return []
        if current == announced:
            self._pending.pop(node.key, None)
            return []
        pending = self._pending.get(node.key)
        if pending is None or pending.stamps != current:
            self._pending[node.key] = _Pending(current, now)
            return []
        if now - pending.since < self._debounce_seconds:
            return []
        self._announced[node.key] = current
        del self._pending[node.key]
        return [role for role in current if current[role] != announced.get(role)]

    async def run(self) -> None:
        """Poll until cancelled."""
        logger.info(f"Watching {len(self._announced)} artifacts every {self._interval_seconds}s")
        while True:
            await asyncio.sleep(self._interval_seconds)
            try:
                events = self.poll_once(time.monotonic())
            except OSError as exc:  # e.g. a permission problem; keep watching
                logger.error(f"Could not check the artifact files: {exc!r}")
                continue
            for event in events:
                logger.info(f"artifact_changed: {event['key']} {event['payload']['files']}")
