"""Shared helpers for loading threads and rendering them for the LLM.

The renderer assigns each thread its own anonymous participant labels
("משתתף 1", "משתתף 2", ...) so that no pseudonymous sender id ever reaches the
LLM, and tags every line with a stable local index ``[m{i}]`` that downstream
stages use to map a claim back to its supporting messages, senders and dates.
Reaction counts (without sender identities) are appended so the extractor can
weigh social endorsement when judging claim validity.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from thread_tagger.paths import resolve_threads_path

from wiki_build.support import format_reactions_for_llm

DEFAULT_THREADS_PATH = resolve_threads_path()


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
    ``sender``, ``month``, ``message_index`` (into ``thread["messages"]``) and
    ``reactions`` for local message index ``i`` (only non-empty messages are
    rendered/indexed).
    """

    sender_labels: dict[str, str] = {}
    lines: list[str] = []
    line_meta: list[dict[str, Any]] = []

    for message_index, message in enumerate(thread.get("messages", [])):
        content = (message.get("content") or "").strip()
        if not content:
            continue
        sender = message.get("sender", "")
        if sender not in sender_labels:
            sender_labels[sender] = f"משתתף {len(sender_labels) + 1}"
        label = sender_labels[sender]
        month = month_of(message.get("datetime", ""))
        reactions = message.get("reactions") or []
        local_index = len(line_meta)
        reaction_suffix = format_reactions_for_llm(reactions)
        lines.append(f"[m{local_index}] ({month}, {label}) {content}{reaction_suffix}")
        line_meta.append(
            {
                "sender": sender,
                "month": month,
                "label": label,
                "message_index": message_index,
                "reactions": reactions,
            }
        )

    return "\n".join(lines), line_meta
