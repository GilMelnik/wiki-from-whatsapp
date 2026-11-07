from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def write_json_file(payload: dict[str, Any], output_path: Path) -> None:
    """Write ``payload`` to ``output_path`` atomically (temp file + replace).

    A crash mid-write leaves the previous file intact. This function does NOT take a
    lock: callers that need mutual exclusion (including between threads of one process,
    which share the temp file name) must wrap the call in ``utils.file_lock.file_lock``.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_name(f".{output_path.name}.tmp-{os.getpid()}")
    try:
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        tmp_path.replace(output_path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
