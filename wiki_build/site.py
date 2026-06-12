"""Stage F: build the MkDocs site config and wire approved pages.

Generates ``mkdocs.yml`` (Material theme, Hebrew, RTL, search) with a ``nav``
derived from the taxonomy, including only pages that actually exist in the
docs directory. Approved drafts are expected in ``docs/``; the optional
``seed_from_drafts`` flag copies ``drafts/`` into ``docs/`` for a quick preview
(bypassing the human review gate - use only for local previews).
"""

from __future__ import annotations

import shutil
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml

from wiki_build.taxonomy import CATEGORIES, all_pages

DEFAULT_DOCS_DIR = Path("docs")
DEFAULT_DRAFTS_DIR = Path("drafts")
DEFAULT_CONFIG_PATH = Path("mkdocs.yml")


def _build_nav(docs_dir: Path) -> list[Any]:
    def exists(slug: str) -> bool:
        return (docs_dir / f"{slug}.md").exists()

    nav: list[Any] = []
    if (docs_dir / "index.md").exists():
        nav.append({"בית": "index.md"})

    pages_by_category: dict[str, list[dict[str, str]]] = defaultdict(list)
    for page in all_pages():
        if exists(page.slug):
            pages_by_category[page.category].append({page.title_he: f"{page.slug}.md"})

    for category_id, category_title in CATEGORIES.items():
        entries = pages_by_category.get(category_id)
        if entries:
            nav.append({category_title: entries})
    return nav


def build_config(docs_dir: Path) -> dict[str, Any]:
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
        "plugins": ["search"],
        "markdown_extensions": [
            "admonition",
            "pymdownx.details",
            "pymdownx.superfences",
            "attr_list",
            "tables",
            "toc",
        ],
        "nav": _build_nav(docs_dir),
    }


def run(
    docs_dir: Path | str = DEFAULT_DOCS_DIR,
    drafts_dir: Path | str = DEFAULT_DRAFTS_DIR,
    config_path: Path | str = DEFAULT_CONFIG_PATH,
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
            "# ויקי פונדקאות לגייז\n\nברוכים הבאים. בחרו נושא מהתפריט.\n",
            encoding="utf-8",
        )

    config = build_config(docs)
    with Path(config_path).open("w", encoding="utf-8") as f:
        yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False)

    page_count = len(list(docs.glob("*.md")))
    return {
        "config_path": str(config_path),
        "docs_dir": str(docs),
        "page_count": page_count,
        "nav_sections": len(config["nav"]),
    }


if __name__ == "__main__":
    meta = run()
    print(
        f"Wrote {meta['config_path']} with {meta['nav_sections']} nav sections "
        f"({meta['page_count']} pages in {meta['docs_dir']}/)."
    )
