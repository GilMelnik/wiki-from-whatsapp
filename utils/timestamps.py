"""The current time as the pipeline's files record it, in the three forms they use."""

from __future__ import annotations

from datetime import datetime, timezone


def local_now() -> str:
    """Local time to the second, ISO 8601 without an offset (``2026-11-13T15:02:43``)."""
    return datetime.now().isoformat(timespec="seconds")


def utc_now() -> str:
    """UTC to the second, ISO 8601 with its offset (``2026-11-13T13:02:43+00:00``)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def utc_now_z() -> str:
    """UTC to the second with a ``Z`` (``2026-11-13T13:02:43Z``), as the OKF front matter has it."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
