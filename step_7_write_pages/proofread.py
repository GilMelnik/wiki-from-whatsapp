"""The Hebrew language edit: one more call for every page text a model wrote or rewrote.

The stage ``proofread`` (config section ``proofread``: ``enabled``, ``stage``,
``max_output_tokens``) reads one page body as a Hebrew language editor (``SYSTEM``, ``SCHEMA``:
the prompt tested on two pages, 2026-11-13) and answers with a list of replacements ``{old,
new, kind, why}``. Code applies an edit, in the model's order, only when ALL of these hold; an
edit that fails one is dropped and counted under its reason (the keys of ``dropped``), and the
page is never failed for it:

- ``malformed``: ``old`` and ``new`` are text (and ``kind``, ``why``);
- ``no_change``: ``old != new``;
- ``not_unique``: ``old`` occurs exactly once in the body as it stands when the edit is applied;
- ``fixed_changed``: reference marks ``[^…]``, link targets (``[[target|`` / ``[[target]]``)
  and digit runs are the same multiset in ``old`` and ``new``, and in the whole body before
  and after (an ``old`` that cuts a link or a mark in two cannot change it either);
- ``overlap``: it does not touch the text of an edit already applied;
- ``frozen``: it does not touch a sentence an update kept (``AnswerRules.frozen``: the
  sentences of the page's previous effective body, which stay byte-identical so the owner's
  reviewed text does not drift), and every such sentence is still a sentence afterwards.

Then the page must still pass the checks that accepted it (``checks.problems`` with the same
rules): a problem the edits brought in sends the body back to what the writer wrote, the
applied edits counted as ``reverted`` and the problems listed.

Every page's ``language_edits`` record describes its body: ``{model, applied: [{old, new, kind,
why}], dropped: {reason: count}}`` (+ ``reverted: [problem]``), or, when the call gave no
usable answer (an API error, a refusal, a truncated or unparseable answer), ``{model, failed,
frozen}``: the body is the writer's, and ``frozen`` holds the fingerprints of the sentences
that were kept, so the step can send the page again on its next run under the same rule.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import anthropic

from step_7_write_pages.checks import AnswerRules, problems, rules_for, sentence_spans, sentences
from step_7_write_pages.inputs import PageContext
from utils.config import section
from utils.llm_client import BatchRequest, LLMClient
from utils.llm_client.ask import ask_json
from utils.reconcile import fingerprint

PROOFREAD_CONFIG = section("proofread")

SYSTEM = """You are a senior Hebrew language editor (עורך לשון) at an Israeli publisher. \
You edit one article of a Hebrew community wiki about surrogacy for gay men. The article \
was written by a language model, and it shows: the Hebrew is grammatical but in places it \
reads like a translation from English.

Find every place where a careful native editor would change the wording, and give the \
replacement. Look for:
- calques: English idioms, collocations or sentence patterns carried word for word into \
Hebrew, where a native writer would say it differently;
- loanwords where an ordinary Hebrew word is the natural choice in this register \
(keep established professional terms as they are);
- wrong or unnatural prepositions, word order, agreement (gender, number, definiteness), \
construct state;
- heavy noun chains, needless passives and bureaucratic phrasing where plain Hebrew is \
shorter and clearer;
- anything else that makes an Israeli reader feel the text was not written in Hebrew.

Do NOT change what the article says. Keep every fact, number, date, name, hedge and \
attribution ("חברים דיווחו", "נכון ל-2023") exactly. Do not add or remove content, do not \
reorder sentences, do not touch headings unless their Hebrew is wrong. The text is plain, \
neutral and informative: do not make it literary, chatty or promotional. Leave correct, \
natural Hebrew alone: an edit is only worth making when the result is clearly better.

Markup you must keep byte for byte when it falls inside the text you replace: reference \
marks like [^g-30ee5558], links like [[page-id|shown text]] and [[label:usa|shown text]] \
(you may correct the shown text, never the part before the |).

Answer with a list of edits. For each edit:
- old: the exact text to replace, copied character for character from the article, long \
enough to occur only once (a phrase or a clause, not a whole paragraph);
- new: the replacement;
- kind: one of calque, loanword, grammar, heavy, other;
- why: one short sentence in English."""

SCHEMA = {
    "type": "object",
    "properties": {"edits": {"type": "array", "items": {
        "type": "object",
        "properties": {"old": {"type": "string"}, "new": {"type": "string"},
                       "kind": {"type": "string",
                                "enum": ["calque", "loanword", "grammar", "heavy", "other"]},
                       "why": {"type": "string"}},
        "required": ["old", "new", "kind", "why"], "additionalProperties": False}}},
    "required": ["edits"], "additionalProperties": False}

MALFORMED, NO_CHANGE, NOT_UNIQUE = "malformed", "no_change", "not_unique"
FIXED_CHANGED, OVERLAP, FROZEN, REVERTED = "fixed_changed", "overlap", "frozen", "reverted"
_EDIT_KEYS = ("old", "new", "kind", "why")
_FIXED = re.compile(r"\[\^[^\]]+\]|\[\[[^|\]]+(?:\||\]\])|\d+")
NO_ANSWER = "no usable answer (the call failed, was refused or was cut off; see the LLM log)"


def user_message(body: str) -> str:
    return f"<article>\n{body}\n</article>"


def prompt_chars(body_chars: int) -> int:
    """Characters of the prompt that proofreads a body of ``body_chars`` characters."""
    return len(SYSTEM) + len(user_message("")) + body_chars


@dataclass(frozen=True)
class Edited:
    """``apply_edits``' result: the body, the edits applied and the dropped ones by reason."""

    body: str
    applied: list[dict[str, str]]
    dropped: Counter[str]


def _fixed(text: str) -> Counter[str]:
    return Counter(_FIXED.findall(text))


def _once(body: str, old: str) -> int | None:
    """Where ``old`` starts when it occurs exactly once in ``body`` (overlaps counted)."""
    start = body.find(old)
    return start if old and start >= 0 and body.find(old, start + 1) < 0 else None


def _touches(start: int, end: int, span: tuple[int, int]) -> bool:
    """Whether ``[start, end)`` overlaps ``span`` (an empty span: lies strictly inside)."""
    left, right = span
    return start < right and left < end if left < right else start < left < end


def _frozen(body: str, frozen: frozenset[str]) -> tuple[list[tuple[int, int]], Counter[str]]:
    """The spans of ``body``'s kept sentences, and how often each kept sentence occurs."""
    spans = [(start, end) for start, end in sentence_spans(body) if body[start:end] in frozen]
    return spans, Counter(body[start:end] for start, end in spans)


def _refusal(body: str, edit: Any, applied: Sequence[tuple[int, int]],
             frozen: frozenset[str]) -> str | None:
    """Why ``edit`` cannot be applied to ``body`` (see the module docstring), or None."""
    if not isinstance(edit, Mapping) or not all(isinstance(edit.get(k), str) for k in _EDIT_KEYS):
        return MALFORMED
    old, new = edit["old"], edit["new"]
    if old == new:
        return NO_CHANGE
    start = _once(body, old)
    if start is None:
        return NOT_UNIQUE
    end = start + len(old)
    after = body[:start] + new + body[end:]
    if _fixed(old) != _fixed(new) or _fixed(body) != _fixed(after):
        return FIXED_CHANGED
    if any(_touches(start, end, span) for span in applied):
        return OVERLAP
    spans, kept = _frozen(body, frozen)
    if any(_touches(start, end, span) for span in spans) or _frozen(after, frozen)[1] != kept:
        return FROZEN
    return None


def apply_edits(body: str, edits: Sequence[Any], frozen: Collection[str] = ()) -> Edited:
    """``body`` with every safe edit applied, in order (see the module docstring)."""
    kept = frozenset(frozen)
    spans: list[tuple[int, int]] = []  # the applied replacements, in the current body
    applied: list[dict[str, str]] = []
    dropped: Counter[str] = Counter()
    for edit in edits:
        reason = _refusal(body, edit, spans, kept)
        if reason is not None:
            dropped[reason] += 1
            continue
        old, new = edit["old"], edit["new"]
        start = body.index(old)
        end, shift = start + len(old), len(new) - len(old)
        spans = [(left + shift, right + shift) if left >= end else (left, right)
                 for left, right in spans] + [(start, start + len(new))]
        body = body[:start] + new + body[end:]
        applied.append({key: edit[key] for key in _EDIT_KEYS})
    return Edited(body, applied, dropped)


@dataclass(frozen=True)
class ProofreadJob:
    """One page to proofread: its answer as the checks read it (``body``, ``points``,
    ``left_out``) and the rules that accepted it (``rules.frozen``: never edited)."""

    page_id: str
    answer: Mapping[str, Any]
    rules: AnswerRules


def proofread_job(context: PageContext, plan: Mapping[str, Any], answer: Mapping[str, Any],
                  frozen: Collection[str] = ()) -> ProofreadJob:
    """The job of a page the writer answered with ``answer``; ``frozen``: the sentences the
    update kept (none for a page written in full)."""
    rules = rules_for(context.page_id, context.records, plan, required=context.required_ids(),
                      frozen=frozen, withheld=context.withheld)
    return ProofreadJob(context.page_id, answer, rules)


def owed_frozen(record: Mapping[str, Any] | None, body: str) -> list[str] | None:
    """The kept sentences of ``body`` when its ``language_edits`` ``record`` says the last
    call failed (the page is owed a proofreading), else None."""
    if not record or "failed" not in record:
        return None
    wanted = set(record.get("frozen") or [])
    return [sentence for sentence in sentences(body) if fingerprint(sentence) in wanted]


@dataclass(frozen=True)
class Proofread:
    """One page's outcome: the body to store and its ``language_edits`` record."""

    body: str
    language_edits: dict[str, Any]

    @property
    def failed(self) -> bool:
        return "failed" in self.language_edits


class Proofreader:
    """Proofreads the pages of one run with the ``proofread`` stage client: one batch when the
    provider has a batch API (half price), else one sync call per page."""

    def __init__(self, llm: LLMClient, logger: logging.Logger) -> None:
        self.llm = llm
        self.logger = logger

    def run(self, jobs: Sequence[ProofreadJob]) -> dict[str, Proofread]:
        """``{page_id: Proofread}`` for every job (a failed call keeps the job's body)."""
        if not jobs:
            return {}
        requests = [BatchRequest(job.page_id, SYSTEM, user_message(job.answer["body"]),
                                 task=f"proofread:{job.page_id}", response_schema=SCHEMA)
                    for job in jobs]
        self.logger.info(f"Proofreading {len(jobs)} pages ({self.llm.model})")
        error = NO_ANSWER
        try:
            answers = ask_json(self.llm, requests, batch=self.llm.supports_batch(),
                               logger=self.logger)
        except (anthropic.APIError, RuntimeError, ValueError, OSError) as exc:
            answers, error = {}, repr(exc)
        return {job.page_id: self._outcome(job, answers.get(job.page_id), error) for job in jobs}

    def _outcome(self, job: ProofreadJob, answer: Any, error: str) -> Proofread:
        body, model = job.answer["body"], self.llm.model
        if not isinstance(answer, dict) or not isinstance(answer.get("edits"), list):
            why = error if answer is None else f"the answer has no edits list: {str(answer)[:200]}"
            self.logger.error(f"proofread:{job.page_id}: {why}; the body stays as written and "
                              "the page is sent again on the step's next run")
            return Proofread(body, {"model": model, "failed": why,
                                    "frozen": sorted(fingerprint(s) for s in job.rules.frozen)})
        edited = apply_edits(body, answer["edits"], job.rules.frozen)
        record = {"model": model, "applied": edited.applied, "dropped": dict(edited.dropped)}
        if edited.applied:
            before = problems(job.answer, job.rules)
            new = [problem for problem in problems({**job.answer, "body": edited.body}, job.rules)
                   if problem not in before]
            if new:
                self.logger.warning(f"proofread:{job.page_id}: the edits broke a check the page "
                                    f"passed; the body stays as written: {new}")
                dropped = {**edited.dropped, REVERTED: len(edited.applied)}
                return Proofread(body, {"model": model, "applied": [], "dropped": dropped,
                                        "reverted": new})
        self.logger.info(f"proofread:{job.page_id}: {len(edited.applied)} edits applied, dropped "
                         f"{dict(edited.dropped)}")
        return Proofread(edited.body, record)
