"""One report file and one log file per update (``<stamp>_report.json``, ``<stamp>_update.log``).

The report holds what is needed to trace the update: the config, the git commit, the export's
fingerprint, the counts of new / changed / missing messages, and one event per thing that
happened (a step's run with its result, a stop, a failure, an estimate). It is rewritten after
every event, so a crash leaves everything up to the crash.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from step_10_update.state import now
from utils.config import LOGGING_CONFIG, ROOT_CONFIG
from utils.embedding_ab import _git_commit
from utils.json_io import write_json_file
from utils.logging_setup import LOG_FORMAT


def json_safe(value: Any) -> Any:
    """``value`` as plain JSON (paths, sets and other objects become strings)."""
    return json.loads(json.dumps(value, default=str, ensure_ascii=False))


def scalar_summary(result: Mapping[str, Any]) -> str:
    """A one-line view of a step's result: scalars as they are, collections by their size."""
    parts = []
    for key, value in result.items():
        if isinstance(value, (list, tuple, dict, set)):
            parts.append(f"{key}=[{len(value)}]")
        else:
            parts.append(f"{key}={value}")
    return ", ".join(parts)


class UpdateReport:
    def __init__(self, path: Path) -> None:
        self.path = path

    def create(self, header: Mapping[str, Any]) -> None:
        """Start the report of a new update (config and commit taken now)."""
        write_json_file(
            json_safe({**header, "git_commit": _git_commit(), "config": ROOT_CONFIG,
                       "events": []}),
            self.path,
        )

    def add(self, kind: str, step: str | None, **fields: Any) -> None:
        report = json.loads(self.path.read_text(encoding="utf-8"))
        report["events"].append(json_safe({"at": now(), "kind": kind, "step": step, **fields}))
        report["git_commit_last"] = _git_commit()
        write_json_file(report, self.path)


@contextmanager
def update_log(path: Path) -> Iterator[None]:
    """Also log everything (the driver and the steps it runs) to ``path`` for the block."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        path,
        maxBytes=int(LOGGING_CONFIG["max_bytes"]),
        backupCount=int(LOGGING_CONFIG["backup_count"]),
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        yield
    finally:
        root.removeHandler(handler)
        handler.close()
