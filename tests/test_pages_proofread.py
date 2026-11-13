"""The Hebrew language edit inside step 7 and step 8's apply, with fake models on synthetic
pages: what is sent when, what is stored, the owner's text, a failed call, the estimate."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from step_7_write_pages.proofread import PROOFREAD_CONFIG, prompt_chars
from tests.pages_fixtures import (
    FakeProofreader,
    FakeWriter,
    PagesWorkspace,
    article,
    group_ids_in,
    main_ids_in,
    page_id_in,
    points_of,
)
from tests.review_fixtures import FakeReviewer, ReviewWorkspace, corrector, sentence_a, valid_answers

PAGES = ["agencies", "costs", "clinics", "hospital"]
LEAD = "פתיח קצר על הדף."
NEW = "משפט חדש ונוסף."


def edit(old: str, new: str) -> dict[str, str]:
    return {"old": old, "new": new, "kind": "grammar", "why": "reads better"}


LEAD_EDIT = edit(LEAD, "פתיחה קצרה לדף.")
NEW_EDIT = edit("ונוסף", "נוסף")


def lead_edit(page_id: str, body: str) -> list[dict[str, str]]:
    return [LEAD_EDIT]


def _count(system: str, user: str) -> int:
    return (len(system) + len(user)) // 4


def _bodies(ws: PagesWorkspace, edited: bool = False) -> dict[str, str]:
    return {pid: page["body_md"] for pid, page in ws.pages(edited)["pages"].items()}


def test_written_pages_are_proofread_in_one_batch_and_a_rerun_makes_no_call(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = PagesWorkspace(tmp_path / "on")
    proofreader = FakeProofreader(lead_edit)
    result = ws.run(proofread_llm=proofreader)
    assert proofreader.batches == [PAGES] and proofreader.calls == []
    assert result["proofread"] == 4 and result["language_edits"] == 4
    payload = ws.pages()
    for page in payload["pages"].values():
        assert page["body_md"].startswith("פתיחה קצרה לדף.\n")
        assert page["language_edits"] == {"model": "fake-proofread", "applied": [LEAD_EDIT],
                                          "dropped": {}}
    assert payload["metadata"]["models"]["proofread"] == "fake-proofread"
    again = FakeProofreader(lead_edit)
    assert ws.run(proofread_llm=again)["written"] is False and again.pages() == []

    monkeypatch.setitem(PROOFREAD_CONFIG, "enabled", False)
    off, never = PagesWorkspace(tmp_path / "off"), FakeProofreader(lead_edit)
    off.run(proofread_llm=never)
    assert never.pages() == []
    pages_off = off.pages()["pages"]
    assert all(p["body_md"].startswith(LEAD) and "language_edits" not in p for p in pages_off.values())
    assert {pid: p["input_fingerprint"] for pid, p in pages_off.items()} == {
        pid: p["input_fingerprint"] for pid, p in payload["pages"].items()}
    assert off.run(estimate=True, count_tokens=_count)["proofread"] == []


def test_a_failed_call_keeps_the_body_and_the_next_run_sends_that_page_alone(tmp_path: Path) -> None:
    ws = PagesWorkspace(tmp_path)
    estimate = ws.run(estimate=True, count_tokens=_count)
    assert estimate["proofread"] == [{"page": pid, "chars": prompt_chars(0)} for pid in PAGES]
    result = ws.run(proofread_llm=FakeProofreader(lead_edit, fail={"costs"}))
    assert list(result["proofread_failed"]) == ["costs"]
    costs = ws.pages()["pages"]["costs"]
    assert costs["body_md"].startswith(LEAD)
    assert costs["language_edits"]["failed"] and costs["language_edits"]["frozen"] == []
    assert ws.run(estimate=True, count_tokens=_count)["proofread"] == [
        {"page": "costs", "chars": prompt_chars(len(costs["body_md"]))}]
    second = FakeProofreader(lead_edit)
    ws.run(proofread_llm=second)
    assert second.batches == [["costs"]]
    costs = ws.pages()["pages"]["costs"]
    assert costs["body_md"].startswith("פתיחה קצרה לדף.") and costs["language_edits"]["applied"]
    third = FakeProofreader(lead_edit)
    assert ws.run(proofread_llm=third)["written"] is False and third.pages() == []


def test_a_page_the_owner_edited_after_a_failed_call_is_not_sent(tmp_path: Path) -> None:
    ws = PagesWorkspace(tmp_path)
    ws.run(proofread_llm=FakeProofreader(lead_edit, fail={"costs"}))
    payload = ws.pages()
    payload["pages"]["costs"]["body_md"] += "תוספת של הבעלים.\n"
    ws.write_edited(payload)
    proofreader = FakeProofreader(lead_edit)
    ws.run(proofread_llm=proofreader)
    assert proofreader.pages() == []
    assert ws.pages(edited=True)["pages"]["costs"]["body_md"].endswith("תוספת של הבעלים.\n")


def _updated(system: Any, user: Any, task: str) -> dict[str, Any]:
    """An update answer: the page as before plus one new sentence."""
    page_id = page_id_in(user)
    return {**points_of(main_ids_in(user)), "no_change": False,
            "body": article(group_ids_in(user), page_id, NEW), "summary": f"תקציר {page_id}",
            "questions": ["שאלה?"], "related": [{"page_id": "clinics", "relation": "see_also"}]}


def test_an_update_is_proofread_only_where_it_wrote_new_text(tmp_path: Path) -> None:
    ws = PagesWorkspace(tmp_path)
    ws.run()
    groups = ws.groups()
    groups[0]["supporter_count"] += 1  # agencies changes: an update
    ws.write_groups(groups)
    proofreader = FakeProofreader(lambda page_id, body: [LEAD_EDIT, NEW_EDIT])
    result = ws.run(update_llm=FakeWriter("fake-update", respond=_updated),
                    proofread_llm=proofreader)
    assert result["update"] == 1 and proofreader.batches == [["agencies"]]
    page = ws.pages()["pages"]["agencies"]
    assert page["body_md"].startswith(LEAD) and page["body_md"].endswith("משפט חדש נוסף.\n")
    assert page["language_edits"]["applied"] == [NEW_EDIT]
    assert page["language_edits"]["dropped"] == {"frozen": 1}


def test_an_update_answered_with_no_change_is_not_sent(tmp_path: Path) -> None:
    ws = PagesWorkspace(tmp_path)
    ws.run(proofread_llm=FakeProofreader(lead_edit))
    before = ws.pages()["pages"]["agencies"]
    groups = ws.groups()
    groups[0]["supporter_count"] += 1
    ws.write_groups(groups)

    def unchanged(system: Any, user: Any, task: str) -> dict[str, Any]:
        return {**points_of(main_ids_in(user)), "no_change": True, "body": "", "summary": "",
                "questions": [], "related": []}

    proofreader = FakeProofreader(lead_edit)
    ws.run(update_llm=FakeWriter("fake-update", respond=unchanged), proofread_llm=proofreader)
    page = ws.pages()["pages"]["agencies"]
    assert proofreader.pages() == []
    assert page["body_md"] == before["body_md"] and page["language_edits"] == before["language_edits"]


@pytest.fixture()
def review(tmp_path: Path) -> ReviewWorkspace:
    workspace = ReviewWorkspace(tmp_path)
    workspace.review(FakeReviewer(valid_answers()))
    return workspace


def _suggestion(ws: ReviewWorkspace, kind: str) -> dict[str, Any]:
    return next(s for s in ws.suggestions_payload()["suggestions"] if s["kind"] == kind)


FIXED = "הקבוצה g-00000004 מספרת על הניסיון השני.[^g-00000004]"


def test_an_applied_correction_is_proofread_where_it_changed_the_page(review: ReviewWorkspace) -> None:
    lead = "פתיח קצר על הדף agencies"
    proofreader = FakeProofreader(lambda page_id, body: [
        edit(lead, "פתיחה לדף agencies"), edit("על הניסיון השני", "על ניסיונה השני")])
    applier = review.applier(FakeWriter("fake-apply", respond=corrector(sentence_a("g-00000004"),
                                                                         FIXED)),
                             proofread_llm=proofreader)
    applier.apply(_suggestion(review, "mistake"))
    assert proofreader.batches == [["agencies"]]
    page = review.effective_page("agencies")
    assert "מספרת על ניסיונה השני.[^g-00000004]" in page["body_md"] and lead in page["body_md"]
    assert page["language_edits"]["applied"] == [edit("על הניסיון השני", "על ניסיונה השני")]
    assert page["language_edits"]["dropped"] == {"frozen": 1}
    assert list(page)[-2:] == ["language_edits", "input_fingerprint"]
    assert review.pages()["pages"]["agencies"]["language_edits"]["applied"] == []  # step 7's own


def test_a_contradiction_proofreads_its_pages_in_one_batch_and_a_failure_keeps_the_text(
        review: ReviewWorkspace) -> None:
    proofreader = FakeProofreader(fail={"clinics"})
    applier = review.applier(FakeWriter("fake-apply", respond=corrector("פתיח קצר", "שני צדדים")),
                             proofread_llm=proofreader)
    applier.apply(_suggestion(review, "contradiction"))
    assert proofreader.batches == [["agencies", "clinics"]]
    clinics = review.effective_page("clinics")
    assert "שני צדדים" in clinics["body_md"] and clinics["language_edits"]["failed"]
    assert review.effective_page("agencies")["language_edits"]["applied"] == []


def test_the_apply_makes_no_proofreading_call_when_it_is_off(
        review: ReviewWorkspace, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(PROOFREAD_CONFIG, "enabled", False)
    proofreader = FakeProofreader(lead_edit)
    review.applier(FakeWriter("fake-apply", respond=corrector(sentence_a("g-00000004"), FIXED)),
                   proofread_llm=proofreader).apply(_suggestion(review, "mistake"))
    assert proofreader.pages() == []
    assert "language_edits" not in review.effective_page("agencies")
