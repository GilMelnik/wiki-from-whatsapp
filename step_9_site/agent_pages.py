"""The site as plain markdown for AI agents that fetch it over HTTP: a twin of every page and ``llms.txt``.

An agent that reads the Material HTML pays for navigation, scripts and markup around the text.
Step 9 therefore also publishes, for every content page (home, wiki pages, label pages, the about
and terms pages), a markdown twin at ``<page url>index.md`` (``/georgia/`` -> ``/georgia/index.md``)
and an ``/llms.txt`` that lists them (https://llmstxt.org). The twin of a wiki page is built from
``Site.bodies``, the very text that ``wiki_index.json`` (and so the MCP ``get_page``) holds as
``text``; only two things are done to it: wiki links become absolute links to the twins, and the
reference marks (``[^g-…]``, meaningless without the list) are removed. Every twin starts with
the site's disclaimer, so an agent passes the warning on; the MCP ``get_page`` opens every page with
the same line (``wiki_index.json``).

mkdocs would turn a ``.md`` file in ``docs/`` into an HTML page, so the twins are written under
``docs/<site.agent_markdown.dir>/`` (left out of the build with ``exclude_docs``) and the mkdocs
hook ``step_9_site/agent_hook.py`` copies that folder into the built site. ``llms.txt`` is not
markdown for mkdocs: it sits in ``docs/`` and is copied as it is.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urljoin

from step_7_write_pages.checks import CITATION
from step_9_site.content import ABOUT_CONFIG, ABOUT_FILE, HOME_FILE, TERMS_CONFIG, TERMS_FILE, Site, label_file, page_file
from step_9_site.info_pages import about_body, terms_body
from utils.config import section

SITE_CONFIG = section("site")
AGENT_CONFIG = SITE_CONFIG["agent_markdown"]
LLMS_CONFIG = AGENT_CONFIG["llms"]
TWIN_FILE = AGENT_CONFIG["twin_file"]
LLMS_FILE = AGENT_CONFIG["llms_file"]
MCP_ROUTE = section("mcp")["route"]

_MARKS = re.compile(rf"[ \t]*(?:{CITATION.pattern})+")
_ADMONITION = re.compile(r'^(?:!!!|\?\?\?\+?)\s+[\w-]+(?:\s+"([^"]*)")?\s*$')
_INDENT = "    "


def _page_path(docs_file: str) -> str:
    """Site-relative path of the HTML page built from a docs file: ``georgia/``, ``""`` for home."""
    return "" if docs_file == HOME_FILE else docs_file.removesuffix(".md") + "/"


def page_url(docs_file: str) -> str:
    """Absolute URL of the HTML page built from a docs file (``georgia.md``, ``index.md``)."""
    return urljoin(SITE_CONFIG["url"], _page_path(docs_file))


def twin_url(docs_file: str) -> str:
    """Absolute URL of the markdown twin of a docs file's page."""
    return page_url(docs_file) + TWIN_FILE


def twin_docs_path(docs_file: str) -> str:
    """Where the twin of a docs file is written, relative to ``docs/``."""
    return f"{AGENT_CONFIG['dir']}/{_page_path(docs_file)}{TWIN_FILE}"


def disclaimer_line() -> str:
    """The site's disclaimer as one markdown line, linking the terms twin: every twin opens with
    it, ``llms.txt`` carries it and ``wiki_index.json`` hands it to the MCP ``get_page``."""
    return AGENT_CONFIG["disclaimer"].format(terms_url=twin_url(TERMS_FILE), **SITE_CONFIG["disclaimer"])


def strip_marks(body: str) -> str:
    """``body`` without its reference marks (and the space before them)."""

    def drop(match: re.Match[str]) -> str:
        following = match.string[match.end() : match.end() + 1]
        return "" if not following or following in " \t\n.,;:!?)]" else " "

    return _MARKS.sub(drop, body)


def plain_admonitions(text: str) -> str:
    """``text`` with Material admonitions (``!!! note "Title"`` + indented block) as block quotes."""
    lines, out, in_block = text.splitlines(), [], False
    for number, line in enumerate(lines):
        match = _ADMONITION.match(line)
        if match:
            in_block = True
            if match.group(1):
                out.append(f"> **{match.group(1)}**")
        elif in_block and line.startswith(_INDENT):
            out.append(f"> {line[len(_INDENT):]}")
        elif in_block and not line.strip() and lines[number + 1 : number + 2] and lines[number + 1].startswith(_INDENT):
            out.append(">")
        else:
            in_block = False
            out.append(line)
    return "\n".join(out) + "\n"


class AgentPages:
    """The markdown twins and ``llms.txt`` of one :class:`Site`."""

    def __init__(self, site: Site) -> None:
        self.site = site

    def files(self) -> dict[str, str]:
        """``{docs-relative path: text}``: every twin under the agent folder, plus ``llms.txt``."""
        site = self.site
        twins = {
            HOME_FILE: self._home(),
            ABOUT_FILE: self._plain(ABOUT_CONFIG["title"], ABOUT_FILE, about_body()),
            TERMS_FILE: self._plain(TERMS_CONFIG["title"], TERMS_FILE, terms_body()),
            **{page_file(page_id): self._wiki_page(page_id) for page_id in site.order},
            **{label_file(label_id): self._label_page(label_id) for label_id in site.labels},
        }
        files = {twin_docs_path(docs_file): text for docs_file, text in twins.items()}
        files[LLMS_FILE] = self._llms()
        return files

    # --------------------------------------------------------------------- pieces

    def _header(self, title: str, docs_file: str) -> list[str]:
        """Title, the disclaimer in one line, the address of the HTML page."""
        return [
            f"# {title}",
            "",
            f"> {disclaimer_line()}",
            "",
            AGENT_CONFIG["page_line"].format(url=page_url(docs_file)),
            "",
        ]

    def _item(self, page_id: str) -> str:
        site = self.site
        return f"- [{site.title(page_id)}]({twin_url(page_file(page_id))}): {site.description(page_id)}"

    def _plain(self, title: str, docs_file: str, body: str) -> str:
        return "\n".join([*self._header(title, docs_file), plain_admonitions(body).strip(), ""])

    # ---------------------------------------------------------------------- pages

    def _wiki_page(self, page_id: str) -> str:
        site = self.site
        front_matter = site.pages[page_id]["front_matter"]
        body = strip_marks(site.resolve_links(page_id, twin_url, report=False)).strip()
        return "\n".join(
            [*self._header(site.title(page_id), page_file(page_id)), body, "", "---", "", *self._about_page(front_matter), ""]
        )

    @staticmethod
    def _about_page(front_matter: dict[str, Any]) -> list[str]:
        """When the page was generated and how many claims it rests on (counts, no text)."""
        claims = front_matter["claims"]
        line = [
            AGENT_CONFIG["updated_line"].format(updated=front_matter["generated"]["at"][:10]),
            AGENT_CONFIG["claims_line"].format(**claims),
        ]
        if claims["first_claim_at"] and claims["last_claim_at"]:
            line.append(
                AGENT_CONFIG["claims_dates_line"].format(first=claims["first_claim_at"], last=claims["last_claim_at"])
            )
        return [" ".join(line)]

    def _label_page(self, label_id: str) -> str:
        site = self.site
        label = site.plan["labels"][label_id]
        items = [self._item(page_id) for page_id in site.order if label_id in site.page_labels[page_id]]
        body = [label["description"], "", f"## {SITE_CONFIG['label_pages_heading']}", "", *items]
        return "\n".join([*self._header(label["title_he"], label_file(label_id)), *body, ""])

    def _contents(self) -> list[str]:
        """The pages by the plan's sections, then the labels (optional), then the info pages."""
        site = self.site
        lines = site.tree_lines(site.plan["tree"], 2, self._item)
        kind_titles = SITE_CONFIG["label_kind_titles"]
        by_kind: dict[str, list[str]] = {}
        for label_id in site.labels:
            label = site.plan["labels"][label_id]
            item = f"- [{label['title_he']}]({twin_url(label_file(label_id))}): {label['description']}"
            by_kind.setdefault(label["kind"], []).append(item)
        if by_kind:
            lines += ["", f"## {LLMS_CONFIG['optional_heading']}"]
            for kind, items in by_kind.items():
                lines += ["", f"### {kind_titles.get(kind, kind)}", "", *items]
        about = [(ABOUT_CONFIG["title"], ABOUT_FILE), (TERMS_CONFIG["title"], TERMS_FILE)]
        info = [f"- [{title}]({twin_url(docs_file)})" for title, docs_file in about]
        return [*lines, "", f"## {LLMS_CONFIG['about_heading']}", "", *info]

    def _home(self) -> str:
        header = self._header(SITE_CONFIG["mkdocs"]["site_name"], HOME_FILE)
        return "\n".join([*header, self.site.home_intro(twin_url(ABOUT_FILE)), *self._contents(), ""])

    def _llms(self) -> str:
        """``llms.txt``: title, summary block quote, the MCP endpoint, the pages by section."""
        mcp_url = urljoin(SITE_CONFIG["url"], MCP_ROUTE.lstrip("/"))
        lines = [
            f"# {SITE_CONFIG['mkdocs']['site_name']}",
            "",
            f"> {LLMS_CONFIG['summary']}",
            ">",
            f"> {disclaimer_line()}",
            "",
            LLMS_CONFIG["mcp_line"].format(mcp_url=mcp_url),
            *self._contents(),
        ]
        return "\n".join(lines) + "\n"
