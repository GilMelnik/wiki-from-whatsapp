"""The references under a wiki page, rendered by step 9 from the claim records (C6).

A page body (C9) cites claims with markdown footnote marks ``[^g-xxxxxxxx]`` (their short ids)
and stores no definitions. At build time each cited claim gets one definition, in order of
first citation, worded by ``site.references``: the short id, when it was said (one Hebrew
month, or first – last) and how many people wrote in the conversation it comes from, e.g.
``g-1a2b3c4d · מאי 2024 · 7 משתתפים בשיחה``. No claim text, and never the claim's private
``claim_id`` / ``thread_id``. A claim without a known count shows its id and date only. Each
reference starts with ``start_mark`` (a right-to-left mark): the site lays out every paragraph
by its first strong character, and the reference must read right to left with the id first,
not as a left-to-right line that starts with the id. The result is ordinary python-markdown
footnotes, so the references toggle of the site (``sup[id^="fnref"]``, ``.footnote``) works
unchanged.

A body stored with definitions (step 7 before this change) is read without them. A mark of a
claim that exists nowhere is dropped and reported to the caller.
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
UNKNOWN = "unknown_reference"


def _when(claim: Mapping[str, Any]) -> str | None:
    """The month the claim was said, or the range of months; ``None`` without dates."""
    first, last = claim["first_date"], claim["last_date"]
    if not first or not last:
        return None
    first_month, last_month = hebrew_month(first), hebrew_month(last)
    if first_month == last_month:
        return first_month
    return REFERENCES_CONFIG["date_range"].format(first=first_month, last=last_month)


def _participants(claim: Mapping[str, Any]) -> str | None:
    """How many people wrote in the claim's conversation; ``None`` when unknown."""
    count = claim["participant_count"]
    if not count:
        return None
    people = (REFERENCES_CONFIG["participants_one"] if count == 1
              else REFERENCES_CONFIG["participants_many"].format(count=count))
    return REFERENCES_CONFIG["in_conversation"].format(participants=people)


def reference_text(claim: Mapping[str, Any]) -> str:
    """One reference: short id · when · participants (the parts that are known)."""
    parts = [claim["short_id"], _when(claim), _participants(claim)]
    return REFERENCES_CONFIG["start_mark"] + REFERENCES_CONFIG["separator"].join(part for part in parts if part)


@dataclass(frozen=True)
class ResolvedBody:
    """A page body with its marks resolved, and the problems found on the way
    (``(kind, short id)`` with kind ``unknown_reference``)."""

    body: str
    problems: tuple[tuple[str, str], ...]


class References:
    """The references of every page of one build, from the claim records (C6)."""

    def __init__(self, claims: Iterable[Mapping[str, Any]]) -> None:
        self.claims = {claim["short_id"]: claim for claim in claims}

    def resolve(self, body: str) -> ResolvedBody:
        """``body`` without stored definitions; marks of unknown claims are dropped."""
        problems: list[tuple[str, str]] = []

        def mark(match: re.Match[str]) -> str:
            short_id = match.group(1)
            if short_id in self.claims:
                return match.group(0)
            problems.append((UNKNOWN, short_id))
            return ""

        return ResolvedBody(CITATION.sub(mark, strip_definitions(body)), tuple(problems))

    def with_references(self, body: str) -> str:
        """A resolved ``body`` + one definition per cited claim, in order of first citation."""
        lines = [f"[^{short_id}]: {reference_text(self.claims[short_id])}"
                 for short_id in cited_ids(body)]
        return body.rstrip() + ("\n\n" + "\n".join(lines) if lines else "") + "\n"
