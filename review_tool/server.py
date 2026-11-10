"""The uvicorn server that runs the review application."""

from __future__ import annotations

import socket

import uvicorn
from fastapi import FastAPI

from review_tool.events import EventBus


class ReviewServer(uvicorn.Server):
    """uvicorn server that ends the open event streams before it shuts down.

    uvicorn waits for open connections when it stops, and an SSE connection never finishes
    by itself, so without this Ctrl+C would hang for as long as a browser tab is open.
    Run it on an already bound socket: ``ReviewServer(...).run(sockets=[listener])``.
    """

    def __init__(self, app: FastAPI, bus: EventBus, *, shutdown_timeout_seconds: int) -> None:
        # log_config=None: uvicorn's loggers propagate to the handlers of utils.logging_setup.
        super().__init__(
            uvicorn.Config(
                app, log_config=None, timeout_graceful_shutdown=shutdown_timeout_seconds
            )
        )
        self._bus = bus

    async def shutdown(self, sockets: list[socket.socket] | None = None) -> None:
        self._bus.close()
        await super().shutdown(sockets=sockets)
