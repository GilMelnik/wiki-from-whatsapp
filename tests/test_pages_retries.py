"""The page writer does not pay for retries that code can avoid (tasks 27c, 27d), fake model only.

A sentence may carry any number of marks; a dispute or a change may be told in more statements
than another point; the rendered prompts are pinned to the character. Every page here is
synthetic.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

import pytest

from step_7_write_pages.checks import AnswerRules, problems, rules_for, statements
from step_7_write_pages.inputs import PAGES_CONFIG, Corpus, PageContext
from step_7_write_pages.points import told_cap
from step_7_write_pages.prompts import retry_prompt, update_prompt, write_prompt
from step_7_write_pages.writer import GroupDelta, PageWriter
from tests.pages_fixtures import FakeWriter, PagesWorkspace
from utils.llm_client import _flatten

LOGGER = logging.getLogger("test-retries")
LEAD = "A short lead.\n\n## Section\n\n"


def _g(number: int) -> str:
    return f"g-{number:08x}"


def _m(*numbers: int) -> str:
    return "".join(f"[^{_g(n)}]" for n in numbers)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _answer(body: str, points: list[dict[str, Any]]) -> dict[str, Any]:
    return {"points": points, "left_out": [], "body": body, "summary": "s", "questions": ["q?"],
            "related": []}


def _points(*groups: tuple[str, list[int]]) -> list[dict[str, Any]]:
    return [{"kind": kind, "statement": f"point {index}", "groups": [_g(n) for n in numbers]}
            for index, (kind, numbers) in enumerate(groups)]


@pytest.fixture()
def page(tmp_path: Path) -> tuple[Corpus, PageContext]:
    """``hospital`` with groups 1-9 as main groups, group 6 disputed."""
    ws = PagesWorkspace(tmp_path)
    groups = ws.groups()
    for index, group in enumerate(groups):
        group["opposer_count"], group["contradicts"], group["stance"] = int(index == 6), [], "positive"
    ws.write_groups(groups)
    payload = ws.assignments()
    for index, group in enumerate(groups):
        payload["assignments"][group["group_id"]]["pages"] = ["hospital"] if 1 <= index <= 9 else []
    ws.write_assignments(payload)
    corpus = Corpus.load(ws.groups_path, ws.plan_path, ws.assignments_path)
    return corpus, corpus.context("hospital", corpus.catalogue())


def _rules(corpus: Corpus, context: PageContext, frozen: tuple[str, ...] = ()) -> AnswerRules:
    return rules_for(context.page_id, context.records, corpus.plan,
                     required=context.required_ids(), frozen=frozen)


# One statement of five marks (the disputed group 6 among them), one of four.
MANY = LEAD + f"First point told.{_m(4, 2, 3, 1, 6)} Second point told.{_m(5, 7, 8, 9)}\n"
PAIR = _points(("fact", [1, 2, 3, 4, 6]), ("fact", [5, 7, 8, 9]))


# ------------------------------------------------------------------ no cap on the marks


def test_a_sentence_may_carry_any_number_of_marks(page: tuple[Corpus, PageContext]) -> None:
    corpus, context = page
    rules = _rules(corpus, context)
    assert context.required_ids() == [_g(6)]
    assert problems(_answer(MANY, PAIR), rules) == []
    everything = LEAD + f"One sentence for all.{_m(*range(1, 10))}\n"
    assert problems(_answer(everything, _points(("fact", [1, 2, 3, 4, 6, 5, 7, 8, 9]))), rules) == []


def test_a_page_with_many_marks_per_sentence_is_written_in_one_call_and_untouched(
        page: tuple[Corpus, PageContext]) -> None:
    corpus, context = page
    llm = FakeWriter(respond=lambda system, user, task: _answer(MANY, PAIR))
    text = PageWriter(llm, FakeWriter(), corpus.plan, LOGGER).write(context)
    assert len(llm.calls) == 1 and text.report["calls"] == 1
    assert text.report["first_problems"] == [] and text.report["final_problems"] == []
    assert text.body == MANY and text.points == PAIR
    assert text.report["repairs"] == [] and text.report["uncited_required"] == []
    assert text.report["marks"] == 9


def test_an_update_and_an_apply_leave_the_marks_of_a_new_sentence_alone(
        page: tuple[Corpus, PageContext]) -> None:
    corpus, context = page
    current = {"body": LEAD + f"Old and heavy.{_m(1, 2, 3, 4)}\nStill the rest.{_m(5, 6, 7)}\n",
               "summary": "s", "questions": ["q?"], "related": [], "left_out": [],
               "points": _points(("change", [1, 2, 3, 4, 6]), ("fact", [5, 7, 8, 9]))}
    body = current["body"] + f"A new sentence.{_m(9, 8, 7, 5, 1)}\n"
    for request in ("", "fix the wording"):
        llm = FakeWriter(respond=lambda system, user, task: {
            **_answer(body, current["points"]), "no_change": False})
        text = PageWriter(FakeWriter(), llm, corpus.plan, LOGGER).update(
            context, current, GroupDelta(new=(_g(9),)), request=request)
        assert len(llm.calls) == 1 and text.report["calls"] == 1
        assert text.report["final_problems"] == [] and text.report["repairs"] == []
        assert text.body == body


# ------------------------------------------------------------------ the cap per kind


def _told(kind: str, times: int, page_context: PageContext) -> dict[str, Any]:
    """Point 0 (group 1, of ``kind``) told ``times`` statements after the lead."""
    told = " ".join(f"Said again {n}.{_m(1)}" for n in range(times))
    points = [{"kind": kind, "statement": "point", "groups": [_g(1)]},
              {"kind": "fact", "statement": "rest", "groups": [_g(n) for n in range(2, 10)]}]
    return _answer(f"{LEAD}{told}\nThe rest.{_m(2, 6)}\n", points)


@pytest.mark.parametrize("kind, passes, fails", [("dispute", 4, 5), ("change", 4, 5),
                                                 ("fact", 2, 3), ("condition", 2, 3)])
def test_a_dispute_and_a_change_may_be_told_in_four_statements(
        page: tuple[Corpus, PageContext], kind: str, passes: int, fails: int) -> None:
    corpus, context = page
    rules = _rules(corpus, context)
    assert problems(_told(kind, passes, context), rules) == []
    found = problems(_told(kind, fails, context), rules)
    cap = told_cap(kind)
    assert len(found) == 1 and found[0].startswith(
        f"these points are told in more than {cap} statements after the lead; say each point "
        "once, and after it only what adds something: «point» ")
    assert found[0].endswith(f"({fails} statements)")


def test_every_cap_in_play_is_named_in_its_own_problem(page: tuple[Corpus, PageContext]) -> None:
    corpus, context = page
    rules = _rules(corpus, context)
    told = " ".join(f"Said again {n}.{_m(1)}" for n in range(5))
    more = " ".join(f"Said once more {n}.{_m(2)}" for n in range(3))
    points = [{"kind": "dispute", "statement": "sides", "groups": [_g(1)]},
              {"kind": "number", "statement": "prices", "groups": [_g(2)]},
              {"kind": "fact", "statement": "rest", "groups": [_g(n) for n in range(3, 10)]}]
    found = problems(_answer(f"{LEAD}{told} {more}\nThe rest.{_m(3, 6)}\n", points), rules)
    assert [p.split(" statements")[0] for p in found] == [
        "these points are told in more than 4", "these points are told in more than 2"]
    assert "«sides» (5 statements)" in found[0] and "«prices» (3 statements)" in found[1]
    assert statements(_answer(f"{LEAD}{told}\n", points)["body"])


def test_the_cap_is_a_default_with_exceptions_per_kind() -> None:
    config = PAGES_CONFIG["max_statements_per_point"]
    assert config == {"default": 2, "by_kind": {"dispute": 4, "change": 4}}
    assert PAGES_CONFIG["prompt_version"] == 3
    assert "max_marks_per_statement" not in PAGES_CONFIG
    assert [told_cap(kind) for kind in ("dispute", "change", "fact", "number", "unknown")] == [
        4, 4, 2, 2, 2]


# ------------------------------------------------------------------ the prompts are pinned


def test_the_rendered_prompts_are_pinned_and_only_the_two_system_prompts_changed_in_27d(
        tmp_path: Path) -> None:
    """The orchestrator re-runs pages on cached model answers; a cache hit needs every prompt
    to be the same text. The user and retry hashes were taken on ``v2`` (a68d8ac) and did not
    change since; the two system hashes were retaken in task 27d (the mark cap left rule 2)."""
    ws = PagesWorkspace(tmp_path)
    corpus = Corpus.load(ws.groups_path, ws.plan_path, ws.assignments_path)
    context = corpus.context("agencies", corpus.catalogue())
    write_system, write_user = write_prompt(context)
    current = {"body": "גוף.\n", "summary": "ס", "questions": ["ש?"], "related": [], "points": [],
               "left_out": []}
    update_system, update_user = update_prompt(context, current, new=(), changed=(), removed=(),
                                               brief_changed=False, request="תקן")
    retry = retry_prompt(write_user, {"body": "x"}, ["a problem"])
    assert [_sha(text) for text in (write_system, update_system, _flatten(write_user),
                                    _flatten(update_user), _flatten(retry))] == [
        "4bd1e7292305a4760b95c3c7918d7c451bc804e733685aacfa085e2def52fadd",
        "aa942a52a41ff4910f3369f44b7c8f3804bc697e00f411609d072061cc77a4fe",
        "7f483a5aef483d15f203ab2369a831c0f9209e2b8d0a96e7be3d314d65489017",
        "b50cedd12b9c2cd805e69e06c632edbd57e575f20d29df31c1de0ad4cd6c210f",
        "b308e3357ddded096f4a3b587feb135e2d85c329dec2d03b1513dd33194ae3be"]
