"""Step 9: publish the wiki: ``docs/`` and ``mkdocs.yml`` from the plan (C7) and the pages (C9).

Reads the effective plan, the effective written pages and the effective claim groups (C6, for
the references and for the check that no page cites a group withheld by
``step_7_write_pages.withhold``), then writes:

- ``docs/<page_id>.md``: each planned page that has a written page, OKF front matter (hidden by
  mkdocs) + body with its wiki links resolved and its references (group id, when, how many
  people took part) rendered as footnotes from the groups;
- ``docs/<labels_dir>/<label_id>.md``: one page per label that a published page carries;
- ``docs/index.md``: the home page, the plan's tree with each page's one-line description;
- ``docs/about.md`` ("how this wiki was made", linked only from the home page) and
  ``docs/terms.md`` (the terms, linked from the banner and the footer); neither is in the nav;
- ``docs/<agent_markdown.dir>/…/index.md`` (a markdown twin of every content page, shipped by the
  hook ``agent_hook.py``) and ``docs/llms.txt``: the site for AI agents (``agent_pages.py``);
- a redirect stub at the old id of every merged page / label;
- ``docs/wiki_index.json``: the MCP index (contract C11);
- ``mkdocs.yml``: the navigation in the plan's order plus the site settings of ``config.json``.

The step owns ``docs/``: files it did not write are removed, except the top-level entries in
``site.keep``. A file whose content is unchanged is not rewritten, so a re-run on unchanged
inputs changes nothing. Problems (a planned page nobody wrote, a link to a page that is not
published, a label nobody carries...) are logged and returned under ``issues``; a page citing a
withheld group (``withheld_reference``) is logged as an error: re-run step 7 before publishing.
"""

from __future__ import annotations

import argparse
import copy
import json
import logging
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import yaml

from step_9_site import agent_hook, hebrew_search
from step_9_site.agent_pages import AgentPages
from step_7_write_pages.withhold import withheld_ids
from step_9_site.content import INFO_FILES, WITHHELD_REFERENCE, NavEntry, Site
from step_9_site.index import index_text
from utils.config import PROJECT_ROOT, section
from utils.logging_setup import setup_step_logging
from utils.paths import (
    DOCS_DIR,
    MKDOCS_PATH,
    STEP_9,
    WIKI_INDEX_FILE,
    resolve_aggregated_path,
    resolve_plan_path,
    resolve_wiki_pages_path,
)
from utils.rtl import HEBREW_CSS

logger = logging.getLogger(__name__)
SITE_CONFIG = section("site")
AGENT_CONFIG = SITE_CONFIG["agent_markdown"]
SEARCH_CONFIG = section("mcp")["search"]


def _config_path(target: Path, mkdocs_path: Path) -> str:
    """``target`` as mkdocs.yml should name it: relative to its folder when inside it."""
    try:
        return target.resolve().relative_to(mkdocs_path.resolve().parent).as_posix()
    except ValueError:
        return str(target.resolve())


def build_config(
    nav: list[NavEntry], *, docs_dir: Path, mkdocs_path: Path, not_in_nav: Sequence[str] = ()
) -> dict[str, Any]:
    """The mkdocs.yml content: ``site.mkdocs`` of config.json plus the computed keys."""
    base = copy.deepcopy(SITE_CONFIG["mkdocs"])
    config: dict[str, Any] = {
        "site_name": base["site_name"],
        "site_url": SITE_CONFIG["url"],
        "site_description": base["site_description"],
        "docs_dir": _config_path(docs_dir, mkdocs_path),
        "theme": {
            **base["theme"],
            "custom_dir": _config_path(PROJECT_ROOT / SITE_CONFIG["custom_dir"], mkdocs_path),
        },
        "plugins": base["plugins"],
        "markdown_extensions": base["markdown_extensions"],
        "extra": {
            **base.get("extra", {}),
            "coffee_text": SITE_CONFIG["coffee_text"],
            "coffee_link_text": SITE_CONFIG["coffee_link_text"],
            "coffee_url": SITE_CONFIG["coffee_url"],
            "disclaimer": SITE_CONFIG["disclaimer"],
            "refs_toggle": SITE_CONFIG["refs_toggle"],
            "terms_path": SITE_CONFIG["terms"]["file"].removesuffix(".md") + "/",
            "terms_footer_text": SITE_CONFIG["terms"]["footer_link_text"],
            "agent_markdown": {key: AGENT_CONFIG[key] for key in ("dir", "twin_file")},
        },
        "extra_css": [HEBREW_CSS],
        "exclude_docs": f"/{AGENT_CONFIG['dir']}/",
        "hooks": [_config_path(Path(agent_hook.__file__), mkdocs_path)],
    }
    search = SITE_CONFIG["hebrew_search"]
    if search["enabled"]:
        config["hooks"].append(_config_path(Path(hebrew_search.__file__), mkdocs_path))
        for plugin in config["plugins"]:
            if isinstance(plugin, dict) and "search" in plugin:
                plugin["search"]["fields"] = {search["field"]: {"boost": search["boost"]}}
        config["extra"]["hebrew_search"] = {
            "field": search["field"],
            "prefixes": SEARCH_CONFIG["hebrew_prefixes"],
            "min_stem_length": SEARCH_CONFIG["min_stem_length"],
        }
    if not_in_nav:
        config["not_in_nav"] = "\n".join(f"/{path}" for path in not_in_nav)
    config["nav"] = nav
    return config


def _write_if_changed(path: Path, text: str) -> bool:
    if path.is_file() and path.read_text(encoding="utf-8") == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return True


def _check_docs_dir(docs_dir: Path) -> None:
    """Refuse a folder whose cleanup would delete the project (the step owns ``docs_dir``)."""
    resolved = docs_dir.resolve()
    if resolved == PROJECT_ROOT or resolved in PROJECT_ROOT.parents or (resolved / ".git").exists():
        raise ValueError(f"{docs_dir} is not a docs folder step 9 may own (it holds the project)")


def sync_docs(docs_dir: Path, files: dict[str, str]) -> tuple[list[str], list[str]]:
    """Make ``docs_dir`` hold exactly ``files`` (+ ``site.keep``); return (written, removed)."""
    _check_docs_dir(docs_dir)
    keep = set(SITE_CONFIG["keep"])
    written = [rel for rel, text in files.items() if _write_if_changed(docs_dir / rel, text)]
    removed = []
    for path in sorted(docs_dir.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        rel = path.relative_to(docs_dir)
        if rel.parts[0] in keep or rel.as_posix() in files:
            continue
        if path.is_dir():
            if not any(path.iterdir()):
                path.rmdir()
        else:
            path.unlink()
            removed.append(rel.as_posix())
    return sorted(written), sorted(removed)


def _load(path: Path, what: str) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"step 9 needs the {what} at {path}; run the earlier steps first")
    return json.loads(path.read_text(encoding="utf-8"))


def run(
    *,
    plan_path: Path | None = None,
    pages_path: Path | None = None,
    groups_path: Path | None = None,
    docs_dir: Path = DOCS_DIR,
    mkdocs_path: Path = MKDOCS_PATH,
) -> dict[str, Any]:
    """Build the site into ``docs_dir`` + ``mkdocs_path``; nothing is written if an input fails.

    Returns ``published`` (page count), ``labels`` (label pages), ``redirects``, ``written`` /
    ``removed`` (docs-relative paths; ``mkdocs.yml`` by its file name), ``issues`` and
    ``issue_counts``.
    """
    plan = _load(plan_path or resolve_plan_path(), "plan (step 6)")
    pages = _load(pages_path or resolve_wiki_pages_path(), "written pages (step 7)")["pages"]
    groups = _load(groups_path or resolve_aggregated_path(), "claim groups (step 5)")["groups"]
    withheld = withheld_ids(groups)
    site = Site(plan, pages, groups, withheld)
    files = {**site.files, **AgentPages(site).files(), WIKI_INDEX_FILE: index_text(site, docs_dir / WIKI_INDEX_FILE)}
    config = build_config(
        site.nav,
        docs_dir=docs_dir,
        mkdocs_path=mkdocs_path,
        not_in_nav=[*site.redirect_files, *INFO_FILES],
    )
    config_text = yaml.safe_dump(config, allow_unicode=True, sort_keys=False)
    written, removed = sync_docs(docs_dir, files)
    if _write_if_changed(mkdocs_path, config_text):
        written.append(mkdocs_path.name)
    for issue in site.issues:
        level = logging.ERROR if issue["issue"] == WITHHELD_REFERENCE else logging.WARNING
        logger.log(level, f"{issue['issue']}: page {issue['page']!r} target {issue['target']!r}")
    result = {
        "published": len(site.order),
        "labels": len(site.labels),
        "redirects": len(site.redirect_files),
        "written": written,
        "removed": removed,
        "issues": site.issues,
        "issue_counts": dict(Counter(issue["issue"] for issue in site.issues)),
        "docs_dir": str(docs_dir),
        "mkdocs_path": str(mkdocs_path),
    }
    logger.info(
        f"{result['published']} pages, {result['labels']} label pages, {result['redirects']} "
        f"redirects -> {docs_dir}: {len(written)} files written, {len(removed)} removed; "
        f"issues: {result['issue_counts'] or 'none'}"
    )
    return result


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Step 9: build docs/ and mkdocs.yml.")
    parser.add_argument("--docs-dir", type=Path, default=DOCS_DIR, help="the folder step 9 owns")
    parser.add_argument("--mkdocs-file", type=Path, default=MKDOCS_PATH, help="mkdocs.yml to write")
    args = parser.parse_args(argv)
    setup_step_logging(STEP_9)
    run(docs_dir=args.docs_dir, mkdocs_path=args.mkdocs_file)


if __name__ == "__main__":
    main()
