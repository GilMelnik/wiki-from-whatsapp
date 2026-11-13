"""What the review reads, and the page fingerprints its suggestions keep.

The review reads the EFFECTIVE pages (C9; the owner's edits included) and, as each page's
evidence, the very group lines step 7's writer read for that page: step 7's own ``Corpus``
(effective groups, plan and assignments) builds the records and ``group_line`` renders them
(id, role, stance, support, last backed, disputed, when said, entities, contradictions, claim
text; nothing about people). The page body is sent without its footnote definitions: the
group lines say more (counts, dates) than a definition does.

Fingerprints (contract C14): one per page (its title and effective body, language edits not
counted: ``page_fingerprint``); the review passes
build theirs on it (``step_8_review.parts``). A suggestion stores the fingerprints of its pages
when it is raised, so a later run (and the suggestions store) can tell that a page changed or
left the wiki since (``page_marks``).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from step_6_plan.model import PLAN_CONFIG, iter_nodes, retired_ids
from step_7_write_pages.checks import strip_definitions
from step_7_write_pages.inputs import (
    PAGES_CONFIG,
    Corpus,
    Record,
    hebrew_month,
    input_fingerprint,
    page_state,
    writer_fingerprint,
)
from utils.config import LLM_CONFIG, section
from utils.json_io import read_json_if_present
from utils.paths import WIKI_PAGES, Artifact
from utils.reconcile import fingerprint

REVIEW_CONFIG = section("wiki_review")
CHANGED, GONE = "changed", "gone"
REVIEW_FINGERPRINT = "review_fingerprint"


def effective_pages(artifact: Artifact = WIKI_PAGES) -> tuple[dict[str, Any], Path]:
    """The pages as the site will publish them (edited copy if present) and their file."""
    for path in (artifact.edited, artifact.original):
        payload = read_json_if_present(path)
        if payload is not None:
            return payload, path
    raise FileNotFoundError(f"step 8 needs the wiki pages at {artifact.original}; run step 7 first")


def page_fingerprint(page: Mapping[str, Any]) -> str:
    """What a reader of the page sees: its title and its body. A page that only the language
    edit changed since (``language_edits.review_fingerprint``, written by
    ``step_8_review.proofread``: ``after`` the pass, it ``counts_as`` the page before it)
    keeps the fingerprint it had: proofreading never makes a review pass due or marks a
    suggestion's page as changed. Any later change of the page breaks the match."""
    seen = fingerprint({"title": page["front_matter"].get("title", ""), "body": page["body_md"]})
    carried = (page.get("language_edits") or {}).get(REVIEW_FINGERPRINT) or {}
    return carried.get("counts_as", seen) if carried.get("after") == seen else seen


def page_marks(raised_on: Mapping[str, str], current: Mapping[str, str]) -> dict[str, str]:
    """``{page_id: "changed" | "gone"}`` for the pages of a suggestion that changed or left
    the wiki since it was raised (``raised_on``: their fingerprints then)."""
    marks = {}
    for page_id, old in raised_on.items():
        if page_id not in current:
            marks[page_id] = GONE
        elif current[page_id] != old:
            marks[page_id] = CHANGED
    return marks


@dataclass(frozen=True)
class ReviewPage:
    """One page as the reviewer reads it."""

    page_id: str
    title: str
    body: str                     # with its citation marks and links, without definitions
    records: tuple[Record, ...]   # the group lines' records, C6 order

    @property
    def group_ids(self) -> set[str]:
        return {record["id"] for record in self.records}


class ReviewInput:
    """The effective pages with their evidence, and the plan they belong to."""

    def __init__(self, corpus: Corpus, pages: Mapping[str, Any], pages_path: Path) -> None:
        self.corpus = corpus
        self.plan = corpus.plan
        self.all_pages = dict(pages)
        self.pages_path = pages_path
        catalogue = corpus.catalogue()
        self.pages: dict[str, ReviewPage] = {}
        for page_id in self.plan["pages"]:
            page = self.all_pages.get(page_id)
            context = corpus.context(page_id, catalogue) if page is not None else None
            if context is not None:
                self.pages[page_id] = ReviewPage(page_id, page["front_matter"].get("title", ""),
                                                 strip_definitions(page["body_md"]),
                                                 context.records)

    @classmethod
    def load(cls, pages_artifact: Artifact = WIKI_PAGES, aggregated_path: Path | None = None,
             plan_path: Path | None = None, assignments_path: Path | None = None) -> ReviewInput:
        payload, path = effective_pages(pages_artifact)
        corpus = Corpus.load(aggregated_path, plan_path, assignments_path)
        return cls(corpus, payload.get("pages") or {}, path)

    @property
    def sources(self) -> tuple[Path, ...]:
        """Every file the review reads (for the "an input is newer" check)."""
        return (*self.corpus.sources, self.pages_path)

    def page_fingerprints(self) -> dict[str, str]:
        return {page_id: page_fingerprint(self.all_pages[page_id]) for page_id in self.pages}

    def plan_block(self) -> str:
        """The plan as the reviewer reads it: the tree (categories, then pages with their
        description and scope), the labels and the ids that may not be reused."""
        plan = self.plan
        lines = []
        for node, ancestors in iter_nodes(plan["tree"]):
            indent = "  " * len(ancestors)
            description = " ".join(node.get("description", "").split())
            page = plan["pages"].get(node["id"])
            if page is None:
                lines.append(f"{indent}- category {node['id']} | {node['title_he']} | {description}")
            else:
                scope = " ".join(page["scope"].split())
                lines.append(f"{indent}- page {node['id']} | {node['title_he']} | {description} "
                             f"| scope: {scope}")
        labels = [f"{lid} | {lab['title_he']} | kind {lab['kind']} | "
                  f"{' '.join(lab['description'].split())}" for lid, lab in plan["labels"].items()]
        retired = ", ".join(sorted(retired_ids(plan))) or "none"
        return "\n".join([
            f'<plan pages="{len(plan["pages"])}" max_pages="{PLAN_CONFIG["max_pages"]}" '
            f'page_depth="{PLAN_CONFIG["min_depth"]}-{PLAN_CONFIG["max_depth"]}">',
            *lines, "</plan>", "<labels>", *labels, "</labels>",
            f"<retired_ids>{retired}</retired_ids>",
            f"<as_of>{self.corpus.as_of} ({hebrew_month(self.corpus.as_of)})</as_of>"
            if self.corpus.as_of else "<as_of>unknown</as_of>",
        ])

    def outdated_pages(self) -> list[str]:
        """Pages whose stored ``input_fingerprint`` no longer matches their groups and plan
        fields (step 7 has not caught up with an upstream change)."""
        model = LLM_CONFIG["stages"][PAGES_CONFIG["write"]["stage"]]["model"]
        writer = writer_fingerprint(model)
        catalogue = self.corpus.catalogue()
        outdated = []
        for page_id in self.pages:
            context = self.corpus.context(page_id, catalogue)
            if input_fingerprint(page_state(context, writer)) != self.all_pages[page_id].get(
                    "input_fingerprint"):
                outdated.append(page_id)
        return outdated

    def warn_about_gaps(self, logger: logging.Logger) -> dict[str, list[str]]:
        """Log (and return) planned pages that have no text and pages out of date."""
        unwritten = [pid for pid in self.plan["pages"] if pid not in self.pages]
        outdated = self.outdated_pages()
        if unwritten:
            logger.warning(f"{len(unwritten)} planned pages have no text yet and are not "
                           f"reviewed: {unwritten}")
        if outdated:
            logger.warning(f"{len(outdated)} pages are older than their groups or plan fields "
                           f"(run step 7 first for a review of current pages): {outdated}")
        return {"unwritten": unwritten, "outdated": outdated}
