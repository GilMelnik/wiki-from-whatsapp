"""Cross-process locks for artifact files and the locked, atomic save.

``artifact_lock`` wraps ``utils.file_lock.file_lock`` (the same sidecar ``<name>.lock`` a
pipeline step takes when it writes the file), adds re-entrancy within a thread and takes
several files in one fixed order. Rule: lock the files you write.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any

from review_tool.settings import SETTINGS
from utils.file_lock import file_lock
from utils.json_io import write_json_file


class ArtifactLockTimeout(TimeoutError):
    """Another process or thread kept an artifact file locked for the whole timeout."""


_held = threading.local()


def _held_depths() -> dict[Path, int]:
    """Lock depth per path for the current thread."""
    if not hasattr(_held, "depths"):
        _held.depths = {}
    return _held.depths


@contextmanager
def _locked(path: Path, timeout: float) -> Iterator[None]:
    depths = _held_depths()
    if depths.get(path, 0) > 0:  # this thread already holds it
        depths[path] += 1
        try:
            yield
        finally:
            depths[path] -= 1
        return
    with ExitStack() as stack:
        try:
            stack.enter_context(file_lock(path, timeout=timeout))
        except TimeoutError:
            raise ArtifactLockTimeout(
                f"{path.name} is being written by another process (a pipeline step or another "
                f"tool); gave up after {timeout}s. Try again in a moment."
            ) from None
        depths[path] = 1
        try:
            yield
        finally:
            depths[path] = 0


@contextmanager
def artifact_lock(*paths: Path, timeout: float | None = None) -> Iterator[None]:
    """Hold an exclusive lock on every given file while the block runs.

    Paths are locked in sorted order (no deadlock between two callers), nesting in the same
    thread is allowed, and ``ArtifactLockTimeout`` is raised when a file stays locked for
    ``timeout`` seconds (default: ``review.lock_timeout_seconds``).
    """
    wait = SETTINGS.lock_timeout_seconds if timeout is None else timeout
    with ExitStack() as stack:
        for path in sorted({path.resolve() for path in paths}):
            stack.enter_context(_locked(path, wait))
        yield


def save_json(path: Path, payload: dict[str, Any]) -> None:
    """Lock ``path`` and replace it atomically with ``payload`` as JSON."""
    with artifact_lock(path):
        write_json_file(payload, path)
