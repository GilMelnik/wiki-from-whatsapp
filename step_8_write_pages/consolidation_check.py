"""The three-page check of task 27b: a few pages written with the current prompt, side by side
with the stored ones, in a scratch data dir.

PAID (the ``write`` stage, Opus 5.5): about $1 for the three default pages on Sonnet 5.5, so
about $2 on Opus 5.5 at twice its price per token; $4 at most with a retry on each. Run it only against a private copy, never the canonical data:

    WIKI_DATA_DIR=<empty scratch dir> .venv/bin/python -m step_8_write_pages.consolidation_check \\
        --source <data dir that holds the current pages>

1. Copies what the claim records are joined from (the claims, the entities, the private audit
   and the manual contradiction pairs), what decides which claims are withheld (the
   same-complaint check's answers and the owner's "keep withheld" marks), the plan (step 6),
   the assignments (step 7) and the stored pages (step 8, "before"), original and edited files,
   from ``--source`` into the current data dir (``WIKI_DATA_DIR``); refuses when the two are the
   same folder.
2. Writes the pages (``pages.consolidation_check.pages``, or ``--pages``) in full with the
   current prompt (step 8's own ``run(only=…, rebuild=True, use_batch=False)``: one sync call
   per page, so the three pages are back at once; answers are cached in the scratch dir, so a
   re-run after a crash costs nothing). ``--compare-only`` skips this.
3. Logs per page, before / after: prose words, statements, marks, claims cited, years named
   in the prose, links; after only: points by kind, claims left out with their reason, and
   what the checks still found.
4. Writes ``<data dir>/step_8_write_pages/<pages.consolidation_check.report_file>``: both
   versions side by side, then the points with the text of every claim behind them, then the
   claims left out with their reason and text. It holds private text: open it locally, never
   commit or share it.
"""

from __future__ import annotations

import argparse
import html
import json
import logging
import os
import re
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from step_5_same_complaint.claim_records import (
    SHORT_ID_PREFIX,
    ClaimSources,
    load_claim_records,
)
from step_8_write_pages.checks import (
    CITATION,
    cited_ids,
    prose_words,
    statements,
    strip_definitions,
)
from step_8_write_pages.inputs import PAGES_CONFIG
from step_8_write_pages.run import run
from utils.config import DATA_DIR
from utils.llm_client import LLMClient
from utils.logging_setup import setup_step_logging
from utils.paths import (
    ASSIGNMENTS,
    AUDIT_PATH,
    CLAIMS,
    ENTITIES,
    MANUAL_CONTRADICTIONS_PATH,
    PLAN,
    SAME_COMPLAINT_PATH,
    SAME_COMPLAINT_VETOES_PATH,
    STEP_8,
    WIKI_PAGES,
    Artifact,
    resolve,
)
from utils.wiki_links import find_wiki_links, replace_wiki_links

CHECK_CONFIG = PAGES_CONFIG["consolidation_check"]
INPUTS = (
    *(path for artifact in (CLAIMS, ENTITIES, PLAN, ASSIGNMENTS, WIKI_PAGES)
      for path in (artifact.original, artifact.edited)),
    AUDIT_PATH, MANUAL_CONTRADICTIONS_PATH, SAME_COMPLAINT_PATH, SAME_COMPLAINT_VETOES_PATH,
)
_YEAR = re.compile(r"(?<!\d)20\d\d(?!\d)")
_HEADING = re.compile(r"^(#{2,6})\s*(.+)$", re.MULTILINE)


@dataclass(frozen=True)
class Measures:
    """What the owner compares between two versions of a page."""

    words: int
    statements: int
    marks: int
    cited: int
    years: int
    links: int

    @classmethod
    def of(cls, body: str) -> Measures:
        stated = statements(body)
        prose = replace_wiki_links(strip_definitions(body), lambda link: link.text or link.target)
        return cls(words=prose_words(body), statements=len(stated),
                   marks=sum(len(s.marks) for s in stated), cited=len(cited_ids(body)),
                   years=len(_YEAR.findall(CITATION.sub("", prose))),
                   links=len(find_wiki_links(body)))


@dataclass(frozen=True)
class PageComparison:
    """One page before and after, with its points and the claims behind them."""

    page_id: str
    title: str
    before: str
    after: str
    points: list[dict[str, Any]]
    left_out: list[dict[str, str]]
    report: dict[str, Any]

    def lines(self) -> list[str]:
        """The console summary (ids and numbers only)."""
        old, new = Measures.of(self.before), Measures.of(self.after)
        rows = [f"{self.page_id}: {name:10} before {getattr(old, name):6}  after "
                f"{getattr(new, name):6}" for name in Measures.__dataclass_fields__]
        kinds = self.report.get("points_by_kind", {})
        rows.append(f"{self.page_id}: {len(self.points)} points {kinds}; "
                    f"{len(self.left_out)} left out {self.report.get('left_out_by_reason', {})}")
        rows += [f"{self.page_id}:   left out {e['claim']} ({e['reason']})" for e in self.left_out]
        rows.append(f"{self.page_id}: calls {self.report.get('calls')}, still wrong "
                    f"{self.report.get('final_problems')}, unaccounted "
                    f"{self.report.get('unaccounted')}, uncited disputed "
                    f"{self.report.get('uncited_required')}")
        return rows


def _read(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {path}: {exc}") from exc


def _moved(path: Path, root: Path) -> Path:
    """``path`` (a file of the current data dir) in the data dir ``root``."""
    return root / path.relative_to(DATA_DIR)


def _under(artifact: Artifact, root: Path) -> Artifact:
    """``artifact`` (files of the current data dir) in the data dir ``root``."""
    return Artifact(_moved(artifact.original, root), _moved(artifact.edited, root))


class ConsolidationCheck:
    """Copies the inputs from ``source`` to ``target`` (two data dirs), writes the pages in
    ``target``, compares them (see the module docstring)."""

    def __init__(self, source: Path, target: Path, pages: Sequence[str],
                 logger: logging.Logger) -> None:
        self.source, self.target = source.resolve(), target.resolve()
        self.pages = list(pages)
        self.logger = logger
        if self.source == self.target:
            raise ValueError(f"the check writes into the data dir {self.target}; set "
                             "WIKI_DATA_DIR to an empty scratch folder, not to --source")

    def prepare(self) -> None:
        """Copy the input files of step 8 and the pages (original and edited) from the
        source to the target."""
        for path in INPUTS:
            source, target = _moved(path, self.source), _moved(path, self.target)
            if source.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
        plan = _read(resolve(_under(PLAN, self.target)))
        unknown = [pid for pid in self.pages if pid not in plan["pages"]]
        if unknown:
            raise ValueError(f"pages not in the plan: {unknown}")
        self.logger.info(f"Copied the inputs from {self.source} into {self.target}")

    def write(self, llm: LLMClient | None = None,
              update_llm: LLMClient | None = None) -> dict[str, Any]:
        """Step 8 in the target, on the chosen pages only, in full, with the current prompt."""
        return run(sources=self.claim_sources(),
                   plan_path=resolve(_under(PLAN, self.target)),
                   assignments_path=resolve(_under(ASSIGNMENTS, self.target)),
                   artifact=_under(WIKI_PAGES, self.target), only=self.pages, rebuild=True,
                   use_batch=False, llm=llm, update_llm=update_llm)

    def claim_sources(self) -> ClaimSources:
        """The claim records' input files in the target (effective claims and entities)."""
        return ClaimSources(resolve(_under(CLAIMS, self.target)),
                            resolve(_under(ENTITIES, self.target)),
                            _moved(AUDIT_PATH, self.target),
                            _moved(MANUAL_CONTRADICTIONS_PATH, self.target),
                            _moved(SAME_COMPLAINT_PATH, self.target),
                            _moved(SAME_COMPLAINT_VETOES_PATH, self.target))

    def compare(self) -> list[PageComparison]:
        before = _read(resolve(_under(WIKI_PAGES, self.source)))["pages"]
        after = _read(resolve(_under(WIKI_PAGES, self.target)))
        reports = after["metadata"]["pages"]
        found = []
        for page_id in self.pages:
            page = after["pages"][page_id]
            found.append(PageComparison(
                page_id, page["front_matter"]["title"],
                strip_definitions(before[page_id]["body_md"]) if page_id in before else "",
                page["body_md"], page.get("points", []), page.get("left_out", []),
                reports.get(page_id, {}).get("report", {})))
        for comparison in found:
            for line in comparison.lines():
                self.logger.info(line)
        return found

    def save(self, comparisons: Sequence[PageComparison]) -> Path:
        """The side-by-side page (private text: local only)."""
        texts = {claim["short_id"]: claim["claim_text"]
                 for claim in load_claim_records(self.claim_sources())}
        path = _under(WIKI_PAGES, self.target).original.with_name(CHECK_CONFIG["report_file"])
        path.write_text(_page(comparisons, texts), encoding="utf-8")
        self.logger.info(f"Before / after written to {path} (private: open it locally only)")
        return path


# ------------------------------------------------------------------ the HTML page


def _render(body: str) -> str:
    """A body as simple HTML: headings, paragraphs, links as their text, marks as small ids."""
    text = html.escape(replace_wiki_links(strip_definitions(body),
                                          lambda link: f"\x01{link.text or link.target}\x02"))
    text = _HEADING.sub(lambda m: f"<h4>{m.group(2)}</h4>", text)
    text = CITATION.sub(lambda m: f"<sup>{m.group(1).removeprefix(SHORT_ID_PREFIX)}</sup>", text)
    text = text.replace("\x01", "<u>").replace("\x02", "</u>")
    return "".join(f"<p>{part}</p>" for part in text.split("\n\n") if part.strip())


def _claims(ids: Sequence[str], texts: Mapping[str, str]) -> str:
    return "".join(f"<li><code>{sid}</code> {html.escape(texts.get(sid, '?'))}</li>" for sid in ids)


def _section(comparison: PageComparison, texts: Mapping[str, str]) -> str:
    old, new = Measures.of(comparison.before), Measures.of(comparison.after)
    rows = "".join(f"<tr><td>{name}</td><td>{getattr(old, name)}</td><td>{getattr(new, name)}</td></tr>"
                   for name in Measures.__dataclass_fields__)
    points = "".join(
        f"<li><b>{html.escape(p['kind'])}</b> {html.escape(p['statement'])}<details><summary>"
        f"{len(p['claims'])} claims</summary><ul>{_claims(p['claims'], texts)}</ul></details></li>"
        for p in comparison.points)
    left = "".join(f"<li><b>{html.escape(e['reason'])}</b> <code>{e['claim']}</code> "
                   f"{html.escape(texts.get(e['claim'], '?'))}</li>" for e in comparison.left_out)
    problems = html.escape(json.dumps(comparison.report.get("final_problems", []),
                                      ensure_ascii=False))
    return (f"<section><h2>{html.escape(comparison.title)} <code>{comparison.page_id}</code></h2>"
            f"<table><tr><th></th><th>before</th><th>after</th></tr>{rows}</table>"
            f"<p class='ltr'>still wrong after the retry: {problems}</p>"
            f"<div class='pair'><div><h3>before</h3>{_render(comparison.before)}</div>"
            f"<div><h3>after</h3>{_render(comparison.after)}</div></div>"
            f"<h3>points ({len(comparison.points)})</h3><ol>{points}</ol>"
            f"<h3>left out ({len(comparison.left_out)})</h3><ul>{left}</ul></section>")


_STYLE = """
:root { --bg: #fff; --fg: #1d1d1f; --line: #d0d0d5; --muted: #f4f4f6; }
@media (prefers-color-scheme: dark) { :root { --bg: #17171a; --fg: #e8e8ec; --line: #3a3a40;
  --muted: #232328; } }
body { background: var(--bg); color: var(--fg); font: 15px/1.6 system-ui, sans-serif;
  margin: 0 auto; max-width: 1400px; padding: 16px; }
section { border-top: 2px solid var(--line); margin-top: 32px; }
.pair { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }
.pair > div { background: var(--muted); padding: 8px 16px; border-radius: 6px; }
@media (max-width: 800px) { .pair { grid-template-columns: 1fr; } }
table { border-collapse: collapse; direction: ltr; } td, th { border: 1px solid var(--line);
  padding: 2px 10px; text-align: right; }
sup { font-size: 0.65em; opacity: 0.6; direction: ltr; } .ltr { direction: ltr; text-align: left; }
"""


def _page(comparisons: Sequence[PageComparison], texts: Mapping[str, str]) -> str:
    body = "".join(_section(comparison, texts) for comparison in comparisons)
    return (f"<!doctype html><html lang='he' dir='rtl'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width, initial-scale=1'>"
            f"<title>Pages before and after</title><style>{_STYLE}</style></head>"
            f"<body><h1>Pages before / after (task 27b)</h1>{body}</body></html>")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Task 27b: write a few pages with the current "
                                     "prompt into a scratch data dir and compare (PAID).")
    parser.add_argument("--source", type=Path, required=True,
                        help="the data dir that holds the current claims, plan and pages")
    parser.add_argument("--pages", nargs="+", default=CHECK_CONFIG["pages"], metavar="PAGE_ID")
    parser.add_argument("--compare-only", action="store_true",
                        help="no model call: compare what the data dir already holds")
    args = parser.parse_args(argv)
    if not os.environ.get("WIKI_DATA_DIR"):
        parser.error("set WIKI_DATA_DIR to an empty scratch folder: the check copies the inputs "
                     "into the data dir and writes pages there")
    logger = setup_step_logging(STEP_8)
    check = ConsolidationCheck(args.source, DATA_DIR, args.pages, logger)
    if not args.compare_only:
        check.prepare()
        check.write()
    check.save(check.compare())


if __name__ == "__main__":
    main()
