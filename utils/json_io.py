from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Any


def write_json_file(payload: dict[str, Any], output_path: Path) -> None:
    """Write ``payload`` to ``output_path`` atomically (temp file + replace).

    A crash mid-write leaves the previous file intact. The temp file name is unique per
    call (pid + random suffix), so concurrent writers never clobber each other's temp
    file and the last ``replace`` wins. This function does NOT take a lock: callers that
    do a read-modify-write (or otherwise need mutual exclusion) must still wrap the whole
    sequence in ``utils.file_lock.file_lock``.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_name(
        f".{output_path.name}.tmp-{os.getpid()}-{secrets.token_hex(4)}"
    )
    try:
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        tmp_path.replace(output_path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
