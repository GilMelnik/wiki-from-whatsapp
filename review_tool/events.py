"""In-process pub/sub and the Server-Sent Events stream behind ``GET /api/events``.

Event JSON: ``{type, at, key?, tool?, payload?}``. Types today: ``hello`` (first frame of every
connection, and sent again when a slow client lost events and must refetch) and
``artifact_changed`` (published only by ``review_tool.watcher``). Reserved for the step
runner: ``job_started``, ``job_progress``, ``job_finished``.

``publish`` is safe to call from any thread, so a tool's sync endpoint or a job thread can
announce its own event types.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from fastapi.responses import StreamingResponse

from review_tool.fs import iso_utc
from review_tool.settings import SETTINGS

logger = logging.getLogger(__name__)

HELLO = "hello"
ARTIFACT_CHANGED = "artifact_changed"
KEEPALIVE_FRAME = ": keep-alive\n\n"


@dataclass(frozen=True, eq=False)
class _Subscriber:
    loop: asyncio.AbstractEventLoop
    queue: asyncio.Queue[dict[str, Any] | None]


class EventBus:
    """Fan events out to every open SSE connection."""

    def __init__(self, *, keepalive_seconds: float, queue_size: int) -> None:
        self._keepalive_seconds = keepalive_seconds
        self._queue_size = queue_size
        self._subscribers: set[_Subscriber] = set()
        self._lock = threading.Lock()
        self._closed = False

    def publish(
        self,
        event_type: str,
        *,
        key: str | None = None,
        tool: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send an event to every subscriber and return it. Callable from any thread."""
        event = self._event(event_type, key=key, tool=tool, payload=payload)
        with self._lock:
            subscribers = tuple(self._subscribers)
        for subscriber in subscribers:
            try:
                subscriber.loop.call_soon_threadsafe(self._deliver, subscriber, event)
            except RuntimeError:  # the subscriber's event loop is gone
                self._drop(subscriber)
        return event

    async def stream(self) -> AsyncIterator[str]:
        """Yield SSE frames for one connection: ``hello``, then events and keep-alives."""
        subscriber = _Subscriber(
            asyncio.get_running_loop(), asyncio.Queue(maxsize=self._queue_size)
        )
        with self._lock:
            if self._closed:
                return
            self._subscribers.add(subscriber)
        try:
            yield self._frame(self._event(HELLO))
            while True:
                try:
                    event = await asyncio.wait_for(
                        subscriber.queue.get(), timeout=self._keepalive_seconds
                    )
                except asyncio.TimeoutError:
                    yield KEEPALIVE_FRAME
                    continue
                if event is None:
                    return
                yield self._frame(event)
        finally:
            self._drop(subscriber)

    def close(self) -> None:
        """End every open stream and refuse new ones (server shutdown)."""
        with self._lock:
            self._closed = True
            subscribers = tuple(self._subscribers)
        for subscriber in subscribers:
            try:
                subscriber.loop.call_soon_threadsafe(self._deliver, subscriber, None)
            except RuntimeError:
                self._drop(subscriber)

    def _deliver(self, subscriber: _Subscriber, event: dict[str, Any] | None) -> None:
        """Runs on the subscriber's loop. ``None`` ends the stream.

        A full queue (a client that stopped reading) is replaced by a single ``hello``, which
        tells the client it missed events and must refetch.
        """
        queue = subscriber.queue
        if event is not None and queue.full():
            logger.warning("SSE client is too slow; dropping its backlog and asking it to refetch")
            event = self._event(HELLO)
        if event is None or event["type"] == HELLO:
            while not queue.empty():
                queue.get_nowait()
        queue.put_nowait(event)

    def _drop(self, subscriber: _Subscriber) -> None:
        with self._lock:
            self._subscribers.discard(subscriber)

    @staticmethod
    def _event(
        event_type: str,
        *,
        key: str | None = None,
        tool: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        event: dict[str, Any] = {"type": event_type, "at": iso_utc(time.time())}
        if key is not None:
            event["key"] = key
        if tool is not None:
            event["tool"] = tool
        if payload is not None:
            event["payload"] = payload
        return event

    @staticmethod
    def _frame(event: dict[str, Any]) -> str:
        return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def sse_response(bus: EventBus) -> StreamingResponse:
    """The streaming response for ``GET /api/events``."""
    return StreamingResponse(
        bus.stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


BUS = EventBus(
    keepalive_seconds=SETTINGS.sse_keepalive_seconds, queue_size=SETTINGS.sse_queue_size
)
publish = BUS.publish
