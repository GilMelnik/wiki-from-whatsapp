"""Carry out one accepted suggestion: change the artifact it is about and let the existing,
incremental steps do the rest.

- ``split`` / ``merge`` / ``add_page`` / ``add_label``: the plan operation, validated by
  ``step_6_plan.operations``, saved to the plan's EDITED file (as the plan tool saves). No page
  is written here: the assignment step and step 7 follow through their stale notices (a page
  that is not in the plan would break step 9).
- ``mistake``: one call of the ``apply`` stage through step 7's own ``PageWriter.update`` with
  the correction as its free-text ``request``, from the page's current EFFECTIVE text. Step 7's
  checks hold (only the page's groups cited, valid links, disputed and contradicted groups
  cited; one retry, then refusal); on top, at least ``wiki_review.apply.min_kept_sentence_share``
  of the page's sentences must stay word for word. The corrected body then gets step 7's
  Hebrew language edit (``step_7_write_pages.proofread``; the corrected pages of one apply in
  one batch), which never touches a sentence the correction kept; a failed call keeps the
  corrected body (its ``language_edits`` say so; step 7 does not send an edited page again).
  The corrected page goes to the pages' EDITED file, the starting text of every later update.
- ``contradiction``: the pairs become manual contradiction links in the claim groups through
  the groups tool's own store (``GroupsStore.set_contradiction``: step 5's ``contradicts_manual``
  rules, ``contradicts`` re-derived at once), so they survive every re-run and rewrite; and
  every page of the suggestion is corrected as for a mistake so both sides show now (the
  pages are written from the groups as they will be, link included).

A refused apply raises ``ApplyRefused`` (Hebrew message) before any file is written. When a
change spans two files, everything is computed and checked first; if the second write fails,
the first file is put back from its backup.
"""

from __future__ import annotations

import copy
import logging
import shutil
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from review_tool.artifacts import GRAPH
from step_5_aggregate.reviewer.store import EditConflict, GroupNotFound, GroupsStore
from step_6_plan.model import PlanError
from step_7_write_pages.checks import sentences, strip_definitions
from step_7_write_pages.front_matter import LabelRule, page_front_matter
from step_7_write_pages.inputs import PAGES_CONFIG, Corpus, PageContext
from step_7_write_pages.proofread import PROOFREAD_CONFIG, Proofreader, proofread_job
from step_7_write_pages.writer import GroupDelta, PageText, PageWriter, current_text
from step_8_review.inputs import REVIEW_CONFIG, ReviewInput, read_json
from step_8_review.proposals import (
    MISTAKE,
    STRUCTURAL,
    effective_proposal,
    operation,
    plain_text,
    problems,
)
from utils.config import LLM_CONFIG
from utils.llm_client import LLMClient
from utils.paths import AUDIT_PATH, PLAN, WIKI_PAGES, Artifact, resolve
from utils.reconcile import backup_file, guarded_write

APPLY_CONFIG = REVIEW_CONFIG["apply"]
MESSAGES = REVIEW_CONFIG["messages"]
_END = ".!?:;, "
_MIN_QUOTED = 10  # a shorter piece (a heading word) never counts as the quoted sentence


class ApplyRefused(Exception):
    """The suggestion cannot be carried out now; ``args[0]`` is a Hebrew sentence for the
    owner, ``details`` what code found. Nothing was written."""

    def __init__(self, message: str, details: Sequence[str] = ()) -> None:
        super().__init__(message)
        self.details = list(details)


class GroupLinker(Protocol):
    """What applying a contradiction needs from the groups tool's store."""

    edited: Path

    def set_contradiction(self, group_id: str, other_id: str, linked: bool) -> dict[str, Any]: ...


@dataclass(frozen=True)
class Correction:
    """A page as step 7's writer corrected it, before the language edit; ``kept``: the
    sentences of the page before the correction (never edited by the language edit)."""

    context: PageContext
    text: PageText
    kept: list[str]
    input_fingerprint: str | None


@dataclass
class ApplyResult:
    """What an apply changed: a Hebrew message and the details (files, edit, kept share)."""

    message: str
    details: dict[str, Any] = field(default_factory=dict)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def kept_share(before: str, after: str, touched: Collection[str], quoted: str = "") -> float:
    """Share of ``before``'s untouched sentences that ``after`` still has word for word.

    Touched (left out): a sentence citing one of the ``touched`` groups, or the sentence the
    correction quotes. The request may change those; everything else must stay."""
    marks = [f"[^{gid}]" for gid in touched]
    target = plain_text(quoted).rstrip(_END)

    def quoted_here(sentence: str) -> bool:
        text = plain_text(sentence).rstrip(_END)
        return bool(target) and len(text) >= _MIN_QUOTED and (text in target or target in text)

    old = [sentence for sentence in sentences(before)
           if not any(mark in sentence for mark in marks) and not quoted_here(sentence)]
    new = set(sentences(after))
    return sum(1 for sentence in old if sentence in new) / max(len(old), 1)


def _pct(share: float) -> str:
    return f"{share:.0%}"


def _restore(path: Path, backup: Path | None) -> None:
    """Put ``path`` back as ``backup`` holds it (``None``: the file did not exist)."""
    if backup is None:
        path.unlink(missing_ok=True)
    else:
        shutil.copy2(backup, path)


def default_linker() -> GroupsStore:
    """The groups tool's store on the pipeline's files (step 5's own rules for links)."""
    return GroupsStore(GRAPH.node("groups"), GRAPH.node("claims"), GRAPH.node("entities"),
                       AUDIT_PATH)


class Applier:
    """Carries out suggestions on the pipeline's files (defaults) or on the given ones."""

    def __init__(self, *, pages_artifact: Artifact = WIKI_PAGES, plan_artifact: Artifact = PLAN,
                 aggregated_path: Path | None = None, assignments_path: Path | None = None,
                 llm: LLMClient | None = None, proofread_llm: LLMClient | None = None,
                 linker: Callable[[], GroupLinker] = default_linker,
                 logger: logging.Logger | None = None) -> None:
        self.pages_artifact = pages_artifact
        self.plan_artifact = plan_artifact
        self.aggregated_path = aggregated_path
        self.assignments_path = assignments_path
        self._llm = llm
        self._proofread_llm = proofread_llm
        self.linker = linker
        self.logger = logger or logging.getLogger(__name__)

    @property
    def llm(self) -> LLMClient:
        if self._llm is None:
            self._llm = LLMClient.for_stage(APPLY_CONFIG["stage"], logger=self.logger,
                                            max_tokens=APPLY_CONFIG["max_output_tokens"])
        return self._llm

    @property
    def proofreader(self) -> Proofreader | None:
        """The language edit of corrected pages; None when ``proofread.enabled`` is off."""
        if not PROOFREAD_CONFIG["enabled"]:
            return None
        if self._proofread_llm is None:
            self._proofread_llm = LLMClient.for_stage(
                PROOFREAD_CONFIG["stage"], logger=self.logger,
                max_tokens=PROOFREAD_CONFIG["max_output_tokens"])
        return Proofreader(self._proofread_llm, self.logger)

    def review(self) -> ReviewInput:
        """The wiki as it is now (effective pages, groups, plan, assignments)."""
        return ReviewInput.load(self.pages_artifact, self.aggregated_path,
                                resolve(self.plan_artifact), self.assignments_path)

    def apply(self, suggestion: Mapping[str, Any]) -> ApplyResult:
        """Carry out ``suggestion`` (its edited proposal when the owner changed it)."""
        kind = suggestion["kind"]
        proposal = effective_proposal(suggestion)
        review = self.review()
        found = problems(kind, proposal, review)
        if found:
            template = MESSAGES["plan_refused" if kind in STRUCTURAL else "bad_proposal"]
            raise ApplyRefused(template.format(errors="; ".join(found)), found)
        if kind in STRUCTURAL:
            return self._plan(kind, proposal, review)
        if kind == MISTAKE:
            return self._mistake(proposal, review)
        return self._contradiction(proposal, review)

    # ------------------------------------------------------------------ the plan

    def _plan(self, kind: str, proposal: Mapping[str, Any], review: ReviewInput) -> ApplyResult:
        op = operation(kind, proposal)
        try:
            edit = op.apply(review.plan)
        except PlanError as exc:
            raise ApplyRefused(MESSAGES["plan_refused"].format(errors="; ".join(exc.errors)),
                               exc.errors) from exc
        plan = edit.plan
        plan["metadata"] = {**plan["metadata"], "edited_by": REVIEW_CONFIG["edited_by"],
                            "edited_at": _now()}
        backup = guarded_write(self.plan_artifact.edited, plan, backup=True)
        self.logger.info(f"Applied {kind}: {op.describe(review.plan)}; saved "
                         f"{self.plan_artifact.edited} (backup {backup})")
        return ApplyResult(MESSAGES["plan_applied"], {
            "file": str(self.plan_artifact.edited), "backup": str(backup) if backup else None,
            "created": list(edit.created), "removed_pages": list(edit.removed_pages),
            "removed_labels": list(edit.removed_labels), "redirects": dict(edit.redirects),
            "reassign": list(edit.reassign)})

    # ------------------------------------------------------------------ pages

    def _corrected(self, corpus: Corpus, review: ReviewInput, page_id: str, request: str,
                   touched: Collection[str], quoted: str = "", *, unchanged_ok: bool = False
                   ) -> tuple[Correction | None, float]:
        """The page corrected by step 7's writer and the kept share; refused when the writer
        failed its checks, changed too much, or changed nothing (unless ``unchanged_ok``: the
        correction is then ``None``)."""
        page = review.all_pages[page_id]
        context = corpus.context(page_id, corpus.catalogue())
        writer = PageWriter(self.llm, self.llm, corpus.plan, self.logger)
        text: PageText = writer.update(context, current_text(page), GroupDelta(), request=request)
        final = text.report.get("final_problems") or []
        if final:
            raise ApplyRefused(MESSAGES["failed_checks"].format(page_id=page_id,
                                                                problems="; ".join(final)), final)
        if strip_definitions(text.body) == strip_definitions(page["body_md"]):
            if unchanged_ok:
                self.logger.info(f"{page_id}: the writer found nothing to change (checks pass)")
                return None, 1.0
            raise ApplyRefused(MESSAGES["no_change"].format(page_id=page_id))
        share = kept_share(page["body_md"], text.body, touched, quoted)
        if share < APPLY_CONFIG["min_kept_sentence_share"]:
            raise ApplyRefused(MESSAGES["too_much_changed"].format(
                page_id=page_id, kept=_pct(share),
                required=_pct(APPLY_CONFIG["min_kept_sentence_share"])))
        self.logger.info(f"Corrected {page_id}: {_pct(share)} of its sentences kept; report "
                         f"{text.report}")
        return Correction(context, text, sentences(page["body_md"]),
                          page.get("input_fingerprint")), share

    def _items(self, corpus: Corpus, corrections: Mapping[str, Correction]) -> dict[str, Any]:
        """The corrected pages as C9 items, after the language edit (one batch)."""
        proofreader = self.proofreader
        jobs = [proofread_job(c.context, corpus.plan, {"body": c.text.body, "points": c.text.points,
                                                       "left_out": c.text.left_out}, c.kept)
                for c in corrections.values()]
        proofread = proofreader.run(jobs) if proofreader is not None else {}
        model = LLM_CONFIG["stages"][PAGES_CONFIG["write"]["stage"]]["model"]
        labels = LabelRule(corpus, list(corpus.plan["pages"]))
        items = {}
        for page_id, correction in corrections.items():
            text, outcome = correction.text, proofread.get(page_id)
            body = outcome.body if outcome is not None else text.body
            front_matter = page_front_matter(
                correction.context, corpus.plan, body=body, summary=text.summary,
                questions=text.questions, related=text.related, labels=labels(page_id),
                model=model, generated_at=_now())
            item = {"front_matter": front_matter, "body_md": body, "points": text.points,
                    "left_out": corpus.accounted_left_out(page_id, text.left_out)}
            if outcome is not None:
                item["language_edits"] = outcome.language_edits
            items[page_id] = {**item, "input_fingerprint": correction.input_fingerprint}
        return items

    def _save_pages(self, items: Mapping[str, dict[str, Any]]) -> Path | None:
        """Write the corrected pages into the pages' EDITED file (created from the original
        when the owner has no edits yet); returns the backup of the previous edited file."""
        artifact = self.pages_artifact
        payload = read_json(artifact.edited) or read_json(artifact.original)
        payload = copy.deepcopy(payload)
        payload["pages"].update(items)
        return guarded_write(artifact.edited, payload, backup=True)

    def _mistake(self, proposal: Mapping[str, Any], review: ReviewInput) -> ApplyResult:
        page_id = proposal["page_id"]
        request = (f"In the sentence «{proposal['sentence'].strip()}»: {proposal['correction']} "
                   f"(groups {', '.join(proposal['group_ids'])})")
        correction, share = self._corrected(review.corpus, review, page_id, request,
                                            proposal["group_ids"], proposal["sentence"])
        backup = self._save_pages(self._items(review.corpus, {page_id: correction}))
        return ApplyResult(MESSAGES["page_applied"].format(page_id=page_id, kept=_pct(share)), {
            "file": str(self.pages_artifact.edited), "backup": str(backup) if backup else None,
            "pages": {page_id: {"kept_sentence_share": round(share, 3)}}})

    # ------------------------------------------------------------------ contradictions

    @staticmethod
    def _pairs(proposal: Mapping[str, Any]) -> list[tuple[str, str]]:
        return [(a, b) for a in proposal["groups_a"] for b in proposal["groups_b"]]

    @staticmethod
    def _linked_corpus(corpus: Corpus, pairs: Sequence[tuple[str, str]]) -> Corpus:
        """The corpus as it reads once the links are saved (pages are written from it)."""
        groups = copy.deepcopy(corpus.groups)
        for a, b in pairs:
            for one, other in ((a, b), (b, a)):
                groups[one]["contradicts"] = sorted({*(groups[one].get("contradicts") or []), other})
        return replace(corpus, groups=groups)  # withheld groups stay withheld

    def _contradiction(self, proposal: Mapping[str, Any], review: ReviewInput) -> ApplyResult:
        pairs = self._pairs(proposal)
        corpus = self._linked_corpus(review.corpus, pairs)
        request = (f"Groups {', '.join(proposal['groups_a'])} and "
                   f"{', '.join(proposal['groups_b'])} contradict each other: {proposal['clash']} "
                   f"{proposal['correction']}")
        touched = [*proposal["groups_a"], *proposal["groups_b"]]
        pages = [pid for pid in proposal["pages"] if review.pages[pid].group_ids & set(touched)]
        corrected = {pid: self._corrected(corpus, review, pid, request, touched, unchanged_ok=True)
                     for pid in pages}
        items = self._items(corpus, {pid: correction for pid, (correction, _) in corrected.items()
                                     if correction is not None})
        try:
            linker = self.linker()
        except (FileNotFoundError, EditConflict) as exc:
            raise ApplyRefused(MESSAGES["groups_refused"].format(error=exc)) from exc
        groups_backup = backup_file(linker.edited)
        try:
            for a, b in pairs:
                linker.set_contradiction(a, b, True)
        except (GroupNotFound, EditConflict) as exc:
            _restore(linker.edited, groups_backup)
            raise ApplyRefused(MESSAGES["groups_refused"].format(error=exc)) from exc
        try:
            backup = self._save_pages(items) if items else None
        except OSError:
            _restore(linker.edited, groups_backup)
            raise
        self.logger.info(f"Linked {pairs} as contradictions in {linker.edited}; corrected "
                         f"{list(corrected)}")
        message = " ".join([MESSAGES["link_applied"], *(
            MESSAGES["page_applied" if pid in items else "page_unchanged"].format(
                page_id=pid, kept=_pct(share)) for pid, (_, share) in corrected.items())])
        return ApplyResult(message, {
            "file": str(self.pages_artifact.edited), "backup": str(backup) if backup else None,
            "groups_file": str(linker.edited),
            "groups_backup": str(groups_backup) if groups_backup else None,
            "links": [list(pair) for pair in pairs],
            "pages": {pid: {"kept_sentence_share": round(share, 3), "changed": pid in items}
                      for pid, (_, share) in corrected.items()}})

