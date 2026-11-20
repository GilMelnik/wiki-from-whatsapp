"""The two model calls of step 8: write a page in full, or update it from its current text.

``PageWriter.write(context)`` and ``PageWriter.update(context, current, delta)`` are the whole
contract (tasks 26 and 50 build on ``update``). Both return a ``PageText``: the page's points
and the claims left out (``step_8_write_pages.points``), the body with its ``[^g-…]`` marks
and no footnote definitions (step 11 renders the references from the claims), the summary,
the questions, ``related`` in the C9 form ``[{slug, relation}]``, whether the text changed,
and a ``report`` (calls, problems found, repairs, points by kind, claims left out and why,
statements, marks, uncited disputed claims). Each answer is checked
(``step_8_write_pages.checks``); a failing answer gets ONE retry that names the problems, then
whatever is still wrong is repaired and recorded. A page leaving out more than
``pages.max_left_out_share`` of its main claims is logged as a warning.

A call is prepared first (``write_call`` / ``update_call``: a ``PageCall`` holding the client,
the prompts, the schema and the rules) and then completed: one sync call after the other
(``complete``), or with every page's call in one batch per round
(``step_8_write_pages.batch``). Both routes check an answer with the same methods
(``checked``, ``retry_problems``, ``finish``), so they send the same requests and treat the
same answer the same way.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from step_8_write_pages.checks import (
    AnswerRules,
    problems,
    prose_words,
    repair,
    rules_for,
    sentences,
    statements,
    strip_definitions,
    uncited_required,
)
from step_8_write_pages.inputs import MAIN, PAGES_CONFIG, PageContext
from step_8_write_pages.points import LeftOut, Point, point_report
from step_8_write_pages.prompts import (
    UPDATE_SCHEMA,
    WRITE_SCHEMA,
    retry_prompt,
    update_prompt,
    write_prompt,
)
from utils.llm_client import CacheSegment, LLMClient
from utils.wiki_links import find_wiki_links


@dataclass(frozen=True)
class ClaimDelta:
    """What changed on a page since its text was written: short ids new to it, changed
    (record fingerprint differs) and removed, and whether its title / description / scope
    changed."""

    new: tuple[str, ...] = ()
    changed: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    brief_changed: bool = False


@dataclass
class PageText:
    """A written page's model-owned parts (see the module docstring)."""

    body: str
    summary: str
    questions: list[str]
    related: list[dict[str, str]]
    points: list[Point] = field(default_factory=list)
    left_out: list[LeftOut] = field(default_factory=list)
    changed: bool = True
    report: dict[str, Any] = field(default_factory=dict)


def current_text(page: Mapping[str, Any]) -> dict[str, Any]:
    """The model-owned parts of a stored C9 page, as an update reads them."""
    front_matter = page["front_matter"]
    return {
        "body": strip_definitions(page["body_md"]),
        "summary": front_matter.get("summary", ""),
        "questions": list(front_matter.get("questions", [])),
        "related": [{"page_id": r["slug"], "relation": r["relation"]}
                    for r in front_matter.get("related", [])],
        "points": list(page.get("points", [])),
        "left_out": list(page.get("left_out", [])),
    }


@dataclass(frozen=True)
class PageCall:
    """One page's request to the model and the rules its answer is checked by."""

    llm: LLMClient
    system: str
    user: list[CacheSegment]
    schema: dict[str, Any]
    rules: AnswerRules
    context: PageContext
    task: str
    current: Mapping[str, Any] | None


class PageWriter:
    """Writes and updates pages with the ``write`` and ``update`` stage clients."""

    def __init__(self, write_llm: LLMClient, update_llm: LLMClient, plan: Mapping[str, Any],
                 logger: logging.Logger) -> None:
        self.write_llm = write_llm
        self.update_llm = update_llm
        self.plan = plan
        self.logger = logger

    def write_call(self, context: PageContext) -> PageCall:
        """The call that writes the page in full from its claims (stage ``write``)."""
        rules = rules_for(context.page_id, context.records, self.plan,
                          required=context.required_ids(), withheld=context.withheld)
        system, user = write_prompt(context)
        return PageCall(self.write_llm, system, user, WRITE_SCHEMA, rules, context,
                        task=f"write:{context.page_id}", current=None)

    def update_call(self, context: PageContext, current: Mapping[str, Any], delta: ClaimDelta,
                    request: str = "") -> PageCall:
        """The call that updates the page from ``current`` (stage ``update``); see ``update``."""
        rules = rules_for(context.page_id, context.records, self.plan,
                          required=context.required_ids(), frozen=sentences(current["body"]),
                          withheld=context.withheld)
        system, user = update_prompt(context, current, new=delta.new, changed=delta.changed,
                                     removed=delta.removed, brief_changed=delta.brief_changed,
                                     request=request)
        return PageCall(self.update_llm, system, user, UPDATE_SCHEMA, rules, context,
                        task=f"update:{context.page_id}", current=current)

    def write(self, context: PageContext) -> PageText:
        """The page in full from its claims (stage ``write``), by sync calls."""
        return self.complete(self.write_call(context))

    def update(self, context: PageContext, current: Mapping[str, Any], delta: ClaimDelta,
               request: str = "") -> PageText:
        """The page updated from ``current`` (stage ``update``), by sync calls.

        ``current``: the current EFFECTIVE page as ``current_text`` returns it (body with marks
        but no definitions, summary, questions, related as ``[{page_id, relation}]``, points,
        left_out), so the owner's edits are the starting text and the stored points the
        starting list. ``delta``: the new / changed / removed short ids (``context`` holds the
        page's claims now). Returns the whole updated page with the whole updated points list;
        when the model answers "no change needed" the current text with ``changed=False`` and
        the points as the model returned them (a new claim that joined a point). Checks: those
        of a write over the whole page, except that sentences of the current body are not held
        to the "told once" rule. ``request``: a free-text correction to carry
        out (step 9 applies an accepted review suggestion with an empty ``delta`` and a
        request); the checks are the same.
        """
        return self.complete(self.update_call(context, current, delta, request))

    def complete(self, call: PageCall) -> PageText:
        """The page of ``call`` by sync calls: the answer, ONE retry naming its problems when
        it has any, then ``finish``. A call error or an unusable answer raises."""
        answer = self._ask(call, call.user)
        first = self.retry_problems(call, answer)
        if first:
            answer = self._ask(call, retry_prompt(call.user, answer, first))
        return self.finish(call, answer, first)

    def _ask(self, call: PageCall, user: list[CacheSegment]) -> dict[str, Any]:
        return self.checked(call, call.llm.complete_json(call.system, user, task=call.task,
                                                         response_schema=call.schema))

    @staticmethod
    def checked(call: PageCall, answer: Any) -> dict[str, Any]:
        """The model's ``answer`` as the checks read it (a "no change needed" update gets the
        current text); ``ValueError`` when it has no body or no points / left_out lists."""
        task = call.task
        if not isinstance(answer, dict) or not isinstance(answer.get("body"), str):
            raise ValueError(f"{task}: the model's answer has no body: {str(answer)[:200]}")
        if not all(isinstance(answer.get(key), list) for key in ("points", "left_out")):
            raise ValueError(f"{task}: the model's answer has no points / left_out lists: "
                             f"{str(answer)[:200]}")
        current = call.current
        if current is not None and answer.get("no_change"):
            answer = {**answer, "body": current["body"],
                      **{key: answer.get(key) or current[key]
                         for key in ("summary", "questions", "related")}}
        return answer

    def retry_problems(self, call: PageCall, answer: Mapping[str, Any]) -> list[str]:
        """The problems of a first answer (logged); an answer with any gets ONE retry
        (``retry_prompt``)."""
        first = problems(answer, call.rules)
        if first:
            self.logger.info(f"{call.task}: {len(first)} problem(s), one retry: {first}")
        return first

    def finish(self, call: PageCall, answer: Mapping[str, Any], first: list[str]) -> PageText:
        """The page from its last checked ``answer`` (the retry's when ``first`` holds the
        problems of a first answer): whatever is still wrong is repaired and recorded."""
        rules, context, current, task = call.rules, call.context, call.current, call.task
        calls = 2 if first else 1
        final = problems(answer, rules)
        fixed, repairs = repair(answer, rules)
        no_change = bool(current is not None and answer.get("no_change"))
        body = fixed["body"]
        report = {
            "calls": calls,
            "first_problems": first,
            "final_problems": final,
            "repairs": repairs,
            **point_report(fixed["points"], fixed["left_out"], statements(body), rules.main_ids),
            "uncited_required": uncited_required(body, rules),
            "links": len(find_wiki_links(body)),
            "words": prose_words(body),
            "main_claims": sum(1 for r in context.records if r["role"] == MAIN),
            "claims": len(context.records),
        }
        if final:
            self.logger.warning(f"{task}: still wrong after the retry (repaired / recorded): {final}")
        if report["left_out_share"] > PAGES_CONFIG["max_left_out_share"]:
            self.logger.warning(f"{task}: {report['left_out_share']:.0%} of the main claims were "
                                f"left out ({report['left_out_by_reason']}); check the points")
        return PageText(
            body=body,
            summary=fixed["summary"],
            questions=fixed["questions"],
            related=fixed["related"],
            points=fixed["points"],
            left_out=fixed["left_out"],
            changed=not no_change or _text_differs(fixed, current),
            report=report,
        )


def _text_differs(fixed: Mapping[str, Any], current: Mapping[str, Any]) -> bool:
    """Whether a repaired "no change" answer still changed the page's text (points aside)."""
    related = [{"slug": r["page_id"], "relation": r["relation"]} for r in current["related"]]
    return (fixed["body"] != current["body"].strip() + "\n" or fixed["summary"] != current["summary"]
            or fixed["questions"] != current["questions"] or fixed["related"] != related)


def changed_share(delta: ClaimDelta, claim_count: int) -> float:
    """How much of a page's material changed: (new + changed + removed) / (now + removed)."""
    touched = len(delta.new) + len(delta.changed) + len(delta.removed)
    return touched / max(claim_count + len(delta.removed), 1)

