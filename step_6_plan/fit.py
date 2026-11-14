"""Which claim groups the planning prompt lists, so that it fits ``plan.max_input_tokens``.

When the prompt with every group fits the budget, it lists every group and is exactly the
prompt of a full list. When it does not, the model sees a sample that fits:

- every group that more than one member backs (``supporter_count`` > 1: they carry the wiki's
  weight);
- then groups that fewer than two members back, as many as the budget still holds, chosen with
  the seed ``plan.sample_seed`` and spread evenly over time (``spread_sample``).

If the first part alone does not fit, it is sampled by the same rule and the second part is
left out. The sample's size is found by bisection: a sample of that size fits the budget and
one of a single group more does not.
Sizes are estimated by characters (``plan.chars_per_token``: the development corpus's full
prompt, 3,133 groups, is 667,948 characters and 446,128 tokens by the counting endpoint, 1.497
characters per token on 2026-11-12, rounded down); the endpoint's count stays the final check
before each call (``step_6_plan.run``). A sampled prompt says so in a note before the list and
gives the size guide in groups of the list (``step_6_plan.prompt``).
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from typing import Any

from step_6_plan.model import PLAN_CONFIG
from step_6_plan.prompt import groups_block, initial_request, plan_system, sample_note


def backed_by_many(group: dict[str, Any]) -> bool:
    return group["supporter_count"] > 1


def spread_sample(groups: Sequence[dict[str, Any]], size: int) -> list[dict[str, Any]]:
    """``size`` of ``groups`` (at most all): the groups in time order (``first_date``) cut into
    ``size`` slices of equal length, and one group from each slice at random (``plan.sample_seed``)."""

    ordered = sorted(groups, key=lambda group: group["first_date"])
    rng = random.Random(PLAN_CONFIG["sample_seed"])
    count = len(ordered)
    return [ordered[rng.randrange(count * i // size, count * (i + 1) // size)] for i in range(size)]


class PlanPrompt:
    """The planning prompt over the groups it shows: every group, or a sample of them."""

    def __init__(self, shown: Sequence[dict[str, Any]], groups: Sequence[dict[str, Any]]) -> None:
        shown_ids = {group["group_id"] for group in shown}
        self.shown = [group for group in groups if group["group_id"] in shown_ids]
        self.total = len(groups)
        self.system = plan_system(len(self.shown), self.total)
        self.groups_text = groups_block(self.shown)
        if len(self.shown) < self.total:
            many = sum(1 for group in groups if backed_by_many(group))
            shown_many = sum(1 for group in self.shown if backed_by_many(group))
            note = sample_note(shown_many, many, len(self.shown) - shown_many, self.total - many)
            self.groups_text = f"{note}\n\n{self.groups_text}"
        # The formula of utils.token_counters.CharTokenCounter (written out here while that
        # class lived in step 10, which step 6 could not import).
        user = self.user(initial_request(len(self.shown), self.total))
        self.estimated_tokens = math.ceil(
            (len(self.system) + len(user)) / PLAN_CONFIG["chars_per_token"]
        )

    def user(self, request: str) -> str:
        """The user message: the (noted) list of groups, then ``request``."""

        return f"{self.groups_text}\n\n{request}"

    def fits(self) -> bool:
        return self.estimated_tokens <= PLAN_CONFIG["max_input_tokens"]


def _fullest(
    groups: Sequence[dict[str, Any]], base: list[dict[str, Any]], part: list[dict[str, Any]]
) -> PlanPrompt:
    """The prompt over ``base`` plus a ``spread_sample`` of ``part`` that fits while one of a
    single group more does not (the caller knows that ``base`` alone fits, or that ``base`` is
    empty, and that ``base`` plus all of ``part`` does not)."""

    fitting, too_many = 0, len(part)
    while too_many - fitting > 1:
        size = (fitting + too_many) // 2
        if PlanPrompt([*base, *spread_sample(part, size)], groups).fits():
            fitting = size
        else:
            too_many = size
    prompt = PlanPrompt([*base, *spread_sample(part, fitting)], groups)
    if not prompt.shown:
        raise ValueError(
            f"plan.max_input_tokens ({PLAN_CONFIG['max_input_tokens']}) does not hold the "
            f"planning instructions with even one of the {len(groups)} claim groups"
        )
    return prompt


def fitted_prompt(groups: Sequence[dict[str, Any]]) -> PlanPrompt:
    """The planning prompt over every group if it fits ``plan.max_input_tokens``, else over the
    sample described in the module docstring."""

    whole = PlanPrompt(groups, groups)
    if whole.fits():
        return whole
    many = [group for group in groups if backed_by_many(group)]
    rest = [group for group in groups if not backed_by_many(group)]
    if PlanPrompt(many, groups).fits():
        return _fullest(groups, many, rest)
    return _fullest(groups, [], many)
