"""The date of a message of the Android chat export (``date`` "DD/MM/YYYY", ``time``
"HH:MM:SS", or "HH:MM" in exports made before the export tool wrote seconds).

Read before step 1 by ``utils.split_chat_export`` (the pipeline's input is cut at a date),
by step 1 (``step_1_threads_split.message``) and by the update's export check.
"""

from __future__ import annotations

from datetime import datetime


def parse_android_datetime(date_str: str, time_str: str) -> datetime:
    """Parse the DD/MM/YYYY date and the HH:MM:SS (or HH:MM) time of the Android export."""
    day, month, year = date_str.split("/")
    parts = [int(part) for part in time_str.split(":")]
    if len(parts) not in (2, 3):
        raise ValueError(f"export time {time_str!r} is neither HH:MM nor HH:MM:SS")
    return datetime(int(year), int(month), int(day), *parts)


def has_seconds(time_str: str) -> bool:
    """Whether an export time carries seconds (``HH:MM:SS``, the current export tool)."""
    return time_str.count(":") == 2
