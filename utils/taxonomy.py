"""Seed taxonomy for the surrogacy wiki.

Defines the canonical set of wiki pages (topics), their Hebrew titles, slugs,
hierarchy, the keywords used for heuristic tagging and the offline mock
LLM provider, and user-reviewed ``SEARCH_FOCUS`` queries for background
research. The taxonomy is intentionally extensible: the LLM tagging step
may attach emergent topic ids that are not listed here, and those are collected
under the ``EMERGENT`` category during aggregation.

The data itself lives in the sibling ``taxonomy.json`` (categories, pages,
search_focus); this module loads it once and exposes the ``TopicPage`` type and
the accessor functions the rest of the pipeline uses.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

_DATA_PATH = Path(__file__).with_name("taxonomy.json")


@dataclass(frozen=True)
class TopicPage:
    """A single wiki page in the taxonomy."""

    id: str
    title_he: str
    category: str
    keywords: tuple[str, ...] = field(default_factory=tuple)
    parent: str | None = None

    @property
    def slug(self) -> str:
        return self.id


with _DATA_PATH.open(encoding="utf-8") as _f:
    _DATA = json.load(_f)

# Top-level categories (used to build the site navigation).
CATEGORIES: dict[str, str] = _DATA["categories"]

TAXONOMY: tuple[TopicPage, ...] = tuple(
    TopicPage(
        id=page["id"],
        title_he=page["title_he"],
        category=page["category"],
        keywords=tuple(page.get("keywords", ())),
        parent=page.get("parent"),
    )
    for page in _DATA["pages"]
)

_BY_ID: dict[str, TopicPage] = {page.id: page for page in TAXONOMY}

# User-reviewed background search queries (from wiki_plan_edited.json).
SEARCH_FOCUS: dict[str, str] = _DATA["search_focus"]


def search_focus_for(topic_id: str) -> str | None:
    return SEARCH_FOCUS.get(topic_id)


def resolve_search_focus(
    page_id: str,
    source_tags: list[str],
    *,
    llm_value: str | None = None,
) -> str:
    """Taxonomy term wins; else LLM suggestion for pages not in the seed list."""

    for topic_id in (page_id, *source_tags):
        sf = SEARCH_FOCUS.get(topic_id)
        if sf:
            return sf
    return str(llm_value or "").strip()


def all_pages() -> tuple[TopicPage, ...]:
    return TAXONOMY


def get_page(topic_id: str) -> TopicPage | None:
    return _BY_ID.get(topic_id)


def page_ids() -> list[str]:
    return [page.id for page in TAXONOMY]


def category_title(category_id: str) -> str:
    return CATEGORIES.get(category_id, category_id)


def taxonomy_tag_seed() -> str:
    """Topic id/title listing for classify/extract tagging (no search_focus)."""

    lines: list[str] = []
    for page in TAXONOMY:
        parent = f" (תת-נושא של {page.parent})" if page.parent else ""
        lines.append(f"- {page.id}: {page.title_he}{parent}")
    return "\n".join(lines)


def taxonomy_seed_block() -> str:
    """Compact seed listing for planning (includes search_focus when defined)."""

    lines: list[str] = []
    for page in TAXONOMY:
        parent = f" (תת-נושא של {page.parent})" if page.parent else ""
        sf = SEARCH_FOCUS.get(page.id)
        sf_part = f" | search_focus: {sf}" if sf else ""
        lines.append(f"- {page.id}: {page.title_he}{parent}{sf_part}")
    return "\n".join(lines)
