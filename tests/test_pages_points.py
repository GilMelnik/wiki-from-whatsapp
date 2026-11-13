"""The page's points and the checks that replace the cited-share floor (task 27b), offline.

The owner's own example (2026-11-11: a paragraph that tells the same recommendation again and
again, and the reduction they asked for) is the test case of the "told once" check. Every
other group is synthetic (``pages_fixtures``).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest

from step_7_write_pages.checks import prose_words, problems, repair, rules_for, statements
from step_7_write_pages.inputs import PAGES_CONFIG, Corpus, PageContext
from step_7_write_pages.points import KINDS, REASONS, account, point_report, tidy
from step_7_write_pages.prompts import (
    UPDATE_SCHEMA,
    UPDATE_SYSTEM,
    WRITE_SCHEMA,
    WRITE_SYSTEM,
    write_prompt,
)
from step_7_write_pages.writer import PageWriter
from tests.pages_fixtures import FakeWriter, PagesWorkspace
from utils.llm_client import _flatten

LOGGER = logging.getLogger("test-points")
LEAD = "פתיח קצר על הדף.\n\n## מתי להגיע ליעד לפני הלידה\n\n"


def _g(number: int) -> str:
    return f"g-{number:08x}"


def _m(*numbers: int) -> str:
    return "".join(f"[^{_g(n)}]" for n in numbers)


# The owner's paragraph (task 27b's brief), its footnotes 1-9 as marks of groups 1-9.
LONG = LEAD + (
    "ההמלצה הנפוצה בקבוצה היא להגיע ליעד סביב שבועות 36–37 להריון. בשנת 2022 חברים המליצו "
    f"להגיע כשלושה שבועות לפני התאריך המשוער{_m(1)}, ובמאי 2024 נאמרה אותה המלצה, עם הערה "
    f"שבלידה מוקדמת ייתכן צורך בהקדמה דחופה של הטיסות{_m(2)}. ביולי 2023 שלושה חברים ציינו טווח "
    "של שבוע 36 עד 38: שבוע 36 בהיריון תאומים ושבוע 38 בהיריון יחיד, בהתאם להיסטוריה הרפואית "
    f"של הפונדקאית{_m(3)}. באוקטובר 2023 חמישה חברים הדגישו שהמועד תלוי בסוג ההיריון, "
    "בהיסטוריית הלידות של הפונדקאית ובסיבוכים כמו רעלת היריון, והורים רבים בוחרים להגיע סביב "
    f"שבועות 36–37{_m(4)}. עוד ציינו חברים שבהריון יחיד אפשר להגיע בשבועות 36–38, וכך נמנעים "
    f"מהחלפת טיסות ודירות ברגע האחרון{_m(5)}. בהריון תאומים הוצע להגיע עד שבוע 35{_m(6)}, "
    f"ובאוקטובר 2024 חבר אחד המליץ על שבועות 36–37{_m(7)}.\n"
    "המלצה עדכנית יותר, מאפריל 2025 ובגיבוי שבעה חברים: להגיע לכל המאוחר בשבוע 37, ואף בשבוע "
    "36 למי שיכול לעבוד מרחוק, כדי להימנע מעלויות גבוהות של שינוי טיסות במקרה של לידה "
    f"מוקדמת{_m(8)}. בפברואר 2025 ארבעה חברים הזהירו שתכנון טיולים מורכבים או טיסות פנים "
    "בארה״ב בשבועות שלפני התאריך המשוער מסכן בהקפצה פתאומית, והציעו לשכור דירה כבסיס קבוע "
    f"ולצאת ממנה רק לגיחות קצרות בסופי שבוע{_m(9)}.\n"
)
# The reduction the owner asked for: the common view once, then only what adds something.
SHORT = LEAD + (
    f"ההמלצה הנפוצה בקבוצה היא להגיע ליעד סביב שבועות 36–37 להריון.{_m(8, 7, 1)} חברים הדגישו "
    "שהמועד תלוי בסוג ההיריון, בהיסטוריית הלידות של הפונדקאית ובסיבוכים כמו רעלת "
    f"היריון.{_m(4)} בהריון תאומים הוצע להגיע עד שבוע 35.{_m(6, 3)} חברים הזהירו שתכנון "
    "טיולים מורכבים או טיסות פנים בארה״ב בשבועות שלפני התאריך המשוער מסכן בהקפצה פתאומית, "
    f"והציעו לשכור דירה כבסיס קבוע ולצאת ממנה רק לגיחות קצרות בסופי שבוע.{_m(9)}\n"
)
POINTS = [
    {"kind": "common_view", "statement": "להגיע סביב שבועות 36–37",
     "groups": [_g(8), _g(7), _g(1), _g(2), _g(5)]},
    {"kind": "condition", "statement": "המועד תלוי בסוג ההיריון ובסיבוכים", "groups": [_g(4)]},
    {"kind": "number", "statement": "בתאומים מוקדם יותר, עד שבוע 35", "groups": [_g(6), _g(3)]},
    {"kind": "warning", "statement": "לא לתכנן טיולים מורכבים לפני התאריך", "groups": [_g(9)]},
]


def _context(ws: PagesWorkspace, page_id: str) -> tuple[Corpus, PageContext]:
    corpus = Corpus.load(ws.groups_path, ws.plan_path, ws.assignments_path)
    return corpus, corpus.context(page_id, corpus.catalogue())


def _answer(body: str, points: list[dict[str, Any]],
            left_out: tuple[dict[str, str], ...] | list[dict[str, str]] = ()) -> dict[str, Any]:
    return {"points": points, "left_out": list(left_out), "body": body, "summary": "ס",
            "questions": ["ש?"], "related": []}


@pytest.fixture()
def example(tmp_path: Path) -> tuple[Corpus, PageContext]:
    """Groups 1-9 (no disputes) are the main groups of ``hospital``."""
    ws = PagesWorkspace(tmp_path)
    groups = ws.groups()
    for group in groups:
        group["opposer_count"], group["contradicts"] = 0, []
    ws.write_groups(groups)
    payload = ws.assignments()
    for index, group in enumerate(groups):
        payload["assignments"][group["group_id"]]["pages"] = ["hospital"] if 1 <= index <= 9 else []
    ws.write_assignments(payload)
    return _context(ws, "hospital")


@pytest.fixture()
def disputes(tmp_path: Path) -> tuple[Corpus, PageContext]:
    """``hospital``: groups 3 and 6 contradict each other (a linked pair), 7 is disputed by
    one member with no answering group (a bare count)."""
    ws = PagesWorkspace(tmp_path)
    payload = ws.assignments()
    payload["assignments"][_g(6)]["pages"] = ["hospital"]
    ws.write_assignments(payload)
    return _context(ws, "hospital")


def _rules(corpus: Corpus, context: PageContext, frozen: tuple[str, ...] = ()) -> Any:
    return rules_for(context.page_id, context.records, corpus.plan,
                     required=context.required_ids(), frozen=frozen)


# ------------------------------------------------------------------ schema and prompt


def test_the_answer_lists_the_points_before_the_body() -> None:
    assert list(WRITE_SCHEMA["properties"]) == ["points", "left_out", "body", "summary",
                                                "questions", "related"]
    assert list(UPDATE_SCHEMA["properties"]) == ["points", "left_out", "no_change", "body",
                                                 "summary", "questions", "related"]
    point = WRITE_SCHEMA["properties"]["points"]["items"]
    assert point["required"] == ["kind", "statement", "groups"]
    assert point["properties"]["kind"]["enum"] == list(PAGES_CONFIG["points"]["kinds"]) == list(KINDS)
    reason = WRITE_SCHEMA["properties"]["left_out"]["items"]["properties"]["reason"]
    assert reason["enum"] == list(REASONS) == ["repeats", "trivial", "off_scope"]


def test_the_system_prompt_carries_the_owners_rules() -> None:
    for system in (WRITE_SYSTEM, UPDATE_SYSTEM):
        assert all(f"`{kind}`" in system for kind in KINDS)
        assert "ONE statement per point" in system
        assert "there is no limit on the marks of a sentence" in system
        assert "per sentence" not in system
        assert "ONLY for prices" in system and "do NOT add or contrast their counts" in system
        assert "ההמלצה הנפוצה בקבוצה היא להגיע ליעד סביב שבועות 36–37 להריון." in system
        assert "{" not in system.split("## What you return")[0]  # every placeholder filled
    assert "`points` and `left_out` are returned in full even then" in UPDATE_SYSTEM


# ------------------------------------------------------------------ the owner's example


def test_the_owners_paragraph_fails_and_their_reduction_passes(example: tuple[Corpus, PageContext]
                                                                ) -> None:
    corpus, context = example
    rules = _rules(corpus, context)
    assert context.main_ids == [_g(n) for n in range(1, 10)]
    found = problems(_answer(LONG, POINTS), rules)
    assert len(found) == 1 and "told in more than 2 statements" in found[0]
    assert "«להגיע סביב שבועות 36–37» (4 statements)" in found[0]
    assert problems(_answer(SHORT, POINTS), rules) == []
    report = point_report(POINTS, [], statements(SHORT), rules.main_ids)
    assert report == {"points": 4, "points_by_kind": {"common_view": 1, "condition": 1,
                                                      "number": 1, "warning": 1},
                      "left_out": 0, "left_out_share": 0.0, "left_out_by_reason": {},
                      "unaccounted": [], "statements": 4, "marks": 7,
                      "retold": 0, "uncarried": 0}
    assert prose_words(SHORT) < 0.4 * prose_words(LONG)


def test_the_retry_names_the_retold_point_and_the_reduction_is_kept(
        example: tuple[Corpus, PageContext]) -> None:
    corpus, context = example
    answers = iter([_answer(LONG, POINTS), _answer(SHORT, POINTS)])
    llm = FakeWriter(respond=lambda system, user, task: next(answers))
    text = PageWriter(llm, FakeWriter(), corpus.plan, LOGGER).write(context)
    retry = _flatten(llm.calls[1]["user"]).split("broke these rules:")[1]
    assert "told in more than 2 statements" in retry and "להגיע סביב שבועות 36–37" in retry
    assert text.report["calls"] == 2 and text.report["final_problems"] == []
    assert text.body == SHORT and text.points == POINTS and text.left_out == []


# ------------------------------------------------------------------ each check


def test_accounting_names_missing_repeated_and_foreign_groups(example: tuple[Corpus, PageContext]
                                                              ) -> None:
    corpus, context = example
    rules = _rules(corpus, context)
    points = [{**POINTS[0], "groups": [_g(8), _g(7), _g(1), _g(2), "g-deadbeef"]},
              {**POINTS[1], "groups": [_g(4), _g(7)]}, {**POINTS[2], "groups": []}, POINTS[3]]
    found = problems(_answer(SHORT, points, [{"group": _g(3), "reason": "trivial"}]), rules)
    assert any("in no point" in p and _g(5) in p and _g(6) in p for p in found)
    assert any("more than once" in p and _g(7) in p for p in found)
    assert any("not main groups" in p and "g-deadbeef" in p for p in found)
    assert any("have no groups" in p for p in found)
    assert any("cites groups listed in left_out" in p and _g(3) in p for p in found)
    accounting = account(points, [{"group": _g(3), "reason": "bored"}], context.main_ids)
    assert accounting.reasons == ("bored",) and accounting.missing == (_g(5), _g(6))


def test_a_point_the_body_never_says_and_a_statement_with_many_marks(
        example: tuple[Corpus, PageContext]) -> None:
    corpus, context = example
    rules = _rules(corpus, context)
    heavy = SHORT.replace(_m(8, 7, 1), _m(8, 7, 1, 2))
    assert problems(_answer(heavy, POINTS), rules) == []
    silent = SHORT.replace(_m(9), "")
    found = problems(_answer(silent, POINTS), rules)
    assert len(found) == 1 and "carried by no statement" in found[0]
    assert "«לא לתכנן טיולים מורכבים לפני התאריך» (g-00000009)" in found[0]


def test_kept_sentences_of_an_update_are_not_held_to_the_new_rules(
        example: tuple[Corpus, PageContext]) -> None:
    corpus, context = example
    old = SHORT.replace(f"להריון.{_m(8, 7, 1)}", f"להריון.{_m(8, 7, 1)} עוד פעם.{_m(2)} ועוד.{_m(5)}")
    assert problems(_answer(old, POINTS), _rules(corpus, context))
    frozen = tuple(s.text for s in statements(old))
    assert problems(_answer(old, POINTS), _rules(corpus, context, frozen)) == []
    added = f"{old.rstrip()} עוד משפט חדש.{_m(8)}\n"
    found = problems(_answer(added, POINTS), _rules(corpus, context, frozen))
    assert len(found) == 1 and "«להגיע סביב שבועות 36–37» (4 statements)" in found[0]


def test_left_out_groups_are_reported_and_a_large_share_is_warned_about(
        example: tuple[Corpus, PageContext], caplog: pytest.LogCaptureFixture) -> None:
    corpus, context = example
    left = [{"group": _g(n), "reason": "trivial"} for n in (1, 2, 5, 7)]
    left[0]["reason"] = "repeats"
    points = [{**POINTS[0], "groups": [_g(8)]}, *POINTS[1:]]
    body = SHORT.replace(_m(8, 7, 1), _m(8))
    llm = FakeWriter(respond=lambda system, user, task: _answer(body, points, left))
    with caplog.at_level(logging.WARNING):
        text = PageWriter(llm, FakeWriter(), corpus.plan, LOGGER).write(context)
    assert text.report["final_problems"] == [] and text.left_out == left
    assert text.report["left_out_by_reason"] == {"repeats": 1, "trivial": 3}
    assert text.report["left_out_share"] == 0.444
    assert "44% of the main groups were left out" in caplog.text


def test_repair_keeps_only_main_groups_each_once(example: tuple[Corpus, PageContext]) -> None:
    corpus, context = example
    points = [{**POINTS[0], "groups": [*POINTS[0]["groups"], "g-deadbeef", _g(8)]}, *POINTS[1:],
              {"kind": "fact", "statement": "ריקה", "groups": ["g-deadbeef"]}]
    fixed, repairs = repair(_answer(SHORT, points, [{"group": _g(9), "reason": "trivial"}]),
                            _rules(corpus, context))
    assert fixed["points"][0]["groups"] == POINTS[0]["groups"]
    assert fixed["points"][1:] == POINTS[1:] and fixed["left_out"] == []
    assert any("g-deadbeef" in r and _g(9) in r for r in repairs)
    tidied = tidy(POINTS, [], [_g(n) for n in range(1, 9)])
    assert tidied.emptied == [POINTS[3]] and len(tidied.points) == 3


# ------------------------------------------------------------------ disputes


def test_a_linked_dispute_is_one_point_and_both_sides_are_cited(
        disputes: tuple[Corpus, PageContext]) -> None:
    corpus, context = disputes
    rules = _rules(corpus, context)
    assert context.main_ids == [_g(3), _g(6), _g(7)]
    assert context.required_ids() == [_g(3), _g(6), _g(7)]
    points = [{"kind": "dispute", "statement": "שני צדדים", "groups": [_g(3), _g(6)]},
              {"kind": "common_view", "statement": "המלצה", "groups": [_g(7)]}]
    body = (f"פתיח.\n\n## א\n\nחבר המליץ על הדרך.{_m(3)} חבר אחר השיב שהיא נכשלה אצלו.{_m(6)} "
            f"רוב החברים ממליצים על המרפאה.{_m(7)}\n")
    assert problems(_answer(body, points), rules) == []
    one_side = body.replace(f" חבר אחר השיב שהיא נכשלה אצלו.{_m(6)}", "")
    found = problems(_answer(one_side, points), rules)
    assert any("disputed or contradicted but not cited" in p and _g(6) in p for p in found)
    bare = body.replace(_m(7), "")  # a group with one opposer and no answering group
    found = problems(_answer(bare, points), rules)
    assert any("disputed or contradicted but not cited" in p and _g(7) in p for p in found)
    user = _flatten(write_prompt(context)[1])
    assert f"{_g(3)} | main |" in user and f"contradicts: {_g(6)}" in user
