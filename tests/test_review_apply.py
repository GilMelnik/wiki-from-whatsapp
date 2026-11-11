"""Applying a suggestion: each kind changes its artifact (plan, pages, groups) the way the
existing steps expect, and a refused apply leaves every file byte-identical."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from step_6_plan.model import validation_errors
from step_8_review.apply import ApplyRefused, kept_share, sentences
from step_8_review.inputs import REVIEW_CONFIG
from tests.pages_fixtures import FakeWriter, Responder
from tests.review_fixtures import (
    FakeReviewer,
    ReviewWorkspace,
    corrector,
    flat,
    no_change,
    sentence_a,
    valid_answers,
)

FIXED = "הקבוצה g-00000004 מספרת על הניסיון השני.[^g-00000004]"


@pytest.fixture()
def ws(tmp_path: Path) -> ReviewWorkspace:
    workspace = ReviewWorkspace(tmp_path)
    workspace.review(FakeReviewer(valid_answers()))
    return workspace


def _suggestion(ws: ReviewWorkspace, kind: str) -> dict:
    return next(s for s in ws.suggestions_payload()["suggestions"] if s["kind"] == kind)


@pytest.mark.parametrize("kind,check", [
    ("split", lambda plan: "clinic-prices" in plan["pages"]
     and plan["pages"]["clinics"]["scope"] == "תחום מצומצם"),
    ("merge", lambda plan: "costs" not in plan["pages"]
     and plan["pages"]["agencies"]["merged_from"] == ["costs"]),
    ("add_page", lambda plan: plan["pages"]["travel"]["path"] == ["before", "choosing"]),
    ("add_label", lambda plan: plan["labels"]["texas"]["kind"] == "country"),
])
def test_a_structural_suggestion_is_saved_as_a_valid_plan_edit(
        ws: ReviewWorkspace, kind: str, check: Callable[[dict[str, Any]], bool]) -> None:
    pages_before = ws.artifact.original.read_bytes()
    result = ws.applier().apply(_suggestion(ws, kind))
    plan = json.loads(ws.plan_artifact.edited.read_text(encoding="utf-8"))
    assert validation_errors(plan) == [] and check(plan)
    assert plan["metadata"]["edited_by"] == REVIEW_CONFIG["edited_by"]
    assert ws.plan_path.read_text(encoding="utf-8") == json.dumps(ws.plan(), ensure_ascii=False,
                                                                    indent=2)
    assert not ws.artifact.edited.exists() and ws.artifact.original.read_bytes() == pages_before
    assert result.message == REVIEW_CONFIG["messages"]["plan_applied"]


def test_the_owners_edited_proposal_is_what_gets_applied(ws: ReviewWorkspace) -> None:
    suggestion = _suggestion(ws, "add_label")
    suggestion["edited_proposal"] = {**suggestion["proposal"], "label_id": "lone-star"}
    ws.applier().apply(suggestion)
    labels = json.loads(ws.plan_artifact.edited.read_text(encoding="utf-8"))["labels"]
    assert "lone-star" in labels and "texas" not in labels


def test_a_refused_structural_apply_changes_nothing(ws: ReviewWorkspace) -> None:
    suggestion = _suggestion(ws, "add_page")
    suggestion["edited_proposal"] = {**suggestion["proposal"], "page_id": "clinics"}
    before = ws.snapshot()
    with pytest.raises(ApplyRefused) as refused:
        ws.applier().apply(suggestion)
    assert "is used 2 times" in str(refused.value) and refused.value.details
    assert ws.snapshot() == before


def test_a_mistake_is_corrected_through_step_7_and_saved_to_the_edited_pages(ws: ReviewWorkspace) -> None:
    llm = FakeWriter("fake-apply", respond=corrector(sentence_a("g-00000004"), FIXED))
    before = ws.effective_page("agencies")["body_md"]
    result = ws.applier(llm).apply(_suggestion(ws, "mistake"))
    after = ws.effective_page("agencies")
    assert FIXED in after["body_md"] and sentence_a("g-00000004") not in after["body_md"]
    assert after["body_md"].count("[^g-00000004]:") == 1
    untouched = [s for s in sentences(before) if "g-00000004" not in s]
    assert all(s in after["body_md"] for s in untouched)
    assert result.details["pages"]["agencies"]["kept_sentence_share"] == 1.0
    assert after["front_matter"]["summary"] == "תקציר מתוקן"
    prompt = llm.calls[0]["user"]
    assert "editor request" in "".join(segment.text for segment in prompt)
    assert ws.pages()["pages"]["agencies"]["body_md"] == before


def test_step_7_keeps_the_correction_as_the_pages_text(ws: ReviewWorkspace) -> None:
    llm = FakeWriter("fake-apply", respond=corrector(sentence_a("g-00000004"), FIXED))
    ws.applier(llm).apply(_suggestion(ws, "mistake"))
    writer = FakeWriter()
    ws.run(llm=writer, update_llm=FakeWriter("fake-update"))
    assert writer.calls == []
    assert FIXED in ws.pages(edited=True)["pages"]["agencies"]["body_md"]


@pytest.mark.parametrize("respond,expected", [
    (no_change, "no_change"),
    (corrector("[^g-00000008]", ""), "too_much_changed"),
    (corrector("[^g-00000000]", "[^g-00000003]"), "failed_checks"),
])
def test_a_refused_page_correction_changes_nothing(ws: ReviewWorkspace, respond: Responder,
                                                   expected: str) -> None:
    before = ws.snapshot()
    with pytest.raises(ApplyRefused) as refused:
        ws.applier(FakeWriter("fake-apply", respond=respond)).apply(_suggestion(ws, "mistake"))
    lead = REVIEW_CONFIG["messages"][expected].split("{")[0]
    assert str(refused.value).startswith(lead)
    assert ws.snapshot() == before


def test_a_mistake_whose_sentence_is_gone_is_refused(ws: ReviewWorkspace) -> None:
    payload = ws.pages()
    payload["pages"]["agencies"]["body_md"] = payload["pages"]["agencies"]["body_md"].replace(
        sentence_a("g-00000004"), "נוסח חדש של הבעלים.[^g-00000004]")
    ws.write_edited(payload)
    llm = FakeWriter("fake-apply")
    with pytest.raises(ApplyRefused, match="not in page agencies"):
        ws.applier(llm).apply(_suggestion(ws, "mistake"))
    assert llm.calls == []


def test_a_contradiction_links_the_groups_and_corrects_both_pages(ws: ReviewWorkspace) -> None:
    def both_sides(system: Any, user: Any, task: str) -> dict[str, Any]:
        answer = corrector("פתיח קצר", "פתיח קצר, ויש בו שני צדדים")(system, user, task)
        assert "contradicts: g-0000000" in "".join(s.text for s in user)
        return answer

    llm = FakeWriter("fake-apply", respond=both_sides)
    result = ws.applier(llm).apply(_suggestion(ws, "contradiction"))
    assert ws.linker.links == [("g-00000000", "g-00000002", True)]
    for page_id in ("agencies", "clinics"):
        assert "שני צדדים" in ws.effective_page(page_id)["body_md"]
    assert set(result.details["pages"]) == {"agencies", "clinics"}
    assert result.details["links"] == [["g-00000000", "g-00000002"]]


def test_a_contradiction_whose_page_correction_fails_links_nothing(ws: ReviewWorkspace) -> None:
    before = ws.snapshot()
    with pytest.raises(ApplyRefused):
        ws.applier(FakeWriter("fake-apply", respond=corrector("[^g-00000000]", "[^g-00000003]"))
                   ).apply(_suggestion(ws, "contradiction"))
    assert ws.linker.links == [] and ws.snapshot() == before


def test_a_contradiction_already_shown_on_the_pages_only_links_the_groups(ws: ReviewWorkspace) -> None:
    pages = ws.artifact.original.read_bytes()
    result = ws.applier(FakeWriter("fake-apply", respond=no_change)).apply(
        _suggestion(ws, "contradiction"))
    assert ws.linker.links == [("g-00000000", "g-00000002", True)]
    assert not ws.artifact.edited.exists() and ws.artifact.original.read_bytes() == pages
    assert result.details["pages"]["agencies"]["changed"] is False


def test_kept_share_ignores_the_touched_sentences() -> None:
    before = ("אחת.[^g-1] שתיים.[^g-2] שלוש 2.5 ק\"ג.[^g-3]\n\n## כותרת\n\n"
              "המשפט הרביעי והארוך.")
    after = before.replace("שתיים.", "שתיים אחרת.").replace("הרביעי", "החמישי")
    assert sentences(before)[2] == 'שלוש 2.5 ק"ג.[^g-3]'
    assert kept_share(before, after, ["g-2"]) == 0.75
    assert kept_share(before, after, ["g-2"], quoted="המשפט הרביעי והארוך") == 1.0


def test_flat_answers_cover_every_kind() -> None:
    assert {a["kind"] for a in valid_answers()} == {"mistake", "contradiction", "split", "merge",
                                                   "add_page", "add_label"}
    assert flat("merge")["merged_ids"] == []
