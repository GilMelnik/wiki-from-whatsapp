"""Step 7: write the wiki's pages (contract C9) from the plan, the claim groups and their pages.

How it works, in plain words: for every page of the reviewed plan (C7) the step collects the
claim groups assigned to it (C8, ``step_7_write_pages.inputs``) and asks the ``write`` stage
(Sonnet 5.5) for the whole page in one call: first the page's points (each one thing the page
says, with every group behind it, ``points``), then the article written from them, one
statement per point: readable Hebrew prose grounded in those groups, both sides wherever
members disagree, dates only where they help the reader decide, and Wikipedia-style links to
other pages and labels (``prompts``). Code then enforces what must not be left to the model
(``checks``): only the page's own groups are cited, only planned pages and labels are linked,
disputed groups are cited, every main group is accounted for in a point or left out with a
reason, every point is said and no point is told
again and again (one retry that names the problems, then repair); the OKF front matter is
written in code (``front_matter``). A negative statement about a named entity that fewer
than two members reported never reaches the writer (``withhold``): code adds it to its page's
``left_out`` with reason ``withheld`` and lists it in ``metadata.withheld``. The body keeps its ``[^g-…]`` marks only: step 9 renders
the references from the groups (a body stored with definitions keeps them until its text
changes; step 9 ignores them). The points and the groups left out are stored with the page:
an update starts from them.

Re-runs (C14) work per page. ``metadata.pages[page_id]`` keeps a fingerprint of the writer
(model + ``pages.prompt_version``), of the page's plan fields and of every group record, and
``input_fingerprint`` combines them:
- unchanged page -> its text is untouched, including a manual edit in the edited file (only
  code-owned front matter such as the breadcrumb or the labels is brought up to date);
- page not written yet, or a new writer -> full write;
- groups or plan fields changed -> UPDATE call (stage ``update``) from the current EFFECTIVE
  page (owner edits and stored points included) + the new / changed / removed groups; "no
  change needed" keeps the text and ``generated.at`` (the points may still change: a new group
  joined one); above ``pages.update.rewrite_changed_share`` a full rewrite;
- a page that left the plan, or has no groups any more, is dropped.
Every body the writer wrote or changed in this run then gets the Hebrew language edit (stage
``proofread``, all such pages in ONE batch, ``step_7_write_pages.proofread``; off with
``proofread.enabled: false``): code applies only the safe replacements, never inside a sentence
the update kept, and keeps the writer's body when the edits break a check. A page whose
proofreading call failed keeps the writer's body and is sent again by the next run (unless the
owner edited it since). Proofreading changes no fingerprint: a run with nothing new makes no
call.
Pages are written through ``utils.reconcile`` once per run (front matter and fingerprint are
refresh keys, so a kept manual edit gets current front matter). A crash costs nothing on the
re-run: prompts are deterministic and answers are in the LLM disk cache. Nothing is written
when nothing changed, unless an input file is newer than the output (the review tool's stale
notice then clears).

    python -m step_7_write_pages.run [--rebuild] [--only PAGE_ID ...] [--estimate]

Output: ``wiki_pages.json`` (C9) ``{pages: {page_id: {front_matter, body_md, points,
left_out, language_edits?, input_fingerprint}}, metadata: {sources, inputs, pages, as_of,
models, config, git_commit, generated_at, run, summary, withheld}}``; ``points`` = ``[{kind,
statement, groups}]``, ``left_out`` = ``[{group, reason}]``; ``language_edits`` = the
proofreading of the body (``proofread``); ``withheld`` = ``[{group_id, page_id, entity_ids,
supporter_count}]`` (ids and numbers only).
"""

from __future__ import annotations

import argparse
import json
import logging
from collections import Counter
from collections.abc import Collection, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import anthropic

from step_6_plan.run import AnthropicTokenCounter, TokenCounter
from step_7_write_pages.checks import sentences, strip_definitions
from step_7_write_pages.front_matter import LabelRule, page_front_matter
from step_7_write_pages.inputs import (
    PAGES_CONFIG,
    Corpus,
    PageContext,
    input_fingerprint,
    page_state,
    writer_fingerprint,
)
from step_7_write_pages.points import LeftOut, Point
from step_7_write_pages.prompts import update_prompt, write_prompt
from step_7_write_pages.proofread import (
    PROOFREAD_CONFIG,
    Proofread,
    Proofreader,
    ProofreadJob,
    owed_frozen,
    prompt_chars,
    proofread_job,
)
from step_7_write_pages.withhold import WITHHOLD_CONFIG
from step_7_write_pages.writer import GroupDelta, PageText, PageWriter, changed_share, current_text
from utils.embedding_ab import _git_commit
from utils.llm_client import LLMClient, _flatten
from utils.logging_setup import setup_step_logging
from utils.paths import STEP_7, WIKI_PAGES, Artifact
from utils.reconcile import diff_inputs, load_previous, write_reconciled

WRITE_CONFIG = PAGES_CONFIG["write"]
UPDATE_CONFIG = PAGES_CONFIG["update"]
REFRESH_KEYS = ("front_matter", "input_fingerprint")
COMPARED_METADATA = ("inputs", "pages", "withheld")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} is not valid JSON: {exc}") from exc


@dataclass
class Job:
    """What happens to one page in this run: ``write``, ``update`` or ``keep``."""

    page_id: str
    mode: str
    context: PageContext
    state: dict[str, Any]
    source: dict[str, Any] | None = None   # the effective page (owner edits included)
    delta: GroupDelta | None = None


class PagesRun:
    """One run of the step over the current inputs and the stored pages."""

    def __init__(self, corpus: Corpus, previous: Mapping[str, Any], effective: Mapping[str, Any],
                 *, model: str, rebuild: bool, only: Collection[str] | None,
                 logger: logging.Logger) -> None:
        self.corpus = corpus
        self.model = model
        self.writer = writer_fingerprint(model)
        self.logger = logger
        self.rebuild = rebuild
        self.scope = None if only is None else set(only)
        self.old_pages: dict[str, Any] = dict(previous.get("pages") or {})
        meta = previous.get("metadata") or {}
        self.old_inputs: dict[str, str] = dict(meta.get("inputs") or {})
        self.old_states: dict[str, Any] = dict(meta.get("pages") or {})
        self.effective = effective
        self.labels = LabelRule(corpus, list(corpus.plan["pages"]))
        self.stats: Counter[str] = Counter()
        self.carried: dict[str, Any] = {}
        self.jobs: list[Job] = []
        self.failed: dict[str, str] = {}
        self.proofread_pages: dict[str, Proofread] = {}

    def plan(self) -> list[Job]:
        """Decide what happens to every planned page (see the module docstring)."""
        catalogue = self.corpus.catalogue()
        for page_id in self.corpus.plan["pages"]:
            old_page = self.old_pages.get(page_id)
            if self.scope is not None and page_id not in self.scope:
                if old_page is not None:
                    self.carried[page_id] = old_page
                continue
            context = self.corpus.context(page_id, catalogue)
            if context is None:
                self.stats["no_groups"] += 1
                self.logger.info(f"{page_id}: no claim groups on this page; not written"
                                 + (" (its old text is dropped)" if old_page else ""))
                continue
            self.jobs.append(self._job(page_id, context, old_page))
        dropped = sorted(set(self.old_pages) - set(self.corpus.plan["pages"]))
        if dropped:
            self.logger.info(f"Pages no longer in the plan, dropped: {dropped}")
        self.stats["dropped"] += len(dropped)
        return self.jobs

    def _job(self, page_id: str, context: PageContext, old_page: dict[str, Any] | None) -> Job:
        state = page_state(context, self.writer)
        old_state = self.old_states.get(page_id)
        source = self.effective.get(page_id, old_page)
        if self.rebuild or old_page is None or old_state is None or old_state["writer"] != self.writer:
            return Job(page_id, "write", context, state)
        if all(state[key] == old_state[key] for key in ("brief", "groups")):
            return Job(page_id, "keep", context, {**old_state, **state}, source or old_page)
        diff = diff_inputs(old_state["groups"], state["groups"])
        delta = GroupDelta(diff.new, diff.changed, diff.removed,
                           brief_changed=state["brief"] != old_state["brief"])
        share = changed_share(delta, len(context.records))
        if share > UPDATE_CONFIG["rewrite_changed_share"]:
            self.logger.info(f"{page_id}: {share:.0%} of its groups changed; rewriting in full")
            return Job(page_id, "write", context, state)
        if source is not None and source != old_page:
            self.logger.info(f"{page_id}: its manual edit is the starting text of this update (the "
                             "reconcile line lists it as superseded; the edit is carried over)")
        return Job(page_id, "update", context, state, source or old_page, delta)

    def _call(self, writer: PageWriter, job: Job) -> PageText:
        if job.mode == "write":
            return writer.write(job.context)
        return writer.update(job.context, current_text(job.source), job.delta)

    def write(self, writer: PageWriter) -> dict[str, PageText]:
        """Run the model calls of every ``write`` / ``update`` job (a failed page is logged
        and keeps its old text, so the next run tries again)."""
        due = [job for job in self.jobs if job.mode != "keep"]
        texts: dict[str, PageText] = {}
        with ThreadPoolExecutor(max_workers=PAGES_CONFIG["concurrency"]) as pool:
            futures = {job.page_id: pool.submit(self._call, writer, job) for job in due}
            for page_id, future in futures.items():
                try:
                    texts[page_id] = future.result()
                except (anthropic.APIError, RuntimeError, ValueError, OSError) as exc:
                    self.failed[page_id] = repr(exc)
                    self.logger.error(f"{page_id}: writing failed, old text kept: {exc!r}")
        return texts

    def _proofread_job(self, job: Job, text: PageText | None) -> ProofreadJob | None:
        """The proofreading a page is due in this run: its body is new, or the last call
        failed on the body it keeps (and the owner has not edited the page since)."""
        source = None if job.source is None else strip_definitions(job.source["body_md"])
        if job.mode != "keep" and text is not None and text.body != source:
            frozen = sentences(source) if job.mode == "update" else []
            answer = {"body": text.body, "points": text.points, "left_out": text.left_out}
            return proofread_job(job.context, self.corpus.plan, answer, frozen)
        old_page = self.old_pages.get(job.page_id)
        if (job.mode != "keep" and text is None) or old_page is None or job.source != old_page:
            return None
        owed = owed_frozen(old_page.get("language_edits"), source)
        if owed is None:
            return None
        points, left_out = ((text.points, text.left_out) if text is not None else
                            (old_page.get("points", []), old_page.get("left_out", [])))
        answer = {"body": source, "points": points, "left_out": left_out}
        return proofread_job(job.context, self.corpus.plan, answer, owed)

    def proofread(self, texts: dict[str, PageText], proofreader: Proofreader) -> dict[str, PageText]:
        """The Hebrew language edit of every due page in one batch; returns ``texts`` with the
        edited bodies (a kept page's outcome is used by ``payload``)."""
        jobs = [due for due in (self._proofread_job(job, texts.get(job.page_id))
                                for job in self.jobs) if due is not None]
        self.proofread_pages = proofreader.run(jobs)
        self.stats["proofread"] = len(jobs)
        self.stats["language_edits"] = sum(len(p.language_edits.get("applied", []))
                                           for p in self.proofread_pages.values())
        return {pid: replace(text, body=self.proofread_pages[pid].body)
                if pid in self.proofread_pages else text for pid, text in texts.items()}

    def estimate(self, count_tokens: TokenCounter) -> dict[str, Any]:
        """Input tokens of every call this run would make (no writing call)."""
        rows = []
        for job in self.jobs:
            if job.mode == "keep":
                continue
            if job.mode == "write":
                system, user = write_prompt(job.context)
            else:
                system, user = update_prompt(job.context, current_text(job.source),
                                             new=job.delta.new, changed=job.delta.changed,
                                             removed=job.delta.removed,
                                             brief_changed=job.delta.brief_changed)
            tokens = count_tokens(system, _flatten(user))
            rows.append({"page": job.page_id, "mode": job.mode, "groups": len(job.context.records),
                         "input_tokens": tokens})
            self.logger.info(f"estimate {job.mode:6} {job.page_id}: {len(job.context.records)} "
                             f"groups, {tokens} input tokens")
        total = sum(row["input_tokens"] for row in rows)
        self.logger.info(f"estimate: {len(rows)} calls (+ retries), {total} input tokens; "
                         f"{sum(1 for job in self.jobs if job.mode == 'keep')} pages untouched")
        return {"estimate": rows, "input_tokens": total, "written": False,
                "modes": dict(Counter(job.mode for job in self.jobs)),
                "proofread": self._proofread_estimate()}

    def _proofread_estimate(self) -> list[dict[str, Any]]:
        """The proofreading calls of the run: every page to write (its current body's length,
        a new page the stored pages' mean) and every kept page that is owed one."""
        if not PROOFREAD_CONFIG["enabled"]:
            return []
        bodies = {pid: len(strip_definitions(page["body_md"])) for pid, page in self.old_pages.items()}
        typical = sum(bodies.values()) // max(len(bodies), 1)
        rows = []
        for job in self.jobs:
            page = job.source or self.old_pages.get(job.page_id)
            if job.mode == "keep" and self._proofread_job(job, None) is None:
                continue
            chars = len(strip_definitions(page["body_md"])) if page else typical
            rows.append({"page": job.page_id, "chars": prompt_chars(chars)})
        return rows

    # ----------------------------------------------------------------- payload

    def _item(self, job: Job, *, body: str, summary: str, questions: list[str],
              related: list[dict[str, str]], points: list[Point], left_out: list[LeftOut],
              generated_at: str, language_edits: dict[str, Any] | None) -> dict[str, Any]:
        front_matter = page_front_matter(
            job.context, self.corpus.plan, body=body, summary=summary, questions=questions,
            related=related, labels=self.labels(job.page_id), model=self.model,
            generated_at=generated_at)
        item = {"front_matter": front_matter, "body_md": body, "points": points,
                "left_out": self.corpus.accounted_left_out(job.page_id, left_out)}
        if language_edits is not None:
            item["language_edits"] = language_edits
        return {**item, "input_fingerprint": input_fingerprint(job.state)}

    def _from_text(self, job: Job, text: PageText) -> dict[str, Any]:
        source = job.source
        same = source is not None and text.body == strip_definitions(source["body_md"]) and all(
            source["front_matter"].get(key, default) == value for key, value, default in (
                ("summary", text.summary, ""), ("questions", text.questions, []),
                ("related", text.related, [])))
        generated_at = source["front_matter"]["generated"]["at"] if same else _now()
        proofread = self.proofread_pages.get(job.page_id)
        if proofread is not None:
            language_edits = proofread.language_edits
        elif source is not None and text.body == strip_definitions(source["body_md"]):
            language_edits = source.get("language_edits")  # the body it describes is kept
        else:
            language_edits = None
        return self._item(job, body=text.body, summary=text.summary, questions=text.questions,
                          related=text.related, points=text.points, left_out=text.left_out,
                          generated_at=generated_at, language_edits=language_edits)

    def _kept(self, job: Job) -> dict[str, Any]:
        """An unchanged page: the step's own body and points, front matter rebuilt from the
        effective page (its citations and model-owned fields), ``generated.at`` unchanged."""
        source = current_text(job.source)
        old_page = self.old_pages[job.page_id]
        own = current_text(old_page)
        proofread = self.proofread_pages.get(job.page_id)
        item = self._item(
            job, body=job.source["body_md"], summary=source["summary"],
            questions=source["questions"],
            related=[{"slug": r["page_id"], "relation": r["relation"]} for r in source["related"]],
            points=own["points"], left_out=own["left_out"],
            generated_at=job.source["front_matter"]["generated"]["at"],
            language_edits=proofread.language_edits if proofread else old_page.get("language_edits"))
        return {**item, "body_md": proofread.body if proofread else old_page["body_md"]}

    def payload(self, texts: Mapping[str, PageText], sources: tuple[Path, ...],
                models: Mapping[str, str]) -> dict[str, Any]:
        pages: dict[str, Any] = dict(self.carried)
        states = {pid: self.old_states[pid] for pid in self.carried if pid in self.old_states}
        for job in self.jobs:
            if job.mode == "keep":
                pages[job.page_id] = self._kept(job)
                states[job.page_id] = job.state
            elif job.page_id in texts:
                text = texts[job.page_id]
                pages[job.page_id] = self._from_text(job, text)
                states[job.page_id] = {**job.state, "mode": job.mode, "report": text.report}
                self.stats["no_change" if not text.changed else job.mode] += 1
            elif job.page_id in self.old_pages and job.page_id in self.old_states:
                pages[job.page_id] = self.old_pages[job.page_id]
                states[job.page_id] = self.old_states[job.page_id]
        order = [pid for pid in self.corpus.plan["pages"] if pid in pages]
        pages = {pid: pages[pid] for pid in order}
        states = {pid: states[pid] for pid in order if pid in states}
        inputs = {pid: input_fingerprint(states[pid]) for pid in states}
        self.stats["kept"] = sum(1 for job in self.jobs if job.mode == "keep") + len(self.carried)
        reports = [s["report"] for s in states.values() if "report" in s]
        withheld = self.corpus.withheld_list()
        return {
            "pages": pages,
            "metadata": {
                "sources": {name: str(path) for name, path in
                            zip(("groups", "plan", "assignments"), sources)},
                "inputs": inputs,
                "pages": states,
                "as_of": self.corpus.as_of,
                "models": dict(models),
                "config": PAGES_CONFIG,
                "git_commit": _git_commit(),
                "generated_at": _now(),
                "run": {**self.stats, "failed": self.failed,
                        "proofread_failed": {pid: p.language_edits["failed"]
                                             for pid, p in self.proofread_pages.items() if p.failed},
                        "skipped_groups": len(self.corpus.skipped)},
                "summary": {
                    "pages": len(pages),
                    # a page written before task 27b keeps its old report (no points)
                    "left_out_share": {pid: s["report"]["left_out_share"]
                                       for pid, s in states.items()
                                       if "left_out_share" in s.get("report", {})},
                    "unaccounted": sum(len(r.get("unaccounted", [])) for r in reports),
                    "uncited_required": sum(len(r["uncited_required"]) for r in reports),
                    "withheld": len(withheld),
                },
                "withheld": withheld,
            },
        }


def _unchanged(payload: Mapping[str, Any], before: Mapping[str, Any], original: Path,
               inputs: tuple[Path, ...]) -> bool:
    """Same pages and per-page state as before, and the output is not older than its inputs."""
    old_meta = before.get("metadata") or {}
    newest_input = max(path.stat().st_mtime_ns for path in inputs)
    return (original.is_file() and original.stat().st_mtime_ns >= newest_input
            and payload["pages"] == before.get("pages")
            and all(payload["metadata"][key] == old_meta.get(key) for key in COMPARED_METADATA))


def run(
    *,
    aggregated_path: Path | None = None,
    plan_path: Path | None = None,
    assignments_path: Path | None = None,
    artifact: Artifact = WIKI_PAGES,
    only: Collection[str] | None = None,
    rebuild: bool = False,
    estimate: bool = False,
    llm: LLMClient | None = None,
    update_llm: LLMClient | None = None,
    proofread_llm: LLMClient | None = None,
    count_tokens: TokenCounter | None = None,
) -> dict[str, Any]:
    """Write, update or keep every planned page; see the module docstring.

    Inputs default to the effective (edited) groups, plan and assignments. ``only``: work on
    these page ids only (every other page stays as it is). ``rebuild``: write every page (in
    ``only``) in full; without ``only`` the edited file is backed up and replaced.
    ``estimate``: no writing call; returns the input tokens of every call the run would make.
    ``llm`` / ``update_llm`` / ``proofread_llm`` default to the ``pages.write`` /
    ``pages.update`` / ``proofread`` stages. Returns the run counters (``write``, ``update``,
    ``no_change``, ``kept``, ``dropped``, ``no_groups``, ``failed``, ``proofread``,
    ``language_edits``, ``proofread_failed``, ``skipped_groups``), ``summary``, ``written``
    and ``backup``.
    """
    logger = setup_step_logging(STEP_7)
    corpus = Corpus.load(aggregated_path, plan_path, assignments_path)
    withheld = corpus.withheld_list()
    logger.info(f"Withheld from every page: {len(withheld)} groups (negative, about a named "
                f"entity, fewer than {WITHHOLD_CONFIG['min_supporters']} members) on "
                f"{len({w['page_id'] for w in withheld})} pages: "
                f"{[w['group_id'] for w in withheld]}")
    if corpus.skipped:
        logger.warning(f"{len(corpus.skipped)} claim groups have no assignment record (run the "
                       "assignment step); they are on no page this run")
    llm = llm or LLMClient.for_stage(WRITE_CONFIG["stage"], logger=logger,
                                     max_tokens=WRITE_CONFIG["max_output_tokens"])
    update_llm = update_llm or LLMClient.for_stage(UPDATE_CONFIG["stage"], logger=logger,
                                                   max_tokens=UPDATE_CONFIG["max_output_tokens"])
    full_rebuild = rebuild and only is None
    previous = {} if full_rebuild else load_previous(artifact)
    edited = None if full_rebuild else _read(artifact.edited)
    effective = (edited or previous).get("pages") or {}
    job = PagesRun(corpus, previous, effective, model=llm.model, rebuild=rebuild, only=only,
                   logger=logger)
    job.plan()
    if estimate:
        return job.estimate(count_tokens or AnthropicTokenCounter(llm.model))
    texts = job.write(PageWriter(llm, update_llm, corpus.plan, logger))
    models = {"write": llm.model, "update": update_llm.model}
    if PROOFREAD_CONFIG["enabled"]:
        proofread_llm = proofread_llm or LLMClient.for_stage(
            PROOFREAD_CONFIG["stage"], logger=logger, max_tokens=PROOFREAD_CONFIG["max_output_tokens"])
        texts = job.proofread(texts, Proofreader(proofread_llm, logger))
        models["proofread"] = proofread_llm.model
    payload = job.payload(texts, corpus.sources, models)
    result = {**payload["metadata"]["run"], "summary": payload["metadata"]["summary"], "backup": None}
    if not full_rebuild and _unchanged(payload, previous, artifact.original, corpus.sources):
        logger.info(f"Pages unchanged ({len(payload['pages'])} pages); nothing written")
        return {**result, "written": False}
    report = write_reconciled(artifact, payload, items_key="pages", id_key=None,
                              refresh_keys=REFRESH_KEYS, rebuild=full_rebuild)
    logger.info(f"Pages written: {len(payload['pages'])} pages; run {payload['metadata']['run']}; "
                f"backup {report.backup}")
    return {**result, "written": True, "backup": str(report.backup) if report.backup else None}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Step 7: write the wiki pages (C9).")
    parser.add_argument("--rebuild", action="store_true",
                        help="write every page in full (after backing up the edited file)")
    parser.add_argument("--only", nargs="+", metavar="PAGE_ID", help="work on these pages only")
    parser.add_argument("--estimate", action="store_true",
                        help="count the input tokens of the calls a run would make; write nothing")
    args = parser.parse_args(argv)
    run(rebuild=args.rebuild, only=args.only, estimate=args.estimate)


if __name__ == "__main__":
    main()
