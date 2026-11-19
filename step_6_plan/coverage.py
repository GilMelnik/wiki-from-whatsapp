"""Coverage check: can a cheap classifier place claims from the page titles and scopes alone?

This measures the owner's requirement that "each claim could easily fit to one" page. A
fixed-seed sample of claims goes to the classifier stage ``plan.coverage.stage`` with one
``choice`` question over the page ids plus a "none" option; the question text holds only the
page titles and scopes. The report says how many claims got a confident page, how many fell
to "none", how the sample spreads over the pages (projected onto all claims), and which pages
fall outside the size guide ``plan.page_claims`` (too small to be a page, too large for one).
It is part of the run's report, not of the plan. Step 6b builds the real assignment on
``page_question`` and ``claim_state``.
"""

from __future__ import annotations

import random
from collections import Counter
from collections.abc import Sequence
from typing import Any

from step_6_plan.claim_records import Record
from step_6_plan.model import PLAN_CONFIG
from utils.llm_client import Classifier, Question

COVERAGE_CONFIG = PLAN_CONFIG["coverage"]
PAGE_CLAIMS = PLAN_CONFIG["page_claims"]
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


def claim_state(claim: Record) -> str:
    """The text a classifier reads about one claim: its text and the entities it names (no id,
    no date, no count)."""

    names = ", ".join(claim["entity_names"])
    return f"{claim['claim_text']}\n({names})" if names else claim["claim_text"]


def sample_claims(claims: Sequence[Record]) -> list[Record]:
    """The fixed-seed sample (``plan.coverage.sample_size`` / ``seed``), in input order."""

    size = min(COVERAGE_CONFIG["sample_size"], len(claims))
    picked = set(random.Random(COVERAGE_CONFIG["seed"]).sample(range(len(claims)), size))
    return [claim for index, claim in enumerate(claims) if index in picked]


def summarize(
    plan: dict[str, Any], picks: Sequence[tuple[str, dict[str, float]]], total_claims: int
) -> dict[str, Any]:
    """The coverage numbers from ``(short_id, probabilities)`` answers of the sample.

    A claim is placed on its most probable option; it is "confident" when that option is a
    page with probability >= ``confident_probability``. Page counts use every claim whose top
    option is a page, confident or not. A page is small when its projected claim count is below
    ``plan.page_claims.fold_below``, large when above ``split_above`` (the size guide the
    planning prompt gives; the projection is an estimate from the sample, so a page near a
    limit may be on either side of it).
    """

    n = len(picks)
    tops = [(sid, max(probs, key=probs.__getitem__), max(probs.values())) for sid, probs in picks]
    confident = {
        sid for sid, top, p in tops if top != NONE and p >= COVERAGE_CONFIG["confident_probability"]
    }
    none_ids = [sid for sid, top, _ in tops if top == NONE]
    counts = Counter(top for _, top, _ in tops if top != NONE)
    page_counts = {page_id: counts.get(page_id, 0) for page_id in plan["pages"]}
    share = {page_id: count / n for page_id, count in page_counts.items()} if n else {}
    projected = {pid: round(s * total_claims) for pid, s in share.items()}
    return {
        "sample_size": n,
        "total_claims": total_claims,
        "confident_share": len(confident) / n if n else 0.0,
        "none_share": len(none_ids) / n if n else 0.0,
        "unsure_share": (n - len(confident) - len(none_ids)) / n if n else 0.0,
        "page_counts": page_counts,
        "projected_claims": projected,
        "small_pages": [pid for pid, size in projected.items() if size < PAGE_CLAIMS["fold_below"]],
        "large_pages": [pid for pid, size in projected.items() if size > PAGE_CLAIMS["split_above"]],
        "none_claims": none_ids,
        "unsure_claims": [sid for sid, top, _ in tops if top != NONE and sid not in confident],
    }


def coverage_check(
    plan: dict[str, Any], claims: Sequence[Record], classifier: Classifier
) -> dict[str, Any]:
    """Classify the fixed sample of ``claims`` against ``plan``'s pages and summarize it."""

    sample = sample_claims(claims)
    question = page_question(plan)
    answers = classifier.classify_many(
        [(claim_state(claim), [question]) for claim in sample], task="plan-coverage"
    )
    picks = [(claim["short_id"], answer[0].probabilities) for claim, answer in zip(sample, answers)]
    return summarize(plan, picks, len(claims))


def _unplaced_share(report: dict[str, Any]) -> float:
    return report["none_share"] + report["unsure_share"]


def named_claims(report: dict[str, Any]) -> list[str]:
    """The claims ``findings`` names: the sampled claims not placed confidently, when their
    share is above ``plan.coverage.max_unplaced_share`` (else none)."""

    if _unplaced_share(report) <= COVERAGE_CONFIG["max_unplaced_share"]:
        return []
    return [*report["none_claims"], *report["unsure_claims"]]


def findings(report: dict[str, Any], plan: dict[str, Any]) -> list[str]:
    """The problems worth one revision call, as sentences for the model (empty = none)."""

    found: list[str] = []
    unplaced = _unplaced_share(report)
    if named_claims(report):
        found.append(
            f"{unplaced:.0%} of the sample was not placed confidently: {report['none_share']:.0%} "
            f"fit no page (claims {', '.join(report['none_claims'])}) and "
            f"{report['unsure_share']:.0%} were uncertain (claims "
            f"{', '.join(report['unsure_claims'])})."
        )
    counts, projected = report["page_counts"], report["projected_claims"]
    found += [
        f"Page {pid!r} ({plan['pages'][pid]['title_he']}) attracted only {counts[pid]} of "
        f"{report['sample_size']} sampled claims (about {projected[pid]} of "
        f"{report['total_claims']}): too small for a page of its own (fewer than "
        f"{PAGE_CLAIMS['fold_below']}); make it a section of a neighbouring page."
        for pid in report["small_pages"]
    ]
    found += [
        f"Page {pid!r} ({plan['pages'][pid]['title_he']}) attracted {counts[pid]} of "
        f"{report['sample_size']} sampled claims (about {projected[pid]} of "
        f"{report['total_claims']}): likely too broad (more than {PAGE_CLAIMS['split_above']}); "
        "split it by sub-subject."
        for pid in report["large_pages"]
    ]
    return found
