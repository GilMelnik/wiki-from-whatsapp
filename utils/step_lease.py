"""A cross-process lease that says "a pipeline step is running outside the review server".

The update driver (``step_10_update``) runs steps in its own process, so the review server's
job runner cannot see them. While the driver runs it holds an exclusive ``flock`` on one file
(``utils.paths.UPDATE_LEASE_PATH``) and writes into it which step it is running::

    {"pid": 123, "step": "step_3_extract", "produces": "claims", "title": "...", "since": "..."}

(``step`` / ``produces`` are ``null`` between two steps). The server reads it with
``current()``: its job runner refuses to start any step while the lease is held, and its write
guard answers 423 to a tool that writes the artifact the driver's step produces -- the same
rule it applies to its own jobs. The kernel releases the lock when the driver exits, even on a
crash, so a stale file never blocks anything.
"""

from __future__ import annotations

import fcntl
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Any


class LeaseHeld(RuntimeError):
    """Another process already holds the lease (another update is running)."""


class StepLease:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._file: IO[str] | None = None

    @contextmanager
    def hold(self) -> Iterator[StepLease]:
        """Take the lease for the block; ``LeaseHeld`` when another process has it."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lease_file = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(lease_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lease_file.close()
            holder = self.current() or {}
            raise LeaseHeld(
                f"another update is running (pid {holder.get('pid')}, step {holder.get('step')}); "
                "wait until it stops"
            ) from None
        self._file = lease_file
        try:
            self.announce(None, None, None)
            yield self
        finally:
            lease_file.truncate(0)
            fcntl.flock(lease_file.fileno(), fcntl.LOCK_UN)
            lease_file.close()
            self._file = None

    def announce(self, step: str | None, produces: str | None, title: str | None) -> None:
        """Say which step runs now (``None`` between steps); only inside ``hold()``."""
        if self._file is None:
            raise RuntimeError("StepLease.announce() outside hold()")
        record = {
            "pid": os.getpid(),
            "step": step,
            "produces": produces,
            "title": title,
            "since": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        self._file.seek(0)
        self._file.truncate(0)
        self._file.write(json.dumps(record))
        self._file.flush()

    @contextmanager
    def running(self, step: str, produces: str, title: str) -> Iterator[None]:
        """Announce ``step`` for the block, then announce "between steps" again."""
        self.announce(step, produces, title)
        try:
            yield
        finally:
            self.announce(None, None, None)

    def current(self) -> dict[str, Any] | None:
        """``None`` when nobody holds the lease, else what the holder wrote (``{}`` while it
        is still writing it)."""
        if not self.path.is_file():
            return None
        with self.path.open("r", encoding="utf-8") as lease_file:
            try:
                fcntl.flock(lease_file.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                text = lease_file.read()
                try:
                    record = json.loads(text) if text.strip() else {}
                except json.JSONDecodeError:
                    record = {}
                return record if isinstance(record, dict) else {}
            fcntl.flock(lease_file.fileno(), fcntl.LOCK_UN)
            return None
