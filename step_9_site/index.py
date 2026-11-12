"""``docs/wiki_index.json``: the search and graph index the MCP Worker reads (contract C11).

The shape is the Worker's (``worker/wiki_index.schema.json``; field sources in
``tasks/41-wiki-mcp/report.md``): the site's disclaimer line (the one the markdown twins open
with, which ``get_page`` puts before every page body), every plan label, then every published page in navigation
order with its front-matter metadata, its resolved labels / related pages / out-links (only
published pages) and its body as written, wiki links and ``[^g-…]`` marks kept, without
references (a definition stored by an older step 7 is left out; marks of merged groups follow
the group that holds them).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from step_9_site.agent_pages import disclaimer_line
from step_9_site.content import Site
from utils.config import section
from utils.wiki_links import find_wiki_links

MCP_CONFIG = section("mcp")
DEFAULT_STATUS = section("pages")["default_status"]
GENERATED_AT = "generated_at"


def _out_links(site: Site, page_id: str) -> list[str]:
    """Distinct published targets of the body's page links, first appearance first, not self."""
    targets = (site.page_target(link.target) for link in find_wiki_links(site.pages[page_id]["body_md"]) if link.kind == "page")
    return [target for target in dict.fromkeys(targets) if target is not None and target != page_id]


def _page_entry(site: Site, page_id: str) -> dict[str, Any]:
    front_matter = site.pages[page_id]["front_matter"]
    entry: dict[str, Any] = {
        "id": page_id,
        "url": f"{page_id}/",
        "title": front_matter["title"],
        "description": front_matter["description"],
    }
    if "summary" in front_matter:
        entry["summary"] = front_matter["summary"]
    entry.update(
        questions=front_matter.get("questions", []),
        labels=site.page_labels[page_id],
        path=front_matter["path"],
        aliases=front_matter["aliases"],
        entities=front_matter["entities"],
        related=site.related[page_id],
        out_links=_out_links(site, page_id),
        claims=front_matter["claims"],
        updated_at=front_matter["generated"]["at"],
    )
    if front_matter.get("status", DEFAULT_STATUS) != DEFAULT_STATUS:
        entry["status"] = front_matter["status"]
    entry["text"] = site.bodies[page_id]
    return entry


def build_index(site: Site, generated_at: str) -> dict[str, Any]:
    labels = {
        label_id: {"title": label["title_he"], "description": label["description"], "kind": label["kind"]}
        for label_id, label in site.plan["labels"].items()
    }
    return {
        "index_version": MCP_CONFIG["index_version"],
        GENERATED_AT: generated_at,
        "disclaimer": disclaimer_line(),
        "labels": labels,
        "pages": [_page_entry(site, page_id) for page_id in site.order],
    }


def index_text(site: Site, previous_path: Path) -> str:
    """The index as written (2-space JSON); ``generated_at`` is kept when nothing else changed."""
    index = build_index(site, datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    if previous_path.is_file():
        try:
            previous = json.loads(previous_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            previous = None
        if isinstance(previous, dict) and {**previous, GENERATED_AT: None} == {**index, GENERATED_AT: None}:
            index[GENERATED_AT] = previous[GENERATED_AT]
    return json.dumps(index, ensure_ascii=False, indent=2) + "\n"
