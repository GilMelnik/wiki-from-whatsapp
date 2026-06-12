"""Shared helpers for loading threads and rendering them for the LLM.

The renderer assigns each thread its own anonymous participant labels
("משתתף 1", "משתתף 2", ...) so that no pseudonymous sender id ever reaches the
LLM, and tags every line with a stable local index ``[m{i}]`` that downstream
stages use to map a claim back to its supporting messages, senders and dates.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DEFAULT_THREADS_PATH = Path("data/threads.json")


def load_threads(path: Path | str = DEFAULT_THREADS_PATH) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as f:
        return json.load(f)


def thread_text(thread: dict[str, Any]) -> str:
    """All message contents concatenated (used for heuristic keyword matching)."""

    return "\n".join(m.get("content", "") or "" for m in thread.get("messages", []))


def month_of(iso_datetime: str) -> str:
    """Return ``YYYY-MM`` from an ISO datetime string."""

    return iso_datetime[:7]


def render_thread_for_llm(thread: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    """Render a thread as anonymized text plus a per-line metadata map.

    Returns ``(rendered_text, line_meta)`` where ``line_meta[i]`` holds the
    ``sender`` and ``month`` for local message index ``i`` (only non-empty
    messages are rendered/indexed).
    """

    sender_labels: dict[str, str] = {}
    lines: list[str] = []
    line_meta: list[dict[str, Any]] = []

    for message in thread.get("messages", []):
        content = (message.get("content") or "").strip()
        if not content:
            continue
        sender = message.get("sender", "")
        if sender not in sender_labels:
            sender_labels[sender] = f"משתתף {len(sender_labels) + 1}"
        label = sender_labels[sender]
        month = month_of(message.get("datetime", ""))
        local_index = len(line_meta)
        lines.append(f"[m{local_index}] ({month}, {label}) {content}")
        line_meta.append({"sender": sender, "month": month, "label": label})

    return "\n".join(lines), line_meta
