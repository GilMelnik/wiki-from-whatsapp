"""The references under a wiki page, rendered by step 9 from the claim groups (C6).

A page body (C9) cites claim groups with markdown footnote marks ``[^g-xxxxxxxx]`` and stores
no definitions. At build time each cited group gets one definition, in order of first
citation, worded by ``site.references``: the group id, when it was said (one Hebrew month, or
first – last) and how many people wrote in the conversation(s) its claims come from, e.g.
``g-1a2b3c4d · מאי 2024 · 7 משתתפים בשיחה``. No claim text. A group without a known count
shows its id and date only. The result is ordinary python-markdown footnotes, so the
references toggle of the site (``sup[id^="fnref"]``, ``.footnote``) works unchanged.

A body stored with definitions (step 7 before this change) is read without them. A mark of a
group merged away (C6 ``merged_from``) points at the group that holds it now; a mark of a
group that exists nowhere is dropped. Both are reported to the caller.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from step_7_write_pages.checks import CITATION, cited_ids, strip_definitions
from step_7_write_pages.inputs import hebrew_month
from utils.config import section

REFERENCES_CONFIG = section("site")["references"]
MERGED = "merged_reference"
UNKNOWN = "unknown_reference"


def _when(group: Mapping[str, Any]) -> str | None:
    """The month the group was said, or the range of months; ``None`` without dates."""
    first, last = group.get("first_date"), group.get("last_date")
    if not first or not last:
        return None
    first_month, last_month = hebrew_month(first), hebrew_month(last)
    if first_month == last_month:
        return first_month
    return REFERENCES_CONFIG["date_range"].format(first=first_month, last=last_month)


def _participants(group: Mapping[str, Any]) -> str | None:
    """How many people wrote in the group's conversation(s); ``None`` when unknown."""
    count = group.get("participant_count")
    if not count:
        return None
    people = (REFERENCES_CONFIG["participants_one"] if count == 1
              else REFERENCES_CONFIG["participants_many"].format(count=count))
    threads = int(group.get("thread_count") or 1)
    if threads == 1:
        return REFERENCES_CONFIG["conversations_one"].format(participants=people)
    return REFERENCES_CONFIG["conversations_many"].format(participants=people, count=threads)


def reference_text(group: Mapping[str, Any]) -> str:
    """One reference: group id · when · participants (the parts that are known)."""
    parts = [group["group_id"], _when(group), _participants(group)]
    return REFERENCES_CONFIG["separator"].join(part for part in parts if part)


@dataclass(frozen=True)
class ResolvedBody:
    """A page body with its marks resolved, and the problems found on the way
    (``(kind, group id)`` with kind ``merged_reference`` or ``unknown_reference``)."""

    body: str
    problems: tuple[tuple[str, str], ...]


class References:
    """The references of every page of one build, from the effective C6 groups."""

    def __init__(self, groups: Iterable[Mapping[str, Any]]) -> None:
        self.groups = {group["group_id"]: group for group in groups}
        self.merged = {old: group_id for group_id, group in self.groups.items()
                       for old in group.get("merged_from") or []}

    def resolve(self, body: str) -> ResolvedBody:
        """``body`` without stored definitions; marks of merged-away groups follow the group
        that holds them (a repeated mark right after it is dropped), unknown marks are
        dropped."""
        problems: list[tuple[str, str]] = []

        def mark(match: re.Match[str]) -> str:
            group_id = match.group(1)
            if group_id in self.groups:
                return match.group(0)
            if group_id in self.merged:
                problems.append((MERGED, group_id))
                return f"[^{self.merged[group_id]}]"
            problems.append((UNKNOWN, group_id))
            return ""

        resolved = CITATION.sub(mark, strip_definitions(body))
        if any(kind == MERGED for kind, _ in problems):
            resolved = re.sub(r"(\[\^([^\]\s]+)\])(?:\[\^\2\])+", r"\1", resolved)
        return ResolvedBody(resolved, tuple(problems))

    def with_references(self, body: str) -> str:
        """A resolved ``body`` + one definition per cited group, in order of first citation."""
        lines = [f"[^{group_id}]: {reference_text(self.groups[group_id])}"
                 for group_id in cited_ids(body)]
        return body.rstrip() + ("\n\n" + "\n".join(lines) if lines else "") + "\n"
