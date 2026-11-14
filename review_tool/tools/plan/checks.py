"""Warnings about a valid plan: things the C7 rules allow but the owner probably wants to fix.

Each warning is ``{code, ids, text, tone}`` with an English sentence that names the items by
title and says what to do; ``tone`` is ``warning``, or ``info`` for a page a little outside the
usual size that may well be right. They are advice; nothing is refused because of them.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Mapping
from typing import Any

from review_tool.tools.plan.sizes import FOLD, GUIDE, LARGE, SMALL, SPLIT
from step_6_plan.model import PLAN_CONFIG, iter_nodes
from step_6_plan.operation_requests import quote

_PARENTHESIZED = re.compile(r"\([^)]*\)")
_NOT_A_LETTER = re.compile(r"[^\w\s]")


def _core(title: str) -> str:
    """A title without parenthesized remarks and punctuation, spaces collapsed."""

    return " ".join(_NOT_A_LETTER.sub(" ", _PARENTHESIZED.sub(" ", title)).split())


def _label_like_page(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """A label whose title is a page's title, part of it, or one of its Hebrew aliases: the
    same subject would live in two places (e.g. a country label next to that country's page)."""

    found = []
    for label_id, label in plan["labels"].items():
        core = _core(label["title_he"])
        if len(core) < 2:
            continue
        for page_id, page in plan["pages"].items():
            aliases = {_core(alias) for alias in page["aliases"]["he"]}
            if core in _core(page["title_he"]) or core in aliases:
                found.append({
                    "code": "label_like_page",
                    "ids": [label_id, page_id],
                    "text": f"The label {quote(label['title_he'])} overlaps the page "
                            f"{quote(page['title_he'])}: the same subject would appear in two "
                            "places. You can delete the label, merge it into another label or "
                            "sharpen its description.",
                })
                break
    return found


def _same_titles(plan: dict[str, Any]) -> list[dict[str, Any]]:
    by_title: dict[str, list[str]] = defaultdict(list)
    for node, _ in iter_nodes(plan["tree"]):
        by_title[_core(node["title_he"])].append(node["id"])
    for label_id, label in plan["labels"].items():
        by_title[_core(label["title_he"])].append(label_id)
    return [
        {"code": "same_title", "ids": ids,
         "text": f"{len(ids)} items are called {quote(title)}: they are hard to tell apart in "
                 "the navigation and in the assignment."}
        for title, ids in by_title.items() if len(ids) > 1
    ]


def _empty_categories(plan: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"code": "empty_category", "ids": [node["id"]],
         "text": f"The category {quote(node['title_he'])} has no pages; the site will skip it. "
                 "Move a page into it or delete it."}
        for node, _ in iter_nodes(plan["tree"])
        if node["id"] not in plan["pages"]
        and not any(n["id"] in plan["pages"] for n, _ in iter_nodes(node["children"]))
    ]


def _lonely_kinds(plan: dict[str, Any]) -> list[dict[str, Any]]:
    kinds: dict[str, list[str]] = defaultdict(list)
    for label_id, label in plan["labels"].items():
        kinds[label["kind"]].append(label_id)
    return [
        {"code": "single_label_kind", "ids": ids,
         "text": f"The label kind {kind} has only one label "
                 f"({quote(plan['labels'][ids[0]]['title_he'])}). Maybe it belongs to another kind."}
        for kind, ids in kinds.items() if len(ids) == 1
    ]


def _pages_without_examples(plan: dict[str, Any], group_ids: set[str] | None) -> list[dict[str, Any]]:
    found = []
    for page_id, page in plan["pages"].items():
        examples = page["example_group_ids"]
        known = examples if group_ids is None else [g for g in examples if g in group_ids]
        if not known:
            found.append({
                "code": "no_examples",
                "ids": [page_id],
                "text": f"The page {quote(page['title_he'])} has no example claim groups"
                        + ("" if not examples else " that exist in the groups file")
                        + ": it is hard to check whether its scope says what you meant.",
            })
    return found


def _budget(plan: dict[str, Any]) -> list[dict[str, Any]]:
    left = PLAN_CONFIG["max_pages"] - len(plan["pages"])
    if left > 0:
        return []
    return [{"code": "page_budget", "ids": [],
             "text": f"The plan has reached the maximum number of pages ({PLAN_CONFIG['max_pages']}): "
                     "a new page or a split first needs another page merged or deleted."}]


_SIZE_ADVICE = {
    FOLD: ("warning", f"fewer than {GUIDE['fold_below']}: the size guide makes a subject this small "
                      "a section of a neighbouring page. Merge it into the page it belongs with, or "
                      "widen its scope."),
    SMALL: ("info", f"under the usual {GUIDE['typical_min']} to {GUIDE['typical_max']}. Fine if the "
                    "subject is this narrow; otherwise widen its scope or merge it into a "
                    "neighbouring page."),
    LARGE: ("info", f"over the usual {GUIDE['typical_min']} to {GUIDE['typical_max']}. Fine if it is "
                    "one subject; split it if it covers several."),
    SPLIT: ("warning", f"more than {GUIDE['split_above']}: the size guide says to split it by "
                       "sub-subject."),
}


def _sizes(plan: dict[str, Any], sizes: Mapping[str, Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    """A page outside the size guide, by its exact count of groups (``sizes.page_sizes``)."""

    if sizes is None:
        return []
    found = []
    for page_id, size in sizes.items():
        if size["flag"] is None:
            continue
        tone, advice = _SIZE_ADVICE[size["flag"]]
        found.append({"code": f"{size['flag']}_page", "ids": [page_id], "tone": tone,
                      "text": f"The page {quote(plan['pages'][page_id]['title_he'])} is the main "
                              f"page of {size['groups']} claim groups, {advice}"})
    return found


def plan_warnings(
    plan: dict[str, Any],
    *,
    group_ids: set[str] | None,
    sizes: Mapping[str, Mapping[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Every warning about ``plan``. ``group_ids`` = the groups file's ids (``None`` when there
    is no groups file); ``sizes`` = ``sizes.page_sizes`` (``None`` before step 6b has run)."""

    found = [
        *_budget(plan),
        *_label_like_page(plan),
        *_same_titles(plan),
        *_empty_categories(plan),
        *_lonely_kinds(plan),
        *_pages_without_examples(plan, group_ids),
    ]
    return [*({"tone": "warning", **warning} for warning in found), *_sizes(plan, sizes)]
