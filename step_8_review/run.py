"""Step 8: the wiki reviewed in parts, plus one pass across every page's points (C12).

How it works, in plain words: the wiki is cut along the plan tree into PARTS of a few related
pages (``step_8_review.parts``). For each part the model of stage ``wiki_review.stage`` (Opus
5.5) reads the plan of the whole wiki and the part's effective pages (the owner's edits
included) with the group lines step 7's writer read for them; one CROSS pass reads the plan
and the stored points of every page, to see what no part can (``prompts``). Every pass answers
with a short list of suggestions, most valuable first: pages that contradict each other, a
page to split, two to merge, a missing page or label, and (parts only) plain mistakes in a
page's text, each with a structured ``proposal`` code can carry out (``proposals``). Code
checks every proposal against the wiki and drops what names a page or group its pass did not
see, fails a check, repeats an earlier suggestion (of any run or of another pass of this run)
or exceeds the pass's cap. The owner then accepts, rejects or corrects each one (``store``);
an accepted one is carried out by ``apply``.

The due passes of a run are sent as ONE batch (half price) when the provider has a batch API;
``--sync`` sends them one by one. A pass that fails (no answer, a bad answer, a prompt over
``context_window_tokens - max_output_tokens``: never truncated) loses only its own
suggestions: it is listed in ``metadata.run.passes`` and its fingerprint is not stored, so the
next run asks again for it alone.

Re-runs (C14): ``metadata.inputs.passes`` = one fingerprint per pass. A run calls only the
passes whose fingerprint changed; nothing changed -> no call, nothing written (the file is
only refreshed when an input file is newer, so the review tool's stale notice clears). Each
pass is shown the suggestions already raised about its pages (pending, accepted, rejected,
applied: never raised again); old suggestions are carried over as they are, the new ones
appended (``raised_by`` = the pass), and the owner's statuses and corrections in the edited
file are kept (``utils.reconcile``; ``page_marks`` is a refresh key). ``--rebuild`` asks every
pass afresh: it drops the undecided suggestions and keeps every decided or corrected one.
``--only PASS ...`` runs just the named passes (a part's id is its top node's id; ``cross``)
and leaves the others' suggestions and fingerprints alone. A file written by the one-call
review loads as it is: its suggestions and decisions stay, and every pass is due.

    python -m step_8_review.run [--rebuild] [--estimate] [--sync] [--only PASS ...]

Output: ``suggestions.json`` (+ the owner's ``suggestions_edited.json``)
``{suggestions: [...], metadata: {sources, inputs{passes}, stage, model, prompt_version,
config, git_commit, generated_at, input_tokens, run{..., passes[], without_points}, summary}}``.
"""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from step_6_plan.run import AnthropicTokenCounter, TokenCounter
from step_8_review.inputs import REVIEW_CONFIG, ReviewInput, page_marks
from step_8_review.parts import ReviewPass, ReviewPasses, batch_available, stored_passes
from step_8_review.proposals import OPEN, PENDING, answer_schema, from_answer
from utils.config import LLM_CONFIG
from utils.json_io import read_json_if_present
from utils.llm_client import BatchRequest, LLMClient, _flatten
from utils.llm_client.ask import ask_json
from utils.logging_setup import git_commit, setup_step_logging
from utils.paths import STEP_8, SUGGESTIONS, WIKI_PAGES, Artifact
from utils.reconcile import load_previous, write_reconciled

REFRESH_KEYS = ("page_marks",)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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
        marks = page_marks(suggestion.get("raised_on") or {}, current) if status in OPEN else {}
        out.append({**suggestion, "page_marks": marks})
    return out


def _newer_input(original: Path, sources: Sequence[Path]) -> bool:
    newest = max(path.stat().st_mtime_ns for path in sources if path.is_file())
    return not original.is_file() or original.stat().st_mtime_ns < newest


def over_budget(review_pass: ReviewPass, tokens: int) -> str | None:
    """Why a pass's prompt cannot be sent (``None``: it fits the context window)."""
    budget = REVIEW_CONFIG["context_window_tokens"] - REVIEW_CONFIG["max_output_tokens"]
    if tokens <= budget:
        return None
    return (f"The review prompt of pass {review_pass.id} has {tokens} input tokens "
            f"({len(review_pass.pages)} pages); with wiki_review.max_output_tokens="
            f"{REVIEW_CONFIG['max_output_tokens']} the budget is {budget}, so it is "
            f"{tokens - budget} tokens ({tokens / budget - 1:.1%}) too long. Nothing was sent "
            "for this pass. A single page this long cannot be reviewed: trim its evidence or "
            "split the page.")


class ReviewRun:
    """One run of step 8 over the current wiki and the suggestions already raised."""

    def __init__(self, review: ReviewInput, artifact: Artifact, *, model: str, rebuild: bool,
                 only: Collection[str] | None) -> None:
        self.review = review
        self.artifact = artifact
        self.rebuild = rebuild
        self.only = only
        self.model = model
        self.passes = ReviewPasses(review, model)
        self.previous = {} if rebuild else load_previous(artifact)
        original = read_json_if_present(artifact.original) or {}
        self.stored = stored_passes(original.get("metadata"))
        self.due = self.passes.due(self.stored, only, rebuild)
        effective = read_json_if_present(artifact.edited) or original
        self.current: list[dict[str, Any]] = list(effective.get("suggestions") or [])
        self.statuses = {s["id"]: s["status"] for s in self.current}

    def inputs(self, answered: Collection[str]) -> dict[str, Any]:
        """``metadata.inputs`` after this run: the fingerprint of every pass whose answer is
        current (answered now, or not due); with ``--only`` the other passes' stay as stored."""
        passes = dict(self.stored) if self.only is not None else {}
        due = {p.id for p in self.due}
        for review_pass in self.passes.select(self.only):
            if review_pass.id in answered or (review_pass.id not in due and self.stored.get(
                    review_pass.id) == review_pass.fingerprint):
                passes[review_pass.id] = review_pass.fingerprint
            else:
                passes.pop(review_pass.id, None)
        return {"passes": passes}

    @property
    def unchanged(self) -> bool:
        return bool(self.previous) and not self.due and self.inputs(()) == {"passes": self.stored}

    @property
    def carried(self) -> list[dict[str, Any]]:
        """What the payload starts from: every suggestion of the step's last output (the
        owner's statuses stay in the edited file); on a rebuild only the decided ones and,
        with ``--only``, those of the other passes, as the owner left them."""
        if self.rebuild:
            return [s for s in self.current if decided(s)
                    or (self.only is not None and s.get("raised_by") not in self.only)]
        return list(self.previous.get("suggestions") or [])

    @property
    def shown(self) -> list[dict[str, Any]]:
        """What the passes are shown as already raised (each only those about its pages),
        with the owner's statuses and corrections (never to be raised again)."""
        if self.rebuild:
            return self.carried
        return self.current

    def payload(self, new: Sequence[dict[str, Any]], metadata: Mapping[str, Any]) -> dict[str, Any]:
        current = self.review.page_fingerprints()
        suggestions = with_marks([*self.carried, *new], self.statuses, current)
        return {"suggestions": suggestions, "metadata": {
            **metadata,
            "summary": {"suggestions": len(suggestions),
                        "kinds": dict(Counter(s["kind"] for s in suggestions)),
                        "new": len(new),
                        "marked": sum(1 for s in suggestions if s["page_marks"])},
        }}

    def metadata(self, input_tokens: int, answered: Collection[str], run: Mapping[str, Any]
                 ) -> dict[str, Any]:
        sources = ("groups", "plan", "assignments", "pages")
        return {
            "sources": {name: str(path) for name, path in zip(sources, self.review.sources)},
            "inputs": self.inputs(answered),
            "stage": REVIEW_CONFIG["stage"],
            "model": self.model,
            "effort": LLM_CONFIG["stages"][REVIEW_CONFIG["stage"]].get("thinking_param"),
            "prompt_version": REVIEW_CONFIG["prompt_version"],
            "config": REVIEW_CONFIG,
            "git_commit": git_commit(),
            "generated_at": _now(),
            "input_tokens": input_tokens,
            "run": dict(run),
        }

    def write(self, payload: dict[str, Any]) -> dict[str, Any]:
        report = write_reconciled(self.artifact, payload, items_key="suggestions", id_key="id",
                                  refresh_keys=REFRESH_KEYS, rebuild=self.rebuild)
        return {"written": True, "backup": str(report.backup) if report.backup else None}


def run(
    *,
    artifact: Artifact = SUGGESTIONS,
    pages_artifact: Artifact = WIKI_PAGES,
    aggregated_path: Path | None = None,
    plan_path: Path | None = None,
    assignments_path: Path | None = None,
    rebuild: bool = False,
    estimate: bool = False,
    only: Collection[str] | None = None,
    use_batch: bool = True,
    llm: LLMClient | None = None,
    count_tokens: TokenCounter | None = None,
) -> dict[str, Any]:
    """Review the passes whose input changed; see the module docstring.

    Inputs default to the effective pages, groups, plan and assignments. ``estimate``: count
    every pass's prompt, make no review call, write nothing; returns ``passes`` (``id``,
    ``pages``, ``input_tokens``, ``due``), ``calls`` and ``input_tokens`` of the due ones and
    ``batch``. ``only``: pass ids to run (others untouched; an unknown id raises
    ``ValueError``). ``use_batch=False`` (``--sync``): one call at a time. ``llm`` defaults to
    the ``wiki_review.stage`` client, ``count_tokens`` to the free counting endpoint. Returns
    ``written``, ``calls``, ``input_tokens``, ``raised`` (new suggestions), ``dropped``,
    ``suggestions`` (total), ``backup``, the review ``note``, ``passes`` and ``failed``.
    """
    logger = setup_step_logging(STEP_8)
    review = ReviewInput.load(pages_artifact, aggregated_path, plan_path, assignments_path)
    gaps = review.warn_about_gaps(logger)
    stage = REVIEW_CONFIG["stage"]
    model = llm.model if llm is not None else LLM_CONFIG["stages"][stage]["model"]
    job = ReviewRun(review, artifact, model=model, rebuild=rebuild, only=only)
    passes = job.passes
    if passes.without_points:
        logger.warning(f"{len(passes.without_points)} pages have no stored points (written "
                       "before step 7 listed them) and are left out of the cross-page pass: "
                       f"{passes.without_points}")
    batch = use_batch and (llm.supports_batch() if llm is not None else batch_available())
    count = count_tokens or AnthropicTokenCounter(model)
    result = {"calls": 0, "raised": 0, "dropped": [], "input_tokens": 0, "backup": None,
              "suggestions": len(job.current), "note": "", "passes": [], "failed": []}
    if estimate:
        counted = passes.estimate(job.stored, job.shown, count, only=only, rebuild=rebuild,
                                  batch=batch)
        logger.info(f"Review estimate: {counted['calls']} of {len(passes.passes)} passes due, "
                    f"{counted['input_tokens']} input tokens"
                    f"{' at the batch price' if batch else ''}")
        return {**result, **counted, "model": model, "written": False, "estimate": True}
    run_info: dict[str, Any] = {"calls": 0, "raised": 0, "dropped": [], "note": "",
                                "rebuild": rebuild, "only": list(only) if only else None,
                                "batch": batch, "answered": 0, "passes": [],
                                "without_points": passes.without_points, **gaps}
    if not job.due:
        if job.unchanged and not _newer_input(artifact.original, review.sources):
            logger.info(f"Wiki unchanged since the last review ({len(job.current)} suggestions); "
                        "no call, nothing written")
            return {**result, "written": False}
        if job.unchanged:
            meta = {**job.previous["metadata"], "generated_at": _now()}
            logger.info("Wiki unchanged but an input file is newer; refreshing the file (no call)")
        else:
            meta = job.metadata(0, (), run_info)
            logger.info("No review pass is due, but the passes changed; writing (no call)")
        return {**result, **job.write(job.payload([], meta))}

    rows: dict[str, dict[str, Any]] = {}
    requests = []
    for review_pass in job.due:
        system, user = passes.prompt(review_pass, job.shown)
        tokens = count(system, _flatten(user))
        problem = over_budget(review_pass, tokens)
        rows[review_pass.id] = {"id": review_pass.id, "pages": list(review_pass.pages),
                                "input_tokens": tokens, "raised": 0, "dropped": [],
                                "failed": problem, "note": ""}
        logger.info(f"Review pass {review_pass.id}: {len(review_pass.pages)} pages, {tokens} "
                    "input tokens")
        if problem:
            logger.error(problem)
            continue
        requests.append(BatchRequest(review_pass.id, system, user, task=f"review:{review_pass.id}",
                                     response_schema=answer_schema(review_pass.scope.kinds)))
    if requests:
        llm = llm or LLMClient.for_stage(stage, logger=logger,
                                         max_tokens=REVIEW_CONFIG["max_output_tokens"])
        answers = ask_json(llm, requests, batch=batch, logger=logger)
    else:
        answers = {}

    new: list[dict[str, Any]] = []
    answered: list[str] = []
    raised_at = _now()
    for review_pass in job.due:
        row = rows[review_pass.id]
        if row["failed"]:
            continue
        if review_pass.id not in answers:
            row["failed"] = "no usable answer (the LLM failure log has the details)"
            continue
        raw = answers[review_pass.id]
        try:
            kept, dropped = from_answer(raw, review, [*job.shown, *job.carried, *new],
                                        raised_at=raised_at, raised_by=review_pass.id,
                                        scope=review_pass.scope)
        except ValueError as exc:
            row["failed"] = str(exc)
            continue
        new.extend(kept)
        answered.append(review_pass.id)
        row.update(raised=len(kept), dropped=dropped, note=str(raw.get("note") or ""))
        run_info["answered"] += len(raw.get("suggestions") or [])
    failed = [pass_id for pass_id, row in rows.items() if row["failed"]]
    for row in rows.values():
        for line in row["dropped"]:
            logger.info(f"Dropped suggestion of pass {row['id']} {line}")
        if row["failed"]:
            logger.warning(f"Review pass {row['id']} failed and will be asked again: {row['failed']}")
    dropped_all = [f"{row['id']} {line}" for row in rows.values() for line in row["dropped"]]
    note = " ".join(row["note"] for row in rows.values() if row["note"])
    tokens = sum(rows[request.request_id]["input_tokens"] for request in requests)
    run_info.update(calls=len(requests), raised=len(new), dropped=dropped_all, note=note,
                    passes=list(rows.values()))
    logger.info(f"Review: {len(requests)} calls, {len(new)} new suggestions, {len(dropped_all)} "
                f"dropped, {len(failed)} passes failed")
    payload = job.payload(new, job.metadata(tokens, answered, run_info))
    written = job.write(payload)
    return {**result, **written, "calls": len(requests), "raised": len(new),
            "dropped": dropped_all, "input_tokens": tokens,
            "suggestions": len(payload["suggestions"]), "note": note,
            "passes": list(rows.values()), "failed": failed}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Step 8: review the wiki in parts (C12).")
    parser.add_argument("--rebuild", action="store_true",
                        help="ask afresh: drop undecided suggestions, keep every decided one")
    parser.add_argument("--estimate", action="store_true",
                        help="count every pass's prompt; no review call, nothing written")
    parser.add_argument("--sync", action="store_true",
                        help="call the passes one by one instead of through the batch API")
    parser.add_argument("--only", nargs="+", metavar="PASS",
                        help="run just these passes (a part's top node id, or 'cross')")
    args = parser.parse_args(argv)
    run(rebuild=args.rebuild, estimate=args.estimate, only=args.only, use_batch=not args.sync)


if __name__ == "__main__":
    main()
