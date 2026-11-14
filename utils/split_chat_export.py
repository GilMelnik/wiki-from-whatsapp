"""Split a full chat export at the configured cutoff date.

``data/chat_android.json`` (the pipeline input) keeps every message strictly
before ``input.cutoff_date``; the full export stays in ``data/updates/`` as the
input of the update step.

Usage: ``python -m utils.split_chat_export [--export PATH] [--force]``
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

from utils.export_messages import parse_android_datetime
from utils.json_io import write_json_file
from utils.logging_setup import setup_step_logging
from utils.paths import CHAT_ANDROID_PATH, INPUT_CONFIG, SHARED, UPDATES_DIR

logger = logging.getLogger(__name__)


def split_export(
    export_path: Path, cutoff: datetime, output_path: Path, force: bool = False
) -> tuple[int, int]:
    """Write the messages before ``cutoff`` to ``output_path``; return ``(kept, remaining)``.

    Order and the raw message dicts are preserved untouched. Raises
    ``FileExistsError`` when ``output_path`` exists and ``force`` is false.
    """
    if output_path.exists() and not force:
        raise FileExistsError(f"{output_path} already exists; pass force=True (--force) to overwrite")
    messages: list[dict[str, Any]] = json.loads(export_path.read_text(encoding="utf-8"))
    if not isinstance(messages, list):
        raise ValueError(f"{export_path} must contain a JSON list of messages, got {type(messages).__name__}")
    kept = [m for m in messages if parse_android_datetime(m["date"], m["time"]) < cutoff]
    write_json_file(kept, output_path)  # type: ignore[arg-type]  # a list is serialised fine
    return len(kept), len(messages) - len(kept)


def newest_export(updates_dir: Path, export_filename: str) -> Path:
    """Return the latest ``<stamp>_<export_filename>`` in ``updates_dir`` (stamps sort by name)."""
    candidates = sorted(updates_dir.glob(f"*_{export_filename}"))
    if not candidates:
        raise FileNotFoundError(f"no '*_{export_filename}' export found in {updates_dir}")
    return candidates[-1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--export", type=Path, help="full export (default: newest in the updates dir)")
    parser.add_argument("--force", action="store_true", help="overwrite an existing output file")
    args = parser.parse_args()

    setup_step_logging(SHARED)
    export_path = args.export or newest_export(UPDATES_DIR, INPUT_CONFIG["export_filename"])
    cutoff = datetime.combine(date.fromisoformat(INPUT_CONFIG["cutoff_date"]), time.min)
    logger.info(f"Splitting {export_path} at {cutoff:%Y-%m-%d %H:%M}")
    kept, remaining = split_export(export_path, cutoff, CHAT_ANDROID_PATH, force=args.force)
    logger.info(f"kept={kept} remaining={remaining}; wrote {CHAT_ANDROID_PATH}")


if __name__ == "__main__":
    main()
