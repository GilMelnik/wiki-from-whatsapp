"""Step 8b: proofread the wiki's Hebrew after the review, once the owner's decisions are applied.

The order is the owner's (2026-11-13): step 7 writes, step 8 reviews, the owner decides on the
suggestions and the accepted ones are applied; only then does the language editor read the
text, and step 9 publishes it. How it works, in plain words:

- Every planned page with text is read from the EFFECTIVE pages (the edited file when it
  exists, else step 7's file: ``inputs.effective_pages``). Its ``language_edits`` record holds
  the fingerprints of the sentences that have been through the editor; every other sentence
  was never proofread (``step_8_review.proofreader.sentence_split``): text step 7 wrote or
  an update added, a correction an apply wrote, a sentence the owner typed by hand. A page with
  no such sentence is not sent.
- Every page that has one is sent, all in ONE batch (half price; ``--sync``: one call per
  page), to the editor (stage ``proofread``, ``step_8_review.proofreader``) with its
  proofread sentences frozen: an edit that touches one is dropped. So an update that added three
  sentences to a page pays for one call that may change only those three.
- A page whose edits break a check keeps its body and is recorded as proofread with no edit
  applied, so it is not paid for again. A failed call loses only its page: nothing is stored
  for it, and the next pass sends it again.
- The results go into the pages' EDITED file (created from step 7's file when the owner has
  none; the previous one backed up), under the file's lock, and only for pages that did not
  change while the editor read them. Each page's ``language_edits`` also gets
  ``review_fingerprint``: for step 8 the page after the pass counts as the page before it
  (``inputs.page_fingerprint``), so proofreading makes no review pass due and marks no
  suggestion's page as changed. Step 7 decides by its inputs, which proofreading never
  touches; an update starts from the proofread text and keeps the record.
- ``proofread.enabled: false``: the command does nothing.

    python -m step_8_review.proofread [--estimate] [--only PAGE_ID ...] [--sync]

``--estimate``: the pages that would be sent and the characters of their prompts; no call.
``--only``: these pages only. Output: the pages (above) and the pass's record ``proofread.json``
(``PROOFREAD_RECORD``) ``{pages: {page_id: {new_sentences, applied, dropped, reverted?} |
{new_sentences, failed}}, left: [page_id], metadata: {stage, model, batch, only, pages_due,
sent, failed, language_edits, pages_left, config, git_commit, generated_at, sources}}``: what
the pass sent and did, and the pages still holding text that was never proofread. With nothing
due and the record newer than every input, nothing is written.
"""

from __future__ import annotations

import argparse
import logging
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from step_7_write_pages.checks import strip_definitions
from step_7_write_pages.inputs import Corpus, PageContext
from step_8_review.inputs import REVIEW_FINGERPRINT, effective_pages, page_fingerprint
from step_8_review.parts import batch_available
from step_8_review.proofreader import (
    PROOFREAD_CONFIG,
    Proofread,
    Proofreader,
    ProofreadJob,
    prompt_chars,
    proofread_job,
    sentence_split,
)
from utils.config import LLM_CONFIG
from utils.file_lock import file_lock
from utils.json_io import read_json_if_present, write_json_file
from utils.llm_client import LLMClient
from utils.logging_setup import git_commit, setup_step_logging
from utils.paths import PROOFREAD_RECORD, STEP_8, WIKI_PAGES, Artifact
from utils.reconcile import backup_file, guarded_write
from utils.timestamps import utc_now_z

MOVED = "the page changed while the editor read it; it is sent again on the next pass"


@dataclass(frozen=True)
class DuePage:
    """A page holding text that was never proofread, as the pass read it."""

    page_id: str
    page: dict[str, Any]
    context: PageContext
    done: list[str]   # its proofread sentences (frozen)
    new: int          # how many of its sentences were never proofread

    @property
    def body(self) -> str:
        return strip_definitions(self.page["body_md"])

    def job(self, plan: Mapping[str, Any]) -> ProofreadJob:
        text = {"body": self.body, "points": self.page.get("points") or [],
                "left_out": self.page.get("left_out") or []}
        return proofread_job(self.context, plan, text, self.done)


def proofread_page(page: Mapping[str, Any], outcome: Proofread) -> dict[str, Any]:
    """``page`` after a successful pass: the edited body (when an edit was applied) and the
    pass's ``language_edits`` record, with the fingerprint step 8 keeps counting."""
    out = {key: value for key, value in page.items()
           if key not in ("language_edits", "input_fingerprint")}
    if outcome.language_edits["applied"]:
        out["body_md"] = outcome.body
    after = page_fingerprint(out)  # the page as it reads now (no record yet: nothing carried)
    out["language_edits"] = {**outcome.language_edits, REVIEW_FINGERPRINT: {
        "after": after, "counts_as": page_fingerprint(page)}}
    if "input_fingerprint" in page:
        out["input_fingerprint"] = page["input_fingerprint"]
    return out


class ProofreadPass:
    """One pass over the effective pages (see the module docstring)."""

    def __init__(self, corpus: Corpus, pages_artifact: Artifact, only: Collection[str] | None,
                 logger: logging.Logger) -> None:
        self.corpus = corpus
        self.artifact = pages_artifact
        self.logger = logger
        payload, self.pages_path = effective_pages(pages_artifact)
        pages = payload.get("pages") or {}
        written = [pid for pid in corpus.plan["pages"] if pid in pages]
        unknown = sorted(set(only or ()) - set(written))
        if unknown:
            raise ValueError(f"--only {unknown}: not a written page of the plan; the pages are "
                             f"{written}")
        catalogue = corpus.catalogue()
        self.unproofread: list[str] = []  # every page holding never-proofread text
        self.due: list[DuePage] = []      # those this pass sends
        for page_id in written:
            done, new = sentence_split(pages[page_id])
            if not new:
                continue
            self.unproofread.append(page_id)
            if only is not None and page_id not in only:
                continue
            context = corpus.context(page_id, catalogue)
            if context is None:
                logger.warning(f"{page_id}: no claim groups on this page any more (run step 7); "
                               "not proofread")
                continue
            self.due.append(DuePage(page_id, pages[page_id], context, done, len(new)))

    @property
    def sources(self) -> tuple[Path, ...]:
        return (*self.corpus.sources, self.pages_path)

    def estimate(self) -> list[dict[str, Any]]:
        return [{"page": due.page_id, "chars": prompt_chars(len(due.body)),
                 "new_sentences": due.new} for due in self.due]

    def send(self, proofreader: Proofreader) -> dict[str, Proofread]:
        return proofreader.run([due.job(self.corpus.plan) for due in self.due])

    def save(self, outcomes: Mapping[str, Proofread]) -> tuple[list[str], list[str], Path | None]:
        """Write the proofread pages into the edited file; returns the pages saved, those that
        changed meanwhile (left for the next pass) and the backup of the edited file."""
        read = {due.page_id: due.page for due in self.due}
        good = {pid: outcome for pid, outcome in outcomes.items() if not outcome.failed}
        if not good:
            return [], [], None
        artifact = self.artifact
        with file_lock(artifact.edited):
            payload = read_json_if_present(artifact.edited) or read_json_if_present(artifact.original)
            saved, moved = [], []
            for page_id, outcome in good.items():
                if payload["pages"].get(page_id) != read[page_id]:
                    self.logger.warning(f"{page_id}: {MOVED}")
                    moved.append(page_id)
                    continue
                payload["pages"][page_id] = proofread_page(read[page_id], outcome)
                saved.append(page_id)
            backup = backup_file(artifact.edited) if saved else None
            if saved:
                write_json_file(payload, artifact.edited)
        return saved, moved, backup

    def current(self, record_path: Path) -> bool:
        """The record is not older than anything the pass reads."""
        newest = max(path.stat().st_mtime_ns for path in self.sources if path.is_file())
        return record_path.is_file() and record_path.stat().st_mtime_ns >= newest


def _page_rows(due: Sequence[DuePage], outcomes: Mapping[str, Proofread],
               moved: Collection[str]) -> dict[str, dict[str, Any]]:
    rows = {}
    for page in due:
        record = outcomes[page.page_id].language_edits if page.page_id in outcomes else {}
        row: dict[str, Any] = {"new_sentences": page.new}
        if "failed" in record:
            row["failed"] = record["failed"]
        elif page.page_id in moved:
            row["failed"] = MOVED
        else:
            row.update(applied=len(record.get("applied", [])), dropped=record.get("dropped", {}))
            if "reverted" in record:
                row["reverted"] = record["reverted"]
        rows[page.page_id] = row
    return rows


def run(
    *,
    pages_artifact: Artifact = WIKI_PAGES,
    record_path: Path = PROOFREAD_RECORD,
    aggregated_path: Path | None = None,
    plan_path: Path | None = None,
    assignments_path: Path | None = None,
    only: Collection[str] | None = None,
    estimate: bool = False,
    use_batch: bool = True,
    llm: LLMClient | None = None,
) -> dict[str, Any]:
    """Proofread every page holding text that was never proofread; see the module docstring.

    Inputs default to the effective pages, groups, plan and assignments. ``estimate``: no call,
    nothing written; returns ``estimate`` (``page``, ``chars`` of the prompt, ``new_sentences``)
    and ``calls``. ``only``: page ids (an unknown one raises ``ValueError``). ``use_batch=False``
    (``--sync``): one call per page. ``llm`` defaults to the ``proofread`` stage client.
    Returns ``pages_due`` (pages holding never-proofread text), ``calls``, ``batch``,
    ``proofread`` (pages saved), ``failed``, ``language_edits``, ``left``, ``written`` and
    ``backup``.
    """
    logger = setup_step_logging(STEP_8)
    if not PROOFREAD_CONFIG["enabled"]:
        logger.info("Proofreading is off (proofread.enabled: false); nothing to do")
        return {"enabled": False, "calls": 0, "written": False}
    corpus = Corpus.load(aggregated_path, plan_path, assignments_path)
    job = ProofreadPass(corpus, pages_artifact, only, logger)
    stage = PROOFREAD_CONFIG["stage"]
    batch = use_batch and (llm.supports_batch() if llm is not None else batch_available(stage))
    result: dict[str, Any] = {"enabled": True, "pages_due": len(job.unproofread),
                              "calls": len(job.due), "batch": batch}
    logger.info(f"{len(job.unproofread)} pages hold text that was never proofread: "
                f"{job.unproofread}; this pass sends {len(job.due)}")
    if estimate:
        rows = job.estimate()
        logger.info(f"Proofreading estimate: {len(rows)} calls, {sum(r['chars'] for r in rows)} "
                    f"prompt characters{' at the batch price' if batch else ''}")
        return {**result, "estimate": rows, "written": False}
    if not job.due and job.current(record_path):
        logger.info("Nothing to proofread and the record is current; no call, nothing written")
        return {**result, "proofread": 0, "failed": [], "language_edits": 0,
                "left": job.unproofread, "written": False, "backup": None}
    outcomes: dict[str, Proofread] = {}
    if job.due:
        llm = llm or LLMClient.for_stage(stage, logger=logger,
                                         max_tokens=PROOFREAD_CONFIG["max_output_tokens"])
        outcomes = job.send(Proofreader(llm, logger, batch=batch))
    saved, moved, backup = job.save(outcomes)
    failed = [pid for pid, outcome in outcomes.items() if outcome.failed] + moved
    edits = sum(len(outcomes[pid].language_edits["applied"]) for pid in saved)
    left = [pid for pid in job.unproofread if pid not in saved]
    model = llm.model if llm is not None else LLM_CONFIG["stages"][stage]["model"]
    guarded_write(record_path, {
        "pages": _page_rows(job.due, outcomes, moved),
        "left": left,
        "metadata": {"stage": stage, "model": model, "batch": batch,
                     "only": sorted(only) if only is not None else None,
                     "pages_due": len(job.unproofread), "sent": len(job.due),
                     "failed": len(failed), "language_edits": edits, "pages_left": len(left),
                     "config": PROOFREAD_CONFIG, "git_commit": git_commit(),
                     "generated_at": utc_now_z(),
                     "sources": {"pages": str(job.pages_path)}},
    }, backup=False)
    logger.info(f"Proofread {len(saved)} of {len(job.due)} pages sent ({edits} language edits; "
                f"failed or left for the next pass: {failed}); pages backup {backup}; "
                f"{len(left)} pages still hold text that was never proofread: {left}")
    return {**result, "proofread": len(saved), "failed": failed, "language_edits": edits,
            "left": left, "written": True, "backup": str(backup) if backup else None}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Step 8b: proofread the text that was never proofread (after the review).")
    parser.add_argument("--estimate", action="store_true",
                        help="list the pages that would be sent; no call, nothing written")
    parser.add_argument("--only", nargs="+", metavar="PAGE_ID", help="proofread these pages only")
    parser.add_argument("--sync", action="store_true",
                        help="call the pages one by one instead of through the batch API")
    args = parser.parse_args(argv)
    run(estimate=args.estimate, only=args.only, use_batch=not args.sync)


if __name__ == "__main__":
    main()
