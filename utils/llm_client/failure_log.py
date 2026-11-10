"""Append-only JSONL log of unsuccessful LLM / classifier calls.

Each line stores the full inputs and any raw response so a failed call can be
examined and re-run differently later. The files hold chat content, so they live
in the (private, git-ignored) data dir.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any


def append_failure(
    failure_log: Path, entry: dict[str, Any], logger: logging.Logger
) -> None:
    """Append ``entry`` (stamped with the current time) as one JSON line.

    A log that cannot be written must not mask the original failure, so an
    ``OSError`` is reported as a warning instead of raised.
    """

    line = json.dumps(
        {"time": time.strftime("%Y-%m-%dT%H:%M:%S"), **entry}, ensure_ascii=False
    )
    try:
        failure_log.parent.mkdir(parents=True, exist_ok=True)
        with failure_log.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError as exc:
        logger.warning(f"Could not write failure log {failure_log}: {exc}")
