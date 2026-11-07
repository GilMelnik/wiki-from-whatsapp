from __future__ import annotations

import fcntl
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def file_lock(
    path: Path, *, timeout: float = 30.0, poll_interval: float = 0.05
) -> Iterator[None]:
    """Hold an exclusive cross-process lock for ``path`` while the block runs.

    Locks the sidecar ``<path.name>.lock`` (created next to ``path`` and left in place)
    with ``flock``, retrying every ``poll_interval`` seconds until ``timeout`` elapses,
    then raises ``TimeoutError``. Not re-entrant. ``write_json_file`` does not take this
    lock itself; wrap it: ``with file_lock(p): write_json_file(data, p)``.
    """
    lock_path = path.with_name(path.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    with lock_path.open("a") as lock_file:
        while True:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"could not lock {path} within {timeout}s") from None
                time.sleep(poll_interval)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
