"""What step 9 publishes, computed in memory from the plan (C7) and the written pages (C9).

``Site(plan, pages)`` decides which planned pages are published (those that have a written
page), builds the navigation from the plan's tree, renders every generated markdown file
(pages with their OKF front matter, label pages, the home page, redirect stubs for merged
ids) and collects the problems found on the way in ``issues``. Nothing here touches the disk.

Wiki links in page bodies are resolved here: ``[[page_id|text]]`` becomes a relative markdown
link to that page, ``[[label:label_id|text]]`` a link to the label's page; a link to a merged
id goes to the survivor; a link to anything not published becomes its plain text.
"""

from __future__ import annotations

from typing import Any

import yaml

from step_6_plan.model import check_plan, iter_nodes
from step_9_site.info_pages import about_body, terms_body
from utils.config import section
from utils.okf import render_page, validate_front_matter
from utils.wiki_links import WikiLink, replace_wiki_links

SITE_CONFIG = section("site")
PAGES_CONFIG = section("pages")
HOME_FILE = "index.md"
ABOUT_CONFIG = SITE_CONFIG["about"]
TERMS_CONFIG = SITE_CONFIG["terms"]
ABOUT_FILE = ABOUT_CONFIG["file"]
TERMS_FILE = TERMS_CONFIG["file"]
INFO_FILES = (ABOUT_FILE, TERMS_FILE)
NavEntry = dict[str, Any]
Issue = dict[str, str]


def page_file(page_id: str) -> str:
    """The docs-relative markdown file of a page (or of a page redirect stub)."""
    return f"{page_id}.md"


def label_file(label_id: str) -> str:
    """The docs-relative markdown file of a label page (or of a label redirect stub)."""
    return f"{SITE_CONFIG['labels_dir']}/{label_id}.md"


def _href(source: str, target: str) -> str:
    """Markdown link from docs file ``source`` to docs file ``target`` (both docs-relative)."""
    return "../" * source.count("/") + target


def _url(source: str, target: str) -> str:
    """Browser URL of ``target``'s built page, relative to ``source``'s (directory URLs)."""
    return "../" * (source.count("/") + 1) + target.removesuffix(".md") + "/"


def with_front_matter(meta: dict[str, Any], body: str) -> str:
    """A generated (non-wiki) page: a plain YAML front-matter block, then the body."""
    block = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False, width=10**9)
    return f"---\n{block}---\n\n{body.strip()}\n"


def _redirects(items: dict[str, dict[str, Any]]) -> dict[str, str]:
    """``{merged-away id: survivor id}`` from the ``merged_from`` lists of plan items."""
    return {old: item_id for item_id, item in items.items() for old in item.get("merged_from") or []}


class Site:
    """The published site for one plan + pages pair (see the module docstring)."""

    def __init__(self, plan: dict[str, Any], pages: dict[str, dict[str, Any]]) -> None:
        self.plan = check_plan(plan)
        unplanned = sorted(set(pages) - set(plan["pages"]))
        if unplanned:
            raise ValueError(
                f"written pages that are not in the plan: {unplanned}; re-run step 7 so its "
                "output follows the current plan"
            )
        self._check_pages(pages)
        self.pages = pages
        self.issues: list[Issue] = []
        tree_ids = [node["id"] for node, _ in iter_nodes(plan["tree"])]
        self.order = [node_id for node_id in tree_ids if node_id in pages]
        for page_id in tree_ids:
            if page_id in plan["pages"] and page_id not in pages:
                self._issue("page_not_written", page_id)
        self._page_redirects = _redirects(plan["pages"])
        self._label_redirects = _redirects(plan["labels"])
        self.page_labels = {page_id: self._resolve_labels(page_id) for page_id in self.order}
        carried = {label for labels in self.page_labels.values() for label in labels}
        self.labels = [label_id for label_id in plan["labels"] if label_id in carried]
        for label_id in plan["labels"]:
            if label_id not in carried:
                self._issue("label_not_carried", "", label_id)
        self.related = {page_id: self._resolve_related(page_id) for page_id in self.order}
        self.nav = self._nav()
        self.files = self._files()
        self.redirect_files = sorted(set(self.files) - self._content_files())

    @staticmethod
    def _check_pages(pages: dict[str, dict[str, Any]]) -> None:
        for reserved in (HOME_FILE, *INFO_FILES):
            if reserved.removesuffix(".md") in pages:
                raise ValueError(f"a page may not use the id of the generated page {reserved!r}")
        for page_id, page in pages.items():
            missing = {"front_matter", "body_md"} - page.keys()
            if missing:
                raise ValueError(f"written page {page_id!r} has no {sorted(missing)}")
            try:
                validate_front_matter(page["front_matter"])
            except ValueError as error:
                raise ValueError(f"written page {page_id!r}: {error}") from error

    def _issue(self, kind: str, page: str, target: str = "") -> None:
        self.issues.append({"issue": kind, "page": page, "target": target})

    # ------------------------------------------------------------------ resolution

    def title(self, page_id: str) -> str:
        return self.pages[page_id]["front_matter"]["title"]

    def page_target(self, page_id: str) -> str | None:
        """The published page an id stands for (following merges), or ``None``."""
        target = self._page_redirects.get(page_id, page_id)
        return target if target in self.pages else None

    def _label_target(self, label_id: str) -> str | None:
        """The label page an id stands for (following merges), or ``None``."""
        target = self._label_redirects.get(label_id, label_id)
        return target if target in self.labels else None

    def _resolve_labels(self, page_id: str) -> list[str]:
        resolved: list[str] = []
        for entry in self.pages[page_id]["front_matter"].get("labels", []):
            label_id = self._label_redirects.get(entry["id"], entry["id"])
            if label_id not in self.plan["labels"]:
                self._issue("unknown_label", page_id, entry["id"])
            elif label_id not in resolved:
                resolved.append(label_id)
        return resolved

    def _resolve_related(self, page_id: str) -> list[dict[str, str]]:
        related = []
        for entry in self.pages[page_id]["front_matter"]["related"]:
            target = self.page_target(entry["slug"])
            if target is None:
                self._issue("related_not_published", page_id, entry["slug"])
            else:
                related.append({"id": target, "relation": entry["relation"]})
        return related

    def _resolve_links(self, page_id: str) -> str:
        source = page_file(page_id)

        def resolve(link: WikiLink) -> str:
            if link.kind == "page":
                target = self.page_target(link.target)
                found = target and (self.title(target), page_file(target))
            else:
                target = self._label_target(link.target)
                found = target and (self.plan["labels"][target]["title_he"], label_file(target))
            if not found:
                self._issue(f"broken_{link.kind}_link", page_id, link.target)
                planned = self.plan["pages" if link.kind == "page" else "labels"].get(link.target)
                return link.text or (planned["title_he"] if planned else link.target)
            if target != link.target:
                self._issue(f"merged_{link.kind}_link", page_id, link.target)
            title, file = found
            return f"[{link.text or title}]({_href(source, file)})"

        return replace_wiki_links(self.pages[page_id]["body_md"], resolve)

    # ------------------------------------------------------------------------ nav

    def _nav(self) -> list[NavEntry]:
        nav = [{SITE_CONFIG["home_nav_title"]: HOME_FILE}, *self._nav_nodes(self.plan["tree"])]
        kinds: dict[str, list[NavEntry]] = {}
        for label_id in self.labels:
            label = self.plan["labels"][label_id]
            kinds.setdefault(label["kind"], []).append({label["title_he"]: label_file(label_id)})
        if kinds:
            sections = [{self._kind_title(kind): entries} for kind, entries in kinds.items()]
            nav.append({SITE_CONFIG["labels_nav_title"]: sections})
        return nav

    def _nav_nodes(self, nodes: list[dict[str, Any]]) -> list[NavEntry]:
        entries: list[NavEntry] = []
        for node in nodes:
            if node["id"] in self.pages:
                entries.append({self.title(node["id"]): page_file(node["id"])})
            elif node["id"] not in self.plan["pages"]:
                children = self._nav_nodes(node["children"])
                if children:  # a category without published pages is left out
                    entries.append({node["title_he"]: children})
        return entries

    def _kind_title(self, kind: str) -> str:
        titles = SITE_CONFIG["label_kind_titles"]
        if kind not in titles:
            self._issue("untitled_label_kind", "", kind)
        return titles.get(kind, kind)

    # ---------------------------------------------------------------------- files

    def _content_files(self) -> set[str]:
        return {HOME_FILE, *INFO_FILES, *map(page_file, self.order), *map(label_file, self.labels)}

    def _files(self) -> dict[str, str]:
        files = {HOME_FILE: self._home(), **self._info_pages()}
        for page_id in self.order:
            front_matter = self.pages[page_id]["front_matter"]
            files[page_file(page_id)] = render_page(front_matter, self._resolve_links(page_id))
        for label_id in self.labels:
            files[label_file(label_id)] = self._label_page(label_id)
        for old, page_id in self._page_redirects.items():
            if page_id in self.pages:
                files[page_file(old)] = self._stub(page_file(old), page_file(page_id), self.title(page_id))
        for old, label_id in self._label_redirects.items():
            if label_id in self.labels:
                title = self.plan["labels"][label_id]["title_he"]
                files[label_file(old)] = self._stub(label_file(old), label_file(label_id), title)
        return files

    @staticmethod
    def _info_pages() -> dict[str, str]:
        """The about and terms pages: not wiki content, so they stay out of the search."""
        search = {"exclude": True}
        return {
            ABOUT_FILE: with_front_matter({"title": ABOUT_CONFIG["title"], "search": search}, about_body()),
            TERMS_FILE: with_front_matter({"title": TERMS_CONFIG["title"], "search": search}, terms_body()),
        }

    def _page_item(self, source: str, page_id: str) -> str:
        description = self.pages[page_id]["front_matter"]["description"]
        return f"- [{self.title(page_id)}]({_href(source, page_file(page_id))}): {description}"

    def _home(self) -> str:
        mkdocs = SITE_CONFIG["mkdocs"]
        meta = {
            "okf_version": PAGES_CONFIG["okf_version"],
            "title": mkdocs["site_name"],
            "description": mkdocs["site_description"],
        }
        about_link = f"[{ABOUT_CONFIG['home_link_text']}]({ABOUT_FILE})"
        lines = [SITE_CONFIG["home_intro"], "", about_link, *self._home_nodes(self.plan["tree"], 2)]
        return with_front_matter(meta, "\n".join(lines))

    def _home_nodes(self, nodes: list[dict[str, Any]], level: int) -> list[str]:
        lines: list[str] = []
        for node in nodes:
            if node["id"] in self.pages:
                lines.append(self._page_item(HOME_FILE, node["id"]))
            elif node["id"] not in self.plan["pages"]:
                inner = self._home_nodes(node["children"], level + 1)
                if inner:
                    heading = f"{'#' * min(level, 6)} {node['title_he']}"
                    lines += ["", heading, "", node["description"], "", *inner]
        return lines

    def _label_page(self, label_id: str) -> str:
        label = self.plan["labels"][label_id]
        source = label_file(label_id)
        items = [self._page_item(source, p) for p in self.order if label_id in self.page_labels[p]]
        body = "\n".join([label["description"], "", f"## {SITE_CONFIG['label_pages_heading']}", "", *items])
        return with_front_matter({"title": label["title_he"], "description": label["description"]}, body)

    @staticmethod
    def _stub(source: str, target: str, title: str) -> str:
        """A page for a merged-away id that sends the reader on to the survivor."""
        body = (
            f'<meta http-equiv="refresh" content="0; url={_url(source, target)}">\n\n'
            f"{SITE_CONFIG['redirect_text']} [{title}]({_href(source, target)})."
        )
        return with_front_matter({"title": title, "search": {"exclude": True}}, body)
