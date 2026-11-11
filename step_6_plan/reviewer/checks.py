"""Warnings about a valid plan: things the C7 rules allow but the owner probably wants to fix.

Each warning is ``{code, ids, text}`` with an English sentence that names the items by title and
says what to do. They are advice; nothing is refused because of them.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Mapping
from typing import Any

from step_6_plan.model import PLAN_CONFIG, iter_nodes
from step_6_plan.reviewer.requests import quote

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


def _coverage(plan: dict[str, Any], coverage: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    if coverage is None:
        return []
    found = []
    for page_id, numbers in coverage["pages"].items():
        title = quote(plan["pages"][page_id]["title_he"])
        if numbers["small"]:
            found.append({"code": "small_page", "ids": [page_id],
                          "text": f"In the coverage check the page {title} drew very few claims; "
                                  "maybe merge it into a neighbouring page or widen its scope."})
        if numbers["large"]:
            found.append({"code": "large_page", "ids": [page_id],
                          "text": f"In the coverage check the page {title} drew very many claims; "
                                  "maybe split it or narrow its scope."})
    return found


def plan_warnings(
    plan: dict[str, Any],
    *,
    group_ids: set[str] | None,
    coverage: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    """Every warning about ``plan``. ``group_ids`` = the groups file's ids (``None`` when there
    is no groups file); ``coverage`` = ``views.coverage_view`` (or ``None``)."""

    return [
        *_budget(plan),
        *_label_like_page(plan),
        *_same_titles(plan),
        *_empty_categories(plan),
        *_lonely_kinds(plan),
        *_pages_without_examples(plan, group_ids),
        *_coverage(plan, coverage),
    ]
