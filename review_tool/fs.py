"""File stamps: the one way the review tool decides that a file changed on disk."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class FileStamp:
    """Identity of a file's content at a point in time.

    The inode is part of it because ``utils.json_io.write_json_file`` replaces the file
    (new inode), so a rewrite is seen even when size and timestamp happen to be equal.
    """

    mtime_ns: int
    size: int
    inode: int

    @property
    def iso(self) -> str:
        return iso_utc(self.mtime_ns / 1_000_000_000)


def stamp(path: Path) -> FileStamp | None:
    """Return the stamp of ``path``, or ``None`` when it does not exist."""
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return FileStamp(stat.st_mtime_ns, stat.st_size, stat.st_ino)


def iso_utc(epoch_seconds: float) -> str:
    """Format a POSIX timestamp the way every API payload does: ISO-8601 UTC, milliseconds."""
    moment = datetime.fromtimestamp(epoch_seconds, tz=timezone.utc)
    return moment.isoformat(timespec="milliseconds")
