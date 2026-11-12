"""mkdocs hook: ship the markdown twins of the pages (see ``step_9_site/agent_pages.py``).

Step 9 writes the twins under ``docs/<extra.agent_markdown.dir>/`` mirroring the built site
(``<dir>/georgia/index.md``) and keeps that folder out of the build with ``exclude_docs``, since
mkdocs would turn a ``.md`` file into an HTML page. This hook

- marks each page that has a twin (``page.meta["markdown_twin"]`` = the twin's file name), so the
  theme can add ``<link rel="alternate" type="text/markdown">`` to its head, and
- after the build copies the twins into the site, byte for byte, so they are never indexed by
  the search (which only knows pages).

Loaded by mkdocs from its path (``hooks:`` in mkdocs.yml), so it imports nothing from this
project; its settings come from ``extra.agent_markdown`` (written by step 9 from config.json).
"""

from __future__ import annotations

import shutil
from pathlib import Path

from mkdocs.config.defaults import MkDocsConfig
from mkdocs.structure.files import Files
from mkdocs.structure.pages import Page


def _twins_dir(config: MkDocsConfig) -> Path:
    return Path(config.docs_dir) / config.extra["agent_markdown"]["dir"]


def on_page_markdown(markdown: str, page: Page, config: MkDocsConfig, files: Files) -> str:
    twin_file = config.extra["agent_markdown"]["twin_file"]
    if (_twins_dir(config) / page.url / twin_file).is_file():
        page.meta["markdown_twin"] = twin_file
    return markdown


def on_post_build(config: MkDocsConfig) -> None:
    twins = _twins_dir(config)
    if not twins.is_dir():
        return
    for source in sorted(twins.rglob("*")):
        if source.is_file():
            target = Path(config.site_dir) / source.relative_to(twins)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
