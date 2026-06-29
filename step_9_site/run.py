"""Step 8: build the MkDocs site config and wire approved pages.

Generates ``mkdocs.yml`` (Material theme, Hebrew, RTL, search) with a ``nav``
derived from ``data/wiki_plan.json`` when present, otherwise from the taxonomy
seed. Only pages that exist in ``docs/`` are included.
"""

from __future__ import annotations

import json
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml

from utils.paths import resolve_plan_path
from utils.rtl import HEBREW_CSS, wrap_rtl_markdown
from utils.taxonomy import CATEGORIES, all_pages, category_title

DEFAULT_DOCS_DIR = Path("docs")
DEFAULT_DRAFTS_DIR = Path("drafts")
DEFAULT_CONFIG_PATH = Path("mkdocs.yml")
DEFAULT_PLAN_PATH = Path("data/wiki_plan.json")


def effective_plan_path(plan_path: Path | str | None = None) -> Path:
    if plan_path is None:
        return resolve_plan_path()
    resolved = Path(plan_path)
    if resolved == DEFAULT_PLAN_PATH:
        return resolve_plan_path()
    return resolved


def _load_plan_pages(plan_path: Path) -> list[dict[str, str]] | None:
    if not plan_path.exists():
        return None
    with plan_path.open(encoding="utf-8") as f:
        data = json.load(f)
    pages = data.get("pages")
    if not isinstance(pages, list) or not pages:
        return None
    return [
        {
            "id": p["id"],
            "title": p.get("title", p["id"]),
            "category": p.get("category", "emergent"),
        }
        for p in pages
        if isinstance(p, dict) and p.get("id")
    ]


def _build_nav(docs_dir: Path, plan_path: Path | str = DEFAULT_PLAN_PATH) -> list[Any]:
    def exists(slug: str) -> bool:
        return (docs_dir / f"{slug}.md").exists()

    nav: list[Any] = []
    if (docs_dir / "index.md").exists():
        nav.append({"בית": "index.md"})

    plan_pages = _load_plan_pages(effective_plan_path(plan_path))
    pages_by_category: dict[str, list[dict[str, str]]] = defaultdict(list)

    if plan_pages:
        for page in plan_pages:
            if exists(page["id"]):
                cat_title = category_title(page["category"])
                pages_by_category[cat_title].append(
                    {page["title"]: f"{page['id']}.md"}
                )
        category_order = list(CATEGORIES.values()) + ["נושאים נוספים"]
        seen: set[str] = set()
        for cat_title in category_order:
            entries = pages_by_category.get(cat_title)
            if entries:
                nav.append({cat_title: entries})
                seen.add(cat_title)
        for cat_title, entries in pages_by_category.items():
            if cat_title not in seen and entries:
                nav.append({cat_title: entries})
    else:
        for page in all_pages():
            if exists(page.slug):
                pages_by_category[category_title(page.category)].append(
                    {page.title_he: f"{page.slug}.md"}
                )
        for category_id, cat_title in CATEGORIES.items():
            entries = pages_by_category.get(cat_title)
            if entries:
                nav.append({cat_title: entries})

    return nav


def build_config(
    docs_dir: Path,
    plan_path: Path | str = DEFAULT_PLAN_PATH,
) -> dict[str, Any]:
    return {
        "site_name": "ויקי פונדקאות לגייז",
        "site_description": "ידע קהילתי אנונימי על תהליכי פונדקאות",
        "docs_dir": str(docs_dir),
        "theme": {
            "name": "material",
            "language": "he",
            "direction": "rtl",
            "features": [
                "navigation.sections",
                "navigation.top",
                "navigation.indexes",
                "search.highlight",
                "search.suggest",
                "content.code.copy",
            ],
            "palette": [
                {
                    "scheme": "default",
                    "primary": "teal",
                    "accent": "teal",
                    "toggle": {
                        "icon": "material/weather-night",
                        "name": "עבור למצב כהה",
                    },
                },
                {
                    "scheme": "slate",
                    "primary": "teal",
                    "accent": "teal",
                    "toggle": {
                        "icon": "material/weather-sunny",
                        "name": "עבור למצב בהיר",
                    },
                },
            ],
        },
        "plugins": [{"search": {"lang": "he"}}],
        "markdown_extensions": [
            "admonition",
            "md_in_html",
            "pymdownx.details",
            "pymdownx.superfences",
            "attr_list",
            "tables",
            "toc",
        ],
        "extra_css": [HEBREW_CSS],
        "nav": _build_nav(docs_dir, plan_path),
    }


def run(
    docs_dir: Path | str = DEFAULT_DOCS_DIR,
    drafts_dir: Path | str = DEFAULT_DRAFTS_DIR,
    config_path: Path | str = DEFAULT_CONFIG_PATH,
    plan_path: Path | str = DEFAULT_PLAN_PATH,
    seed_from_drafts: bool = False,
) -> dict[str, Any]:
    docs = Path(docs_dir)
    docs.mkdir(parents=True, exist_ok=True)

    if seed_from_drafts:
        drafts = Path(drafts_dir)
        for md in drafts.glob("*.md"):
            shutil.copy2(md, docs / md.name)

    if not (docs / "index.md").exists():
        (docs / "index.md").write_text(
            wrap_rtl_markdown(
                "# ויקי פונדקאות לגייז\n\nברוכים הבאים. בחרו נושא מהתפריט."
            ),
            encoding="utf-8",
        )

    resolved_plan = effective_plan_path(plan_path)
    config = build_config(docs, resolved_plan)
    with Path(config_path).open("w", encoding="utf-8") as f:
        yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False)

    page_count = len(list(docs.glob("*.md")))
    return {
        "config_path": str(config_path),
        "docs_dir": str(docs),
        "page_count": page_count,
        "nav_sections": len(config["nav"]),
        "plan_nav": _load_plan_pages(resolved_plan) is not None,
    }
