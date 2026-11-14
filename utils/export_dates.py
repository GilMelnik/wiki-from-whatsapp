"""The date of a message of the Android chat export (``date`` "DD/MM/YYYY", ``time`` "HH:MM").

Read before step 1 by ``utils.split_chat_export`` (the pipeline's input is cut at a date),
by step 1 (``step_1_threads_split.message``) and by the update's export check.
"""

from __future__ import annotations

from datetime import datetime


def parse_android_datetime(date_str: str, time_str: str) -> datetime:
    """Parse DD/MM/YYYY date and HH:MM time from the Android crypt15 export."""
    day, month, year = date_str.split("/")
    hour, minute = time_str.split(":")
    return datetime(int(year), int(month), int(day), int(hour), int(minute))
