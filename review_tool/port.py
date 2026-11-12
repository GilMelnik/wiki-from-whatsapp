"""Find the running review server, or bind a free port for a new one.

Nothing is ever killed (the old per-tool servers freed a port by stopping its process). A port that is taken is asked
``GET /api/health``; if it answers as this app for the same data directory it is reused,
otherwise the next port is tried.
"""

from __future__ import annotations

import errno
import http.client
import json
import logging
import socket
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from review_tool.guard import WILDCARD_HOSTS
from review_tool.settings import API_PREFIX, APP_ID

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PortDecision:
    """Where the app is served: on a socket we just bound, or by a server already running."""

    port: int
    listener: socket.socket | None

    @property
    def already_running(self) -> bool:
        return self.listener is None


class PortFinder:
    def __init__(
        self, host: str, data_dir: Path, *, health_timeout_seconds: float, search_limit: int
    ) -> None:
        self._host = host
        self._data_dir = data_dir
        self._health_timeout_seconds = health_timeout_seconds
        self._search_limit = search_limit
        # Local requests must not go through a system HTTP proxy.
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def find(self, preferred_port: int) -> PortDecision:
        """Return the first port from ``preferred_port`` up that is ours or free.

        A free port is returned already bound and listening, so no other process can take it
        between this check and the server start.
        """
        last_port = preferred_port + self._search_limit - 1
        for port in range(preferred_port, last_port + 1):
            listener = self._bind(port)
            if listener is not None:
                return PortDecision(port, listener)
            if self._is_our_server(port):
                return PortDecision(port, None)
            logger.info(f"Port {port} is in use by something else; trying the next one")
        raise RuntimeError(
            f"no usable port between {preferred_port} and {last_port} on {self._host}; "
            "pass --port to choose another range"
        )

    def running_port(self, preferred_port: int) -> int | None:
        """The port of the review server already running for this data directory, else
        ``None``. Binds nothing (for a process that only wants to talk to the server)."""
        last_port = preferred_port + self._search_limit - 1
        return next(
            (port for port in range(preferred_port, last_port + 1) if self._is_our_server(port)),
            None,
        )

    def base_url(self, port: int) -> str:
        """URL a browser on this machine uses to reach the server on ``port``."""
        host = "127.0.0.1" if self._host in WILDCARD_HOSTS else self._host
        return f"http://[{host}]:{port}" if ":" in host else f"http://{host}:{port}"

    def fetch_json(self, port: int, api_path: str) -> Any | None:
        """``GET /api/<api_path>`` from the server on ``port``; ``None`` if it is not JSON."""
        url = f"{self.base_url(port)}{API_PREFIX}/{api_path}"
        try:
            with self._opener.open(url, timeout=self._health_timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except (OSError, ValueError, http.client.HTTPException):
            return None

    def _bind(self, port: int) -> socket.socket | None:
        family = socket.AF_INET6 if ":" in self._host else socket.AF_INET
        listener = socket.socket(family, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind((self._host, port))
            listener.listen()
        except OSError as exc:
            listener.close()
            if exc.errno == errno.EADDRINUSE:
                return None
            raise OSError(f"cannot listen on {self._host}:{port}: {exc}") from exc
        return listener

    def _is_our_server(self, port: int) -> bool:
        health = self.fetch_json(port, "health")
        if not isinstance(health, dict) or health.get("app") != APP_ID:
            return False
        if health.get("data_dir") != str(self._data_dir):
            logger.info(
                f"Port {port} is a review server for another data directory "
                f"({health.get('data_dir')})"
            )
            return False
        return True
