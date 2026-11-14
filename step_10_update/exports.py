"""What a newer chat export adds to the pipeline input, and whether it continues it.

An export is the scraper's JSON list of messages ``{id, date, time, sender, text, quote,
reactions, ...}`` (``step_1_threads_split.message``). Compared with the current pipeline input:

- new: an id the input does not have;
- changed: same id, other text or reactions (step 1 refreshes those in place);
- missing: an id of the input the export does not have. Exports can be partial, so a missing
  message is NOT deleted: it stays in the pipeline input unless the owner asks for it
  (``merged_input(..., drop_missing=True)``).

``check_continuation`` refuses an export that does not continue the input: it must contain
the input's last messages (``update.overlap_messages``), or cover a later period that starts
where the input ends (within ``update.max_gap_days``), and a message id both have must be the
same message (same date, time and sender).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from utils.config import section
from utils.export_dates import parse_android_datetime

UPDATE_CONFIG = section("update")
Message = dict[str, Any]


class NotAContinuation(ValueError):
    """The export does not look like a later export of the same chat; nothing was changed."""


def load_export(path: Path) -> list[Message]:
    """The export's messages; raises ``ValueError`` on anything but a non-empty JSON list."""
    if not path.is_file():
        raise FileNotFoundError(f"chat export not found: {path}")
    try:
        messages = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(messages, list) or not messages:
        raise ValueError(f"{path} must hold a non-empty JSON list of messages")
    for index, message in enumerate(messages):
        if not isinstance(message, dict) or not {"id", "date", "time"} <= message.keys():
            raise ValueError(f"{path}: message {index} has no id / date / time")
    return messages


def file_fingerprint(path: Path) -> str:
    """sha1 of the file's bytes (the export's identity in the state and the report)."""
    digest = hashlib.sha1()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def message_time(message: Message) -> datetime:
    return parse_android_datetime(message["date"], message["time"])


def _identity(message: Message) -> tuple[Any, ...]:
    """What never changes for one message: when and by whom it was written."""
    sender = message.get("sender")
    name = sender.get("user_name") if isinstance(sender, dict) else sender
    return message["date"], message["time"], name


def _content(message: Message) -> tuple[Any, ...]:
    return message.get("text"), message.get("reactions")


@dataclass(frozen=True)
class ExportDiff:
    new: tuple[str, ...]
    changed: tuple[str, ...]
    missing: tuple[str, ...]
    conflicting: tuple[str, ...]
    old_last: datetime
    new_first: datetime
    new_last: datetime
    overlap_found: bool

    @property
    def nothing_new(self) -> bool:
        return not self.new and not self.changed

    def counts(self) -> dict[str, int]:
        return {"new": len(self.new), "changed": len(self.changed), "missing": len(self.missing)}


def compare_exports(old: list[Message], new: list[Message]) -> ExportDiff:
    """Ids new / changed / missing in ``new`` relative to ``old`` (both in export order)."""
    old_by_id = {str(m["id"]): m for m in old}
    new_by_id = {str(m["id"]): m for m in new}
    common = [i for i in new_by_id if i in old_by_id]
    last = sorted(old, key=message_time)[-UPDATE_CONFIG["overlap_messages"]:]
    last_ids = [str(m["id"]) for m in last]
    return ExportDiff(
        new=tuple(i for i in new_by_id if i not in old_by_id),
        changed=tuple(i for i in common if _content(new_by_id[i]) != _content(old_by_id[i])),
        missing=tuple(i for i in old_by_id if i not in new_by_id),
        conflicting=tuple(i for i in common if _identity(new_by_id[i]) != _identity(old_by_id[i])),
        old_last=max(map(message_time, old)),
        new_first=min(map(message_time, new)),
        new_last=max(map(message_time, new)),
        overlap_found=all(i in new_by_id for i in last_ids),
    )


def check_continuation(diff: ExportDiff) -> None:
    """Raise ``NotAContinuation`` unless the export continues the input (module docstring)."""
    if diff.conflicting:
        raise NotAContinuation(
            f"{len(diff.conflicting)} message ids of the new export belong to other messages in "
            "the current input (other date, time or sender): it is probably another chat. "
            "Nothing was changed."
        )
    if diff.new_last < diff.old_last:
        raise NotAContinuation(
            f"the new export ends {diff.new_last:%Y-%m-%d %H:%M}, before the current input "
            f"({diff.old_last:%Y-%m-%d %H:%M}): it is older, not newer. Nothing was changed."
        )
    if diff.overlap_found:
        return
    later = diff.new_first >= diff.old_last
    near = diff.new_first - diff.old_last <= timedelta(days=UPDATE_CONFIG["max_gap_days"])
    if later and near:
        return
    raise NotAContinuation(
        f"the new export lacks the current input's last {UPDATE_CONFIG['overlap_messages']} "
        f"messages and does not start where the input ends (input ends "
        f"{diff.old_last:%Y-%m-%d %H:%M}, export starts {diff.new_first:%Y-%m-%d %H:%M}; at most "
        f"{UPDATE_CONFIG['max_gap_days']} days later is accepted). It does not look like a "
        "continuation of the same chat. Nothing was changed."
    )


def merged_input(old: list[Message], new: list[Message], *, drop_missing: bool) -> list[Message]:
    """The next pipeline input: every message of ``new`` plus, unless ``drop_missing``, the
    messages of ``old`` that ``new`` lacks, in time order (``new``'s order kept when nothing
    is added)."""
    if drop_missing:
        return list(new)
    present = {str(m["id"]) for m in new}
    kept = [m for m in old if str(m["id"]) not in present]
    if not kept:
        return list(new)
    return sorted([*new, *kept], key=message_time)
