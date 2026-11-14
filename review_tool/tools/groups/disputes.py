"""Where a contradiction link between two claim groups comes from, and who disputes what.

A group's ``contradicts`` (C6) mixes three kinds of link (``step_5_aggregate.derive``):

- ``conversation``: a member of one group answered a member of the other in a conversation
  and disputed it (C3 ``counters``, recorded by step 3); the claim pairs say who disputes what;
- ``wording``: similar wording with opposite stances about a shared entity (step 5's rule);
- ``manual``: linked by the owner in this tool (``contradicts_manual``).

Claim pairs come from step 5's own scorer (``ClaimScorer.counter_pairs``), so they read the
claims exactly as a step-5 run does. Without vectors (no scorer) the origin is unknown.
"""

from __future__ import annotations

from collections.abc import Sequence

from step_5_aggregate.groups import Group
from step_5_aggregate.similarity import ClaimScorer

CONVERSATION = "conversation"
WORDING = "wording"
MANUAL = "manual"


class Disputes:
    """The claim pairs that dispute each other across two groups, by group pair."""

    def __init__(self, groups: Sequence[Group], scorer: ClaimScorer | None) -> None:
        self.known = scorer is not None
        self._pairs: dict[tuple[str, str], list[tuple[str, str]]] = {}
        if scorer is None:
            return
        group_of = {cid: g["group_id"] for g in groups for cid in g["member_claim_ids"]}
        for a, b in scorer.counter_pairs():
            group_a, group_b = group_of.get(a), group_of.get(b)
            if group_a is None or group_b is None or group_a == group_b:
                continue
            self._pairs.setdefault((group_a, group_b), []).append((a, b))
            self._pairs.setdefault((group_b, group_a), []).append((b, a))

    def claim_pairs(self, group_id: str, other_id: str) -> list[tuple[str, str]]:
        """``(claim of group_id, claim of other_id)`` for every claim pair that disputes."""
        return self._pairs.get((group_id, other_id), [])

    def origin(self, group: Group, other_id: str) -> str | None:
        """Why ``group`` contradicts ``other_id`` (``None``: unknown without vectors)."""
        if other_id in (group.get("contradicts_manual") or []):
            return MANUAL
        if self.claim_pairs(group["group_id"], other_id):
            return CONVERSATION
        return WORDING if self.known else None
