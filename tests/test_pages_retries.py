"""The page writer does not pay for retries that code can avoid (task 27c), fake model only.

A statement with too many marks is cut to the cap in code; a dispute or a change may be told in
more statements than another point; the rendered prompts did not change by one character.
Every page here is synthetic.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

import pytest

from step_7_write_pages.checks import AnswerRules, cap_marks, problems, rules_for, statements, uncited_required
from step_7_write_pages.inputs import PAGES_CONFIG, Corpus, PageContext
from step_7_write_pages.points import told_cap
from step_7_write_pages.prompts import retry_prompt, update_prompt, write_prompt
from step_7_write_pages.writer import GroupDelta, PageWriter
from tests.pages_fixtures import FakeWriter, PagesWorkspace
from utils.llm_client import _flatten

LOGGER = logging.getLogger("test-retries")
LEAD = "A short lead.\n\n## Section\n\n"
# (supporters, last date) of groups 1-9; group 6 is disputed by one member.
BACKING = {1: (5, "2023-01-20"), 2: (3, "2024-06-01"), 3: (4, "2023-01-20"), 4: (3, "2024-01-01"),
           5: (1, "2023-01-20"), 6: (0, "2023-01-20"), 7: (1, "2024-06-01"),
           8: (1, "2024-03-01"), 9: (1, "2024-05-01")}


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
    """``hospital`` with groups 1-9 as main groups: the backing of ``BACKING``, group 6 disputed."""
    ws = PagesWorkspace(tmp_path)
    groups = ws.groups()
    for index, group in enumerate(groups):
        group["opposer_count"], group["contradicts"], group["stance"] = int(index == 6), [], "positive"
        if index in BACKING:
            group["supporter_count"], group["last_date"] = BACKING[index]
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


# One statement of five marks (the disputed group 6, the backing 5, 4, 3 and 3), one of four
# marks that are equally backed and differ only in how recent they are.
OVERCAPPED = LEAD + (f"First point told.{_m(4, 2, 3, 1, 6)} Second point told.{_m(5, 7, 8, 9)}\n")
UPDATE_POINTS = _points(("change", [1, 2, 3, 4, 6]), ("fact", [5, 7, 8, 9]))
PAIR = _points(("fact", [1, 2, 3, 4, 6]), ("fact", [5, 7, 8, 9]))
CAPPED = LEAD + f"First point told.{_m(3, 1, 6)} Second point told.{_m(7, 8, 9)}\n"


# ------------------------------------------------------------------ marks cut in code


def test_a_statement_over_the_cap_keeps_disputed_then_most_backed_then_most_recent(
        page: tuple[Corpus, PageContext]) -> None:
    corpus, context = page
    rules = _rules(corpus, context)
    assert context.required_ids() == [_g(6)] and problems(_answer(OVERCAPPED, PAIR), rules)
    fixed, dropped = cap_marks(_answer(OVERCAPPED, PAIR), rules)
    # first statement: group 6 (disputed) and the two most backed (1, 3); the group 2 and 4
    # marks, backed by 3 members each, go. Second: all backed by 1, so the three most recent.
    assert fixed["body"] == CAPPED and dropped == 3
    assert fixed["points"] == PAIR
    assert problems(fixed, rules) == [] and uncited_required(fixed["body"], rules) == []


def test_the_page_passes_with_one_call_and_the_log_says_how_many_marks_went(
        page: tuple[Corpus, PageContext], caplog: pytest.LogCaptureFixture) -> None:
    corpus, context = page
    llm = FakeWriter(respond=lambda system, user, task: _answer(OVERCAPPED, PAIR))
    with caplog.at_level(logging.INFO):
        text = PageWriter(llm, FakeWriter(), corpus.plan, LOGGER).write(context)
    assert len(llm.calls) == 1 and text.report["calls"] == 1
    assert text.report["first_problems"] == [] and text.report["final_problems"] == []
    assert text.body == CAPPED and text.points == PAIR
    assert text.report["over_cap"] == 0 and text.report["uncited_required"] == []
    assert text.report["repairs"] == ["dropped 3 mark(s) beyond the cap of 3 per statement"]
    assert caplog.text.count("dropped 3 mark(s)") == 1


def test_a_disputed_group_is_never_uncited_by_the_repair(page: tuple[Corpus, PageContext]) -> None:
    corpus, context = page
    groups = [dict(record) for record in context.records]
    for record in groups:  # groups 1-4 are all disputed; group 4 is the least backed of them
        record["opposers"] = int(int(record["id"][2:], 16) <= 4)
    rules = rules_for("hospital", groups, corpus.plan, required=[r["id"] for r in groups
                                                                  if r["opposers"]])
    assert rules.required_ids == (_g(1), _g(2), _g(3), _g(4))
    points = _points(("fact", [1, 2, 3, 4]), ("fact", [5, 6, 7, 8, 9]))
    alone = LEAD + f"All four disputed.{_m(1, 2, 3, 4)} The rest.{_m(5, 6, 7)}\n"
    fixed, dropped = cap_marks(_answer(alone, points), rules)
    # group 4 is cited here only, so the statement is left whole and the check fails as before
    assert fixed["body"] == alone and dropped == 0
    assert any("carry more than 3 marks" in p for p in problems(fixed, rules))
    elsewhere = alone.replace(_m(5, 6, 7), _m(5, 6, 4))
    fixed, dropped = cap_marks(_answer(elsewhere, points), rules)
    assert dropped == 1 and _m(1, 2, 3) in fixed["body"]
    assert uncited_required(fixed["body"], rules) == []


def test_kept_sentences_of_an_update_and_unknown_marks_are_left_to_the_checks(
        page: tuple[Corpus, PageContext]) -> None:
    corpus, context = page
    old = f"Old and heavy.{_m(1, 2, 3, 4)}"
    rules = _rules(corpus, context, (old,))
    body = LEAD + f"{old}\nUnknown id.{_m(5, 7, 8)}[^g-deadbeef]\n"
    fixed, dropped = cap_marks(_answer(body, PAIR), rules)
    assert fixed["body"] == body and dropped == 0


# ------------------------------------------------------------------ update and apply


def _current(page_context: PageContext) -> dict[str, Any]:
    old = f"Old and heavy.{_m(1, 2, 3, 4)}"
    return {"body": LEAD + f"{old}\nStill the rest.{_m(5, 6, 7)}\n", "summary": "s",
            "questions": ["q?"], "related": [], "points": UPDATE_POINTS, "left_out": []}


def test_an_update_and_an_apply_cap_marks_the_same_way(page: tuple[Corpus, PageContext]) -> None:
    corpus, context = page
    current = _current(context)
    body = current["body"] + f"A new sentence.{_m(9, 8, 7, 5, 1)}\n"
    # the kept heavy sentence stays as it is; the new one is cut, and no retry is paid for
    expected = current["body"] + f"A new sentence.{_m(9, 7, 1)}\n"
    for request in ("", "fix the wording"):
        llm = FakeWriter(respond=lambda system, user, task: {
            **_answer(body, UPDATE_POINTS), "no_change": False})
        text = PageWriter(FakeWriter(), llm, corpus.plan, LOGGER).update(
            context, current, GroupDelta(new=(_g(9),)), request=request)
        assert len(llm.calls) == 1 and text.report["calls"] == 1 and text.report["final_problems"] == []
        assert text.body == expected
        assert text.report["repairs"] == ["dropped 2 mark(s) beyond the cap of 3 per statement"]


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
    assert PAGES_CONFIG["prompt_version"] == 2
    assert [told_cap(kind) for kind in ("dispute", "change", "fact", "number", "unknown")] == [
        4, 4, 2, 2, 2]


# ------------------------------------------------------------------ the prompts did not change


def test_the_rendered_prompts_are_byte_identical_to_those_before_the_change(
        tmp_path: Path) -> None:
    """The orchestrator re-runs pages on cached model answers; a cache hit needs every prompt
    to be the same text. The hashes were taken on ``v2`` (a68d8ac) before this change."""
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
        "b42919afa23497d7952398ceef69f6486c1e735ac39e07e11899b858c9a39099",
        "84ca509183f78a9eeede5f01c6668568018c4e4d99707f743f041f315d0b1190",
        "7f483a5aef483d15f203ab2369a831c0f9209e2b8d0a96e7be3d314d65489017",
        "b50cedd12b9c2cd805e69e06c632edbd57e575f20d29df31c1de0ad4cd6c210f",
        "b308e3357ddded096f4a3b587feb135e2d85c329dec2d03b1513dd33194ae3be"]
