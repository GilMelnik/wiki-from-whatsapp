"""Step 8: one reader of the whole wiki proposes what only a whole-wiki reader can see (C12).

How it works, in plain words: the model of stage ``wiki_review.stage`` (Opus 5.5) reads the
plan and every effective page (the owner's edits included) together with the group lines
step 7's writer read for it (``step_8_review.inputs`` / ``prompts``), in ONE call. It answers
with a short list of suggestions, most valuable first: pages that contradict each other, a
page to split, two to merge, a missing page or label, and plain mistakes in a page's text,
each with a structured ``proposal`` code can carry out (``proposals``). Code checks every
proposal against the wiki (the quoted sentence is in the page, the group ids are on the
pages, a plan operation validates) and drops what fails, repeats an earlier suggestion or
exceeds ``wiki_review.max_suggestions``. The owner then accepts, rejects or corrects each one
(``store``); an accepted one is carried out by ``apply``.

The prompt's tokens are counted before the call; a wiki that does not fit
``context_window_tokens - max_output_tokens`` fails with the overshoot, never truncated.

Re-runs (C14): ``metadata.inputs`` = fingerprints of the reviewer (model, prompt version,
cap), of the plan block and of every page. Same inputs -> no call, nothing written (the file
is only refreshed when an input file is newer, so the review tool's stale notice clears).
Otherwise one call that is shown every suggestion already raised (pending, accepted,
rejected, applied: never raised again); old suggestions are carried over as they are, the new
ones appended, and the owner's statuses and corrections in the edited file are kept
(``utils.reconcile``; ``page_marks`` is a refresh key: a pending or accepted suggestion whose
page changed or left the wiki since it was raised is marked). ``--rebuild`` asks afresh: it
drops the undecided suggestions and keeps every decided or corrected one.

    python -m step_8_review.run [--rebuild] [--estimate]

Output: ``suggestions.json`` (+ the owner's ``suggestions_edited.json``)
``{suggestions: [...], metadata: {sources, inputs, stage, model, prompt_version, config,
git_commit, generated_at, input_tokens, run, summary}}``.
"""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from step_6_plan.run import AnthropicTokenCounter, TokenCounter
from step_8_review.inputs import REVIEW_CONFIG, ReviewInput, page_marks, read_json
from step_8_review.prompts import review_prompt
from step_8_review.proposals import ACCEPTED, ANSWER_SCHEMA, PENDING, from_answer
from utils.config import LLM_CONFIG
from utils.embedding_ab import _git_commit
from utils.llm_client import LLMClient, _flatten
from utils.logging_setup import setup_step_logging
from utils.paths import STEP_8, SUGGESTIONS, WIKI_PAGES, Artifact
from utils.reconcile import fingerprint, load_previous, write_reconciled

REFRESH_KEYS = ("page_marks",)
MARKED = (PENDING, ACCEPTED)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def reviewer_fingerprint(model: str) -> str:
    """Who reviews: a new model, prompt version or cap asks again."""
    return fingerprint({"model": model, "prompt_version": REVIEW_CONFIG["prompt_version"],
                        "max_suggestions": REVIEW_CONFIG["max_suggestions"]})


def decided(suggestion: Mapping[str, Any]) -> bool:
    """The owner acted on it (a status other than pending, or a corrected proposal)."""
    return suggestion["status"] != PENDING or "edited_proposal" in suggestion


def with_marks(suggestions: Sequence[Mapping[str, Any]], statuses: Mapping[str, str],
               current: Mapping[str, str]) -> list[dict[str, Any]]:
    """The suggestions with ``page_marks`` recomputed for those still open (by their
    effective status ``statuses``); decided ones carry no marks."""
    out = []
    for suggestion in suggestions:
        status = statuses.get(suggestion["id"], suggestion["status"])
        marks = page_marks(suggestion.get("raised_on") or {}, current) if status in MARKED else {}
        out.append({**suggestion, "page_marks": marks})
    return out


def _newer_input(original: Path, sources: Sequence[Path]) -> bool:
    newest = max(path.stat().st_mtime_ns for path in sources if path.is_file())
    return not original.is_file() or original.stat().st_mtime_ns < newest


class ReviewRun:
    """One run of step 8 over the current wiki and the suggestions already raised."""

    def __init__(self, review: ReviewInput, artifact: Artifact, *, model: str, rebuild: bool
                 ) -> None:
        self.review = review
        self.artifact = artifact
        self.rebuild = rebuild
        self.model = model
        self.previous = {} if rebuild else load_previous(artifact)
        effective = read_json(artifact.edited) or read_json(artifact.original) or {}
        self.current: list[dict[str, Any]] = list(effective.get("suggestions") or [])
        self.statuses = {s["id"]: s["status"] for s in self.current}
        self.inputs = {"reviewer": reviewer_fingerprint(model),
                       "plan": fingerprint(review.plan_block()),
                       "pages": review.page_fingerprints()}

    @property
    def unchanged(self) -> bool:
        return bool(self.previous) and (self.previous.get("metadata") or {}).get("inputs") == self.inputs

    @property
    def carried(self) -> list[dict[str, Any]]:
        """What the payload starts from: every suggestion of the step's last output (the
        owner's statuses stay in the edited file); on a rebuild only the decided ones, as
        the owner left them."""
        if self.rebuild:
            return [s for s in self.current if decided(s)]
        return list(self.previous.get("suggestions") or [])

    @property
    def shown(self) -> list[dict[str, Any]]:
        """What the model is shown as already raised, with the owner's statuses and
        corrections (never to be raised again)."""
        if self.rebuild:
            return self.carried
        return self.current

    def payload(self, new: Sequence[dict[str, Any]], metadata: Mapping[str, Any]) -> dict[str, Any]:
        current = self.inputs["pages"]
        suggestions = with_marks([*self.carried, *new], self.statuses, current)
        return {"suggestions": suggestions, "metadata": {
            **metadata,
            "summary": {"suggestions": len(suggestions),
                        "kinds": dict(Counter(s["kind"] for s in suggestions)),
                        "new": len(new),
                        "marked": sum(1 for s in suggestions if s["page_marks"])},
        }}

    def metadata(self, input_tokens: int, run: Mapping[str, Any]) -> dict[str, Any]:
        sources = ("groups", "plan", "assignments", "pages")
        return {
            "sources": {name: str(path) for name, path in zip(sources, self.review.sources)},
            "inputs": self.inputs,
            "stage": REVIEW_CONFIG["stage"],
            "model": self.model,
            "effort": LLM_CONFIG["stages"][REVIEW_CONFIG["stage"]].get("thinking_param"),
            "prompt_version": REVIEW_CONFIG["prompt_version"],
            "config": REVIEW_CONFIG,
            "git_commit": _git_commit(),
            "generated_at": _now(),
            "input_tokens": input_tokens,
            "run": dict(run),
        }

    def write(self, payload: dict[str, Any]) -> dict[str, Any]:
        report = write_reconciled(self.artifact, payload, items_key="suggestions", id_key="id",
                                  refresh_keys=REFRESH_KEYS, rebuild=self.rebuild)
        return {"written": True, "backup": str(report.backup) if report.backup else None}


def _budget_check(tokens: int, pages: int) -> None:
    budget = REVIEW_CONFIG["context_window_tokens"] - REVIEW_CONFIG["max_output_tokens"]
    if tokens > budget:
        raise ValueError(
            f"The review prompt has {tokens} input tokens ({pages} pages); with "
            f"wiki_review.max_output_tokens={REVIEW_CONFIG['max_output_tokens']} the budget is "
            f"{budget}, so it is {tokens - budget} tokens ({tokens / budget - 1:.1%}) too long. "
            "Nothing was sent. The wiki outgrew one review call: review it in parts or trim "
            "the evidence."
        )


def run(
    *,
    artifact: Artifact = SUGGESTIONS,
    pages_artifact: Artifact = WIKI_PAGES,
    aggregated_path: Path | None = None,
    plan_path: Path | None = None,
    assignments_path: Path | None = None,
    rebuild: bool = False,
    estimate: bool = False,
    llm: LLMClient | None = None,
    count_tokens: TokenCounter | None = None,
) -> dict[str, Any]:
    """Review the whole wiki once if anything it reads changed; see the module docstring.

    Inputs default to the effective pages, groups, plan and assignments. ``estimate``: count
    the prompt's tokens, make no review call, write nothing. ``llm`` defaults to the
    ``wiki_review.stage`` client, ``count_tokens`` to the free counting endpoint. Returns
    ``written``, ``calls``, ``input_tokens``, ``raised`` (new suggestions), ``dropped``,
    ``suggestions`` (total), ``backup`` and the review ``note``.
    """
    logger = setup_step_logging(STEP_8)
    review = ReviewInput.load(pages_artifact, aggregated_path, plan_path, assignments_path)
    gaps = review.warn_about_gaps(logger)
    stage = REVIEW_CONFIG["stage"]
    model = llm.model if llm is not None else LLM_CONFIG["stages"][stage]["model"]
    job = ReviewRun(review, artifact, model=model, rebuild=rebuild)
    result = {"calls": 0, "raised": 0, "dropped": [], "input_tokens": 0, "backup": None,
              "suggestions": len(job.current), "note": ""}
    if job.unchanged and not estimate:
        if not _newer_input(artifact.original, review.sources):
            logger.info(f"Wiki unchanged since the last review ({len(job.current)} suggestions); "
                        "no call, nothing written")
            return {**result, "written": False}
        meta = job.previous["metadata"]
        payload = job.payload([], {**meta, "generated_at": _now()})
        logger.info("Wiki unchanged but an input file is newer; refreshing the file (no call)")
        return {**result, **job.write(payload)}
    system, user = review_prompt(review, job.shown)
    tokens = (count_tokens or AnthropicTokenCounter(model))(system, _flatten(user))
    logger.info(f"Review prompt: {tokens} input tokens, {len(review.pages)} pages, "
                f"{len(job.shown)} suggestions already raised")
    _budget_check(tokens, len(review.pages))
    if estimate:
        return {**result, "input_tokens": tokens, "written": False, "estimate": True}
    llm = llm or LLMClient.for_stage(stage, logger=logger,
                                     max_tokens=REVIEW_CONFIG["max_output_tokens"])
    raw = llm.complete_json(system, user, task="review", response_schema=ANSWER_SCHEMA)
    new, dropped = from_answer(raw, review, [*job.shown, *job.carried], raised_at=_now())
    note = str(raw.get("note") or "") if isinstance(raw, dict) else ""
    for line in dropped:
        logger.info(f"Dropped suggestion {line}")
    logger.info(f"Review: {len(new)} new suggestions, {len(dropped)} dropped; note: {note}")
    run_info = {"calls": 1, "raised": len(new), "dropped": dropped, "note": note,
                "rebuild": rebuild, "answered": len(raw.get("suggestions") or []), **gaps}
    payload = job.payload(new, job.metadata(tokens, run_info))
    written = job.write(payload)
    return {**result, **written, "calls": 1, "raised": len(new), "dropped": dropped,
            "input_tokens": tokens, "suggestions": len(payload["suggestions"]), "note": note}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Step 8: review the whole wiki (C12).")
    parser.add_argument("--rebuild", action="store_true",
                        help="ask afresh: drop undecided suggestions, keep every decided one")
    parser.add_argument("--estimate", action="store_true",
                        help="count the review prompt's tokens; no review call, nothing written")
    args = parser.parse_args(argv)
    run(rebuild=args.rebuild, estimate=args.estimate)


if __name__ == "__main__":
    main()
