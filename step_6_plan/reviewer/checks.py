"""Warnings about a valid plan: things the C7 rules allow but the owner probably wants to fix.

Each warning is ``{code, ids, text}`` with a Hebrew sentence that names the items by title and
says what to do. They are advice; nothing is refused because of them.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Mapping
from typing import Any

from step_6_plan.model import PLAN_CONFIG, iter_nodes

_PARENTHESIZED = re.compile(r"\([^)]*\)")
_NOT_A_LETTER = re.compile(r"[^\w\s]")


def _core(title: str) -> str:
    """A title without parenthesized remarks and punctuation, spaces collapsed."""

    return " ".join(_NOT_A_LETTER.sub(" ", _PARENTHESIZED.sub(" ", title)).split())


def _quote(title: str) -> str:
    return f"„{title}”"


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
                    "text": f"התווית {_quote(label['title_he'])} חופפת לדף "
                            f"{_quote(page['title_he'])}: אותו נושא יופיע בשני מקומות. אפשר "
                            "למחוק את התווית, למזג אותה לתווית אחרת או לחדד את התיאור שלה.",
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
         "text": f"{len(ids)} פריטים נקראים {_quote(title)}: קשה להבדיל ביניהם בניווט ובשיוך."}
        for title, ids in by_title.items() if len(ids) > 1
    ]


def _empty_categories(plan: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"code": "empty_category", "ids": [node["id"]],
         "text": f"בקטגוריה {_quote(node['title_he'])} אין דפים; האתר ידלג עליה. העבירו "
                 "אליה דף או מחקו אותה."}
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
         "text": f"לסוג התוויות {kind} יש תווית אחת בלבד "
                 f"({_quote(plan['labels'][ids[0]]['title_he'])}). אולי היא שייכת לסוג אחר."}
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
                "text": f"לדף {_quote(page['title_he'])} אין קבוצות טענות לדוגמה"
                        + ("" if not examples else " שקיימות בקובץ הקבוצות")
                        + ": קשה לבדוק אם התחום שלו אומר את מה שהתכוונתם.",
            })
    return found


def _budget(plan: dict[str, Any]) -> list[dict[str, Any]]:
    left = PLAN_CONFIG["max_pages"] - len(plan["pages"])
    if left > 0:
        return []
    return [{"code": "page_budget", "ids": [],
             "text": f"התוכנית הגיעה למספר הדפים המרבי ({PLAN_CONFIG['max_pages']}): דף חדש או "
                     "פיצול דורשים קודם מיזוג או מחיקה של דף אחר."}]


def _coverage(plan: dict[str, Any], coverage: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    if coverage is None:
        return []
    found = []
    for page_id, numbers in coverage["pages"].items():
        title = _quote(plan["pages"][page_id]["title_he"])
        if numbers["small"]:
            found.append({"code": "small_page", "ids": [page_id],
                          "text": f"בבדיקת הכיסוי הדף {title} משך מעט מאוד טענות; אולי כדאי "
                                  "למזג אותו לדף שכן או להרחיב את התחום שלו."})
        if numbers["large"]:
            found.append({"code": "large_page", "ids": [page_id],
                          "text": f"בבדיקת הכיסוי הדף {title} משך הרבה מאוד טענות; אולי כדאי "
                                  "לפצל אותו או לצמצם את התחום שלו."})
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
