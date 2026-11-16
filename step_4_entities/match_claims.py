"""The claims the model stages show of a name: spread evenly over the time it was mentioned.

A name's claims are ordered by date (``first_date``, then ``claim_id``) and picked at evenly
spaced positions, so a name used for one thing early and another thing later shows both. A name
with many claims shows more of them: the square root of its claim count, between
``entities.match.examples.min_claims`` and ``max_claims``. Every claim line is its month and its
text, cut at ``claim_chars``.
"""

from __future__ import annotations

import math
from collections.abc import Collection, Mapping
from typing import Any

from utils.config import ENTITIES_CONFIG

EXAMPLES_CONFIG = ENTITIES_CONFIG["match"]["examples"]
ELLIPSIS = "…"


class ClaimSampler:
    """Claim lines of names, read from the claims by id."""

    def __init__(self, claims_by_id: Mapping[str, Mapping[str, Any]]) -> None:
        self.claims_by_id = claims_by_id

    def ordered(self, claim_ids: Collection[str]) -> list[str]:
        """The claims that exist, oldest first (ties by id)."""

        known = [c for c in claim_ids if c in self.claims_by_id]
        return sorted(known, key=lambda c: (self.claims_by_id[c].get("first_date") or "", c))

    @staticmethod
    def example_count(total: int) -> int:
        """How many examples a name with ``total`` claims shows (all when it has few)."""

        wanted = math.ceil(math.sqrt(total))
        wanted = max(EXAMPLES_CONFIG["min_claims"], min(EXAMPLES_CONFIG["max_claims"], wanted))
        return min(total, wanted)

    def spread(self, claim_ids: Collection[str], count: int | None = None) -> list[str]:
        """``count`` claims (default ``example_count``) at evenly spaced dates, oldest first."""

        ordered = self.ordered(claim_ids)
        size = self.example_count(len(ordered)) if count is None else min(count, len(ordered))
        if size <= 0:
            return []
        if size == 1:
            return [ordered[(len(ordered) - 1) // 2]]
        step = (len(ordered) - 1) / (size - 1)
        return list(dict.fromkeys(ordered[round(i * step)] for i in range(size)))

    def text(self, claim_id: str) -> str:
        """The claim's text on one line, cut at ``claim_chars``."""

        text = " ".join(str(self.claims_by_id[claim_id].get("claim_text") or "").split())
        limit = EXAMPLES_CONFIG["claim_chars"]
        return text if len(text) <= limit else text[: limit - 1].rstrip() + ELLIPSIS

    def line(self, claim_id: str) -> str:
        """``YYYY-MM: text`` (the month is left out when the claim has no date)."""

        month = str(self.claims_by_id[claim_id].get("first_date") or "")[:7]
        return f"{month}: {self.text(claim_id)}" if month else self.text(claim_id)
