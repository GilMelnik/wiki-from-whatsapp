"""Coverage check: can a cheap classifier place claim groups from the page titles and scopes alone?

This measures the owner's requirement that "each claim could easily fit to one" page. A
fixed-seed sample of groups goes to the classifier stage ``plan.coverage.stage`` with one
``choice`` question over the page ids plus a "none" option; the question text holds only the
page titles and scopes. The report says how many groups got a confident page, how many fell
to "none", how the sample spreads over the pages (projected onto all groups), and which pages fall outside
the size guide ``plan.page_groups`` (too small to be a page, too large for one). It is part of the run's report, not of the plan.
Task 24 builds the real assignment on ``page_question`` and ``group_state``.
"""

from __future__ import annotations

import random
from collections import Counter
from collections.abc import Sequence
from typing import Any

from step_6_plan.model import PLAN_CONFIG
from utils.llm_client import Classifier, Question

COVERAGE_CONFIG = PLAN_CONFIG["coverage"]
PAGE_GROUPS = PLAN_CONFIG["page_groups"]
NONE = COVERAGE_CONFIG["none_option"]


def page_question(plan: dict[str, Any]) -> Question:
    """One ``choice`` question over the plan's page ids plus "none", described by title + scope."""

    pages = "\n".join(
        f"- {page_id}: {page['title_he']}. {page['scope']}"
        for page_id, page in plan["pages"].items()
    )
    text = (
        "Which wiki page does this claim belong on? Each page below is described by its title "
        "and its scope, which says what belongs on the page and what does not. Answer "
        f'"{NONE}" only if no page\'s scope covers the claim.\n{pages}'
    )
    return Question("page", "choice", text, options=(*plan["pages"], NONE))


def group_state(group: dict[str, Any]) -> str:
    """The text a classifier reads about one group: its claim and the entities it names."""

    names = ", ".join(group.get("entity_names") or [])
    return f"{group['claim_text']}\n({names})" if names else group["claim_text"]


def sample_groups(groups: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """The fixed-seed sample (``plan.coverage.sample_size`` / ``seed``), in input order."""

    size = min(COVERAGE_CONFIG["sample_size"], len(groups))
    picked = set(random.Random(COVERAGE_CONFIG["seed"]).sample(range(len(groups)), size))
    return [group for index, group in enumerate(groups) if index in picked]


def summarize(
    plan: dict[str, Any], picks: Sequence[tuple[str, dict[str, float]]], total_groups: int
) -> dict[str, Any]:
    """The coverage numbers from ``(group_id, probabilities)`` answers of the sample.

    A group is placed on its most probable option; it is "confident" when that option is a
    page with probability >= ``confident_probability``. Page counts use every group whose top
    option is a page, confident or not. A page is small when its projected group count is below
    ``plan.page_groups.fold_below``, large when above ``split_above`` (the size guide the
    planning prompt gives; the projection is an estimate from the sample, so a page near a
    limit may be on either side of it).
    """

    n = len(picks)
    tops = [(gid, max(probs, key=probs.__getitem__), max(probs.values())) for gid, probs in picks]
    confident = {
        gid for gid, top, p in tops if top != NONE and p >= COVERAGE_CONFIG["confident_probability"]
    }
    none_ids = [gid for gid, top, _ in tops if top == NONE]
    counts = Counter(top for _, top, _ in tops if top != NONE)
    page_counts = {page_id: counts.get(page_id, 0) for page_id in plan["pages"]}
    share = {page_id: count / n for page_id, count in page_counts.items()} if n else {}
    projected = {pid: round(s * total_groups) for pid, s in share.items()}
    return {
        "sample_size": n,
        "total_groups": total_groups,
        "confident_share": len(confident) / n if n else 0.0,
        "none_share": len(none_ids) / n if n else 0.0,
        "unsure_share": (n - len(confident) - len(none_ids)) / n if n else 0.0,
        "page_counts": page_counts,
        "projected_groups": projected,
        "small_pages": [pid for pid, size in projected.items() if size < PAGE_GROUPS["fold_below"]],
        "large_pages": [pid for pid, size in projected.items() if size > PAGE_GROUPS["split_above"]],
        "none_group_ids": none_ids,
        "unsure_group_ids": [gid for gid, top, _ in tops if top != NONE and gid not in confident],
    }


def coverage_check(
    plan: dict[str, Any], groups: Sequence[dict[str, Any]], classifier: Classifier
) -> dict[str, Any]:
    """Classify the fixed sample of ``groups`` against ``plan``'s pages and summarize it."""

    sample = sample_groups(groups)
    question = page_question(plan)
    answers = classifier.classify_many(
        [(group_state(group), [question]) for group in sample], task="plan-coverage"
    )
    picks = [(group["group_id"], answer[0].probabilities) for group, answer in zip(sample, answers)]
    return summarize(plan, picks, len(groups))


def findings(report: dict[str, Any], plan: dict[str, Any]) -> list[str]:
    """The problems worth one revision call, as sentences for the model (empty = none)."""

    found: list[str] = []
    unplaced = report["none_share"] + report["unsure_share"]
    if unplaced > COVERAGE_CONFIG["max_unplaced_share"]:
        found.append(
            f"{unplaced:.0%} of the sample was not placed confidently: {report['none_share']:.0%} "
            f"fit no page (groups {', '.join(report['none_group_ids'])}) and "
            f"{report['unsure_share']:.0%} were uncertain (groups "
            f"{', '.join(report['unsure_group_ids'])})."
        )
    counts, projected = report["page_counts"], report["projected_groups"]
    found += [
        f"Page {pid!r} ({plan['pages'][pid]['title_he']}) attracted only {counts[pid]} of "
        f"{report['sample_size']} sampled groups (about {projected[pid]} of "
        f"{report['total_groups']}): too small for a page of its own (fewer than "
        f"{PAGE_GROUPS['fold_below']}); make it a section of a neighbouring page."
        for pid in report["small_pages"]
    ]
    found += [
        f"Page {pid!r} ({plan['pages'][pid]['title_he']}) attracted {counts[pid]} of "
        f"{report['sample_size']} sampled groups (about {projected[pid]} of "
        f"{report['total_groups']}): likely too broad (more than {PAGE_GROUPS['split_above']}); "
        "split it by sub-subject."
        for pid in report["large_pages"]
    ]
    return found
