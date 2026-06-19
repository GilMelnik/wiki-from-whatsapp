"""View models and helpers for the thread tagger."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

FilterKind = Literal["useless", "knowledge", "all"]
SortKind = Literal["num_messages", "participants", "start_time", "duration"]
SortOrder = Literal["asc", "desc"]


def parse_iso(iso: str) -> datetime:
    return datetime.fromisoformat(iso)


def duration_sec(start_time: str, last_time: str) -> float:
    return max(0.0, (parse_iso(last_time) - parse_iso(start_time)).total_seconds())


def default_classification(thread: dict[str, Any]) -> dict[str, Any]:
    return {
        "thread_id": thread["thread_id"],
        "start_time": thread["start_time"],
        "last_time": thread["last_time"],
        "num_messages": thread["num_messages"],
        "num_unique_senders": thread["num_unique_senders"],
        "passed_heuristic": True,
        "is_knowledge_bearing": False,
        "topic_tags": [],
        "emergent_tags": [],
        "entities": [],
        "reason": "missing_classification",
    }
