"""Exact page sizes against the planning size guide (``plan.page_groups``, task 23b).

A page's size is the number of claim groups the assignments (C8, edited if present, read
through today's plan) make its main page (``pages[0]``); a group that only mentions the page
does not count, nor does a group the owner's rule withholds from the wiki
(``step_7_write_pages.withhold``): it is on no page. The guide: fewer than ``fold_below``
groups is a section of a neighbouring page, not a page (``fold``); a page usually rests on
``typical_min`` to ``typical_max`` (below: ``small``, above: ``large``); more than
``split_above`` is split by sub-subject (``split``). A count exactly on a limit is inside it.
These replace the planning run's flags, which were projected from a sample.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from typing import Any

from step_6_plan.model import PLAN_CONFIG

GUIDE = PLAN_CONFIG["page_groups"]
FOLD, SMALL, LARGE, SPLIT = "fold", "small", "large", "split"


def size_flag(count: int) -> str | None:
    """What the guide says about a page with ``count`` groups (``None``: inside the guide)."""

    if count < GUIDE["fold_below"]:
        return FOLD
    if count > GUIDE["split_above"]:
        return SPLIT
    if count < GUIDE["typical_min"]:
        return SMALL
    if count > GUIDE["typical_max"]:
        return LARGE
    return None


def page_sizes(records: Mapping[str, Mapping[str, Any]], plan: Mapping[str, Any],
               withheld: Collection[str]) -> dict[str, dict[str, Any]]:
    """``{page_id: {"groups": n, "flag": size_flag(n)}}`` for every page of the plan."""

    counts = dict.fromkeys(plan["pages"], 0)
    for group_id, record in records.items():
        pages = record["pages"]
        if group_id not in withheld and pages and pages[0] in counts:
            counts[pages[0]] += 1
    return {page_id: {"groups": count, "flag": size_flag(count)} for page_id, count in counts.items()}
