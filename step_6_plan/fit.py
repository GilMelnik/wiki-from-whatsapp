"""Which claims the planning prompt lists, so that it fits ``plan.max_input_tokens``.

When the prompt with every claim fits the budget, it lists every claim. When it does not, the
model sees a sample that fits:

- every claim that more than one member backs (``supporter_count`` > 1: they carry the wiki's
  weight);
- then claims that fewer than two members back, as many as the budget still holds, chosen with
  the seed ``plan.sample_seed`` and spread evenly over time (``spread_sample``).

If the first part alone does not fit, it is sampled by the same rule and the second part is
left out. The sample's size is found by bisection: a sample of that size fits the budget and
one of a single claim more does not.
Sizes are estimated by characters (``plan.chars_per_token``: 1.497 characters per token by the
counting endpoint on the development corpus's full prompt on 2026-11-12, rounded down); the
endpoint's count stays the final check before each call (``step_6_plan.run``). A sampled prompt
says so in a note before the list and gives the size guide in claims of the list
(``step_6_plan.prompt``).
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence

from step_6_plan.claim_records import Record
from step_6_plan.model import PLAN_CONFIG
from step_6_plan.prompt import claims_block, initial_request, plan_system, sample_note


def backed_by_many(claim: Record) -> bool:
    return claim["supporter_count"] > 1


def spread_sample(claims: Sequence[Record], size: int) -> list[Record]:
    """``size`` of ``claims`` (at most all): the claims in time order (``first_date``) cut into
    ``size`` slices of equal length, and one claim from each slice at random (``plan.sample_seed``)."""

    ordered = sorted(claims, key=lambda claim: claim["first_date"])
    rng = random.Random(PLAN_CONFIG["sample_seed"])
    count = len(ordered)
    return [ordered[rng.randrange(count * i // size, count * (i + 1) // size)] for i in range(size)]


class PlanPrompt:
    """The planning prompt over the claims it shows: every claim, or a sample of them."""

    def __init__(self, shown: Sequence[Record], claims: Sequence[Record]) -> None:
        shown_ids = {claim["short_id"] for claim in shown}
        self.shown = [claim for claim in claims if claim["short_id"] in shown_ids]
        self.total = len(claims)
        self.system = plan_system(len(self.shown), self.total)
        self.claims_text = claims_block(self.shown)
        if len(self.shown) < self.total:
            many = sum(1 for claim in claims if backed_by_many(claim))
            shown_many = sum(1 for claim in self.shown if backed_by_many(claim))
            note = sample_note(shown_many, many, len(self.shown) - shown_many, self.total - many)
            self.claims_text = f"{note}\n\n{self.claims_text}"
        # The formula of utils.token_counters.CharTokenCounter (written out here while that
        # class lived in step 10, which step 6 could not import).
        user = self.user(initial_request(len(self.shown), self.total))
        self.estimated_tokens = math.ceil(
            (len(self.system) + len(user)) / PLAN_CONFIG["chars_per_token"]
        )

    def user(self, request: str) -> str:
        """The user message: the (noted) list of claims, then ``request``."""

        return f"{self.claims_text}\n\n{request}"

    def fits(self) -> bool:
        return self.estimated_tokens <= PLAN_CONFIG["max_input_tokens"]


def _fullest(claims: Sequence[Record], base: list[Record], part: list[Record]) -> PlanPrompt:
    """The prompt over ``base`` plus a ``spread_sample`` of ``part`` that fits while one of a
    single claim more does not (the caller knows that ``base`` alone fits, or that ``base`` is
    empty, and that ``base`` plus all of ``part`` does not)."""

    fitting, too_many = 0, len(part)
    while too_many - fitting > 1:
        size = (fitting + too_many) // 2
        if PlanPrompt([*base, *spread_sample(part, size)], claims).fits():
            fitting = size
        else:
            too_many = size
    prompt = PlanPrompt([*base, *spread_sample(part, fitting)], claims)
    if not prompt.shown:
        raise ValueError(
            f"plan.max_input_tokens ({PLAN_CONFIG['max_input_tokens']}) does not hold the "
            f"planning instructions with even one of the {len(claims)} claims"
        )
    return prompt


def fitted_prompt(claims: Sequence[Record]) -> PlanPrompt:
    """The planning prompt over every claim if it fits ``plan.max_input_tokens``, else over the
    sample described in the module docstring."""

    whole = PlanPrompt(claims, claims)
    if whole.fits():
        return whole
    many = [claim for claim in claims if backed_by_many(claim)]
    rest = [claim for claim in claims if not backed_by_many(claim)]
    if PlanPrompt(many, claims).fits():
        return _fullest(claims, many, rest)
    return _fullest(claims, [], many)
