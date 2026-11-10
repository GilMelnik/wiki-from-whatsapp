"""Typed view of the ``review`` section of ``config.json``."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from utils.config import PROJECT_ROOT, section

# What GET /api/health answers in "app"; a second launch recognises a running server by it.
APP_ID = "review_tool"
# Every JSON endpoint lives under this prefix; all other paths serve the web app.
API_PREFIX = "/api"


@dataclass(frozen=True)
class ReviewSettings:
    host: str
    port: int
    port_search_limit: int
    health_timeout_seconds: float
    watch_interval_seconds: float
    watch_debounce_seconds: float
    lock_timeout_seconds: float
    sse_keepalive_seconds: float
    sse_queue_size: int
    shutdown_timeout_seconds: int
    dev_origins: tuple[str, ...]
    web_dist: Path
    log_folder: str

    @classmethod
    def from_config(cls, raw: dict[str, Any]) -> ReviewSettings:
        """Build the settings from the raw ``review`` section; name any missing key."""
        try:
            return cls(
                host=str(raw["host"]),
                port=int(raw["port"]),
                port_search_limit=int(raw["port_search_limit"]),
                health_timeout_seconds=float(raw["health_timeout_seconds"]),
                watch_interval_seconds=float(raw["watch_interval_seconds"]),
                watch_debounce_seconds=float(raw["watch_debounce_seconds"]),
                lock_timeout_seconds=float(raw["lock_timeout_seconds"]),
                sse_keepalive_seconds=float(raw["sse_keepalive_seconds"]),
                sse_queue_size=int(raw["sse_queue_size"]),
                shutdown_timeout_seconds=int(raw["shutdown_timeout_seconds"]),
                dev_origins=tuple(str(origin) for origin in raw["dev_origins"]),
                web_dist=PROJECT_ROOT / str(raw["web_dist"]),
                log_folder=str(raw["log_folder"]),
            )
        except KeyError as exc:
            raise KeyError(
                f"config.json section 'review' is missing the key {exc.args[0]!r}"
            ) from None


SETTINGS = ReviewSettings.from_config(section("review"))
