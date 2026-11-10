"""Store handles: the only way a tool reaches its in-memory store.

A handle remembers the stamps of the files its store was loaded from. ``get()`` reloads the
store when any of them changed on disk (a pipeline step ran, another tool saved), so a view
is never served from stale memory. ``mutate()`` additionally holds the cross-process lock on
the files the store writes for the whole read-modify-write, so a change is never applied to
stale memory and never interleaves with another writer.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Generic, TypeVar

from review_tool.fs import FileStamp, stamp
from review_tool.locks import artifact_lock

logger = logging.getLogger(__name__)

T = TypeVar("T")


class StoreHandle(Generic[T]):
    """Lazily loaded store that follows its files.

    ``factory`` builds a fully loaded store from disk. ``watched_paths`` are the files it
    reads: a change to any of them makes the next access reload. ``lock_paths`` are the files
    it writes (default: all watched paths); ``mutate()`` locks them. The store must persist
    its changes before the ``mutate()`` block ends.
    """

    def __init__(
        self,
        factory: Callable[[], T],
        watched_paths: Iterable[Path],
        *,
        lock_paths: Iterable[Path] | None = None,
        lock_timeout: float | None = None,
    ) -> None:
        self._factory = factory
        self._watched_paths = tuple(watched_paths)
        self._lock_paths = self._watched_paths if lock_paths is None else tuple(lock_paths)
        self._lock_timeout = lock_timeout
        self._mutex = threading.RLock()
        self._store: T | None = None
        self._loaded = False
        self._stamps: tuple[FileStamp | None, ...] = ()
        self._mutating = False

    def get(self) -> T:
        """Return the store for reading, reloaded first if its files changed on disk."""
        with self._mutex:
            return self._store if self._mutating else self._fresh()

    @contextmanager
    def mutate(self) -> Iterator[T]:
        """Yield the store for a change, under the file locks and guaranteed fresh.

        Raises ``ArtifactLockTimeout`` when the files stay locked by another process. If the
        block raises, the in-memory store is discarded and reloaded from disk on next access.
        """
        with self._mutex:
            if self._mutating:  # nested in the same thread: already locked and fresh
                yield self._store
                return
            with artifact_lock(*self._lock_paths, timeout=self._lock_timeout):
                store = self._fresh()
                self._mutating = True
                try:
                    yield store
                except BaseException:
                    self._store = None
                    self._loaded = False
                    raise
                else:
                    self._stamps = self._current_stamps()  # our own writes are not a change
                finally:
                    self._mutating = False

    def _fresh(self) -> T:
        # Stamps are taken before loading: a write that lands during the load causes one more
        # reload instead of being missed.
        stamps = self._current_stamps()
        if not self._loaded or stamps != self._stamps:
            if self._loaded:
                logger.info(f"Files changed on disk; reloading {self._describe()}")
            self._loaded = False
            self._store = self._factory()
            self._stamps = stamps
            self._loaded = True
        return self._store

    def _current_stamps(self) -> tuple[FileStamp | None, ...]:
        return tuple(stamp(path) for path in self._watched_paths)

    def _describe(self) -> str:
        return ", ".join(path.name for path in self._watched_paths)
