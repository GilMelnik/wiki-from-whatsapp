"""The two model calls of step 7: write a page in full, or update it from its current text.

``PageWriter.write(context)`` and ``PageWriter.update(context, current, delta)`` are the whole
contract (tasks 26 and 50 build on ``update``). Both return a ``PageText``: the page's points
and the groups left out (``step_7_write_pages.points``), the body with its ``[^g-…]`` marks
and no footnote definitions (step 9 renders the references from the groups), the summary,
the questions, ``related`` in the C9 form ``[{slug, relation}]``, whether the text changed,
and a ``report`` (calls, problems found, repairs, points by kind, groups left out and why,
statements, marks, uncited disputed groups). Each answer is checked
(``step_7_write_pages.checks``); a failing answer gets ONE retry that names the problems, then
whatever is still wrong is repaired and recorded. A page leaving out more than
``pages.max_left_out_share`` of its main groups is logged as a warning.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from step_7_write_pages.checks import (
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
from step_7_write_pages.inputs import MAIN, PAGES_CONFIG, PageContext
from step_7_write_pages.points import LeftOut, Point, point_report
from step_7_write_pages.prompts import (
    UPDATE_SCHEMA,
    WRITE_SCHEMA,
    retry_prompt,
    update_prompt,
    write_prompt,
)
from utils.llm_client import CacheSegment, LLMClient
from utils.wiki_links import find_wiki_links


@dataclass(frozen=True)
class GroupDelta:
    """What changed on a page since its text was written: group ids new to it, changed
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


class PageWriter:
    """Writes and updates pages with the ``write`` and ``update`` stage clients."""

    def __init__(self, write_llm: LLMClient, update_llm: LLMClient, plan: Mapping[str, Any],
                 logger: logging.Logger) -> None:
        self.write_llm = write_llm
        self.update_llm = update_llm
        self.plan = plan
        self.logger = logger

    def write(self, context: PageContext) -> PageText:
        """The page in full from its groups (stage ``write``)."""
        rules = rules_for(context.page_id, context.records, self.plan,
                          required=context.required_ids(), withheld=context.withheld)
        system, user = write_prompt(context)
        return self._complete(self.write_llm, system, user, WRITE_SCHEMA, rules, context,
                              task=f"write:{context.page_id}", current=None)

    def update(self, context: PageContext, current: Mapping[str, Any], delta: GroupDelta,
               request: str = "") -> PageText:
        """The page updated from ``current`` (stage ``update``).

        ``current``: the current EFFECTIVE page as ``current_text`` returns it (body with marks
        but no definitions, summary, questions, related as ``[{page_id, relation}]``, points,
        left_out), so the owner's edits are the starting text and the stored points the
        starting list. ``delta``: the new / changed / removed group ids (``context`` holds the
        page's groups now). Returns the whole updated page with the whole updated points list;
        when the model answers "no change needed" the current text with ``changed=False`` and
        the points as the model returned them (a new group that joined a point). Checks: those
        of a write over the whole page, except that sentences of the current body are not held
        to the "told once" rule. ``request``: a free-text correction to carry
        out (step 8 applies an accepted review suggestion with an empty ``delta`` and a
        request); the checks are the same.
        """
        rules = rules_for(context.page_id, context.records, self.plan,
                          required=context.required_ids(), frozen=sentences(current["body"]),
                          withheld=context.withheld)
        system, user = update_prompt(context, current, new=delta.new, changed=delta.changed,
                                     removed=delta.removed, brief_changed=delta.brief_changed,
                                     request=request)
        return self._complete(self.update_llm, system, user, UPDATE_SCHEMA, rules, context,
                              task=f"update:{context.page_id}", current=current)

    def _ask(self, llm: LLMClient, system: str, user: list[CacheSegment], schema: dict[str, Any],
             task: str, current: Mapping[str, Any] | None) -> dict[str, Any]:
        answer = llm.complete_json(system, user, task=task, response_schema=schema)
        if not isinstance(answer, dict) or not isinstance(answer.get("body"), str):
            raise ValueError(f"{task}: the model's answer has no body: {str(answer)[:200]}")
        if not all(isinstance(answer.get(key), list) for key in ("points", "left_out")):
            raise ValueError(f"{task}: the model's answer has no points / left_out lists: "
                             f"{str(answer)[:200]}")
        if current is not None and answer.get("no_change"):
            answer = {**answer, "body": current["body"],
                      **{key: answer.get(key) or current[key]
                         for key in ("summary", "questions", "related")}}
        return answer

    def _complete(self, llm: LLMClient, system: str, user: list[CacheSegment],
                  schema: dict[str, Any], rules: AnswerRules, context: PageContext, *, task: str,
                  current: Mapping[str, Any] | None) -> PageText:
        answer = self._ask(llm, system, user, schema, task, current)
        first = problems(answer, rules)
        calls = 1
        if first:
            self.logger.info(f"{task}: {len(first)} problem(s), one retry: {first}")
            answer = self._ask(llm, system, retry_prompt(user, answer, first), schema, task, current)
            calls = 2
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
            "main_groups": sum(1 for r in context.records if r["role"] == MAIN),
            "groups": len(context.records),
        }
        if final:
            self.logger.warning(f"{task}: still wrong after the retry (repaired / recorded): {final}")
        if report["left_out_share"] > PAGES_CONFIG["max_left_out_share"]:
            self.logger.warning(f"{task}: {report['left_out_share']:.0%} of the main groups were "
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


def changed_share(delta: GroupDelta, group_count: int) -> float:
    """How much of a page's material changed: (new + changed + removed) / (now + removed)."""
    touched = len(delta.new) + len(delta.changed) + len(delta.removed)
    return touched / max(group_count + len(delta.removed), 1)

