"""Chat-export messages as the C1 message records step 1 writes.

The Android crypt15 export is a list of ``{id, date "DD/MM/YYYY", time "HH:MM", sender
{user_name, phone}, text, quote {sender, text} | null, reactions [{emoji, senders}], ...}``
in chronological order. ``message_record`` turns one entry into the C1 message
``{id, datetime, sender, content, quote?, reactions?}`` (senders are ``user_name`` strings,
as the old threads carried them). A message whose content is empty once WhatsApp's markers
are removed (a photo without a caption, a deleted message) carries nothing for the wiki and
is not part of any thread. ``load_export_messages`` reads a whole export that way (step 1,
step 3's split experiment, the privacy tool); ``utils.split_chat_export`` and the update read
the export's dates with ``parse_android_datetime``.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

UNICODE_MARKS = re.compile(r"[‎‏‪-‮⁦-⁩]")
OMITTED_MARKERS = re.compile(
    r"[‎‏]?(?:<\s*)?(?:image|video|audio|sticker|document|gif|media)\s+omitted(?:\s*>)?",
    re.IGNORECASE,
)
EDITED_MARKER = re.compile(
    r"[‎‏]?<\s*This message was edited\s*>?", re.IGNORECASE
)


def normalize_content(content: str) -> str:
    """The text without direction marks, "edited" and "media omitted" markers."""
    content = UNICODE_MARKS.sub("", content)
    content = EDITED_MARKER.sub("", content)
    content = OMITTED_MARKERS.sub("", content)
    return content.strip()


def parse_android_datetime(date_str: str, time_str: str) -> datetime:
    """Parse DD/MM/YYYY date and HH:MM time from the Android crypt15 export."""
    day, month, year = date_str.split("/")
    hour, minute = time_str.split(":")
    return datetime(int(year), int(month), int(day), int(hour), int(minute))


def _user_name(sender: dict[str, Any] | str) -> str:
    return sender["user_name"] if isinstance(sender, dict) else sender


def message_record(entry: dict[str, Any]) -> dict[str, Any] | None:
    """The C1 message for one export entry, or ``None`` when it has no content."""
    if not normalize_content(entry.get("text") or ""):
        return None
    record: dict[str, Any] = {
        "id": str(entry["id"]),
        "datetime": parse_android_datetime(entry["date"], entry["time"]).isoformat(),
        "sender": _user_name(entry["sender"]),
        "content": entry["text"],
    }
    quote = entry.get("quote")
    if quote:
        record["quote"] = {"sender": _user_name(quote["sender"]), "text": quote["text"]}
    reactions = [
        {"emoji": r["emoji"], "senders": [_user_name(s) for s in r.get("senders") or []]}
        for r in entry.get("reactions") or []
    ]
    if reactions:
        record["reactions"] = reactions
    return record


def load_export_messages(export_path: Path) -> list[dict[str, Any]]:
    """The C1 records of every export message with content, in export (time) order."""
    if not export_path.is_file():
        raise FileNotFoundError(f"chat export not found: {export_path}")
    entries = json.loads(export_path.read_text(encoding="utf-8"))
    records = [record for record in map(message_record, entries) if record is not None]
    return sorted(records, key=lambda record: record["datetime"])
