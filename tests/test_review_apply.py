"""Applying a suggestion: each kind changes its artifact (plan, pages, the owner's
contradiction pairs) the way the existing steps expect, and a refused apply leaves every file
byte-identical."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

import review_tool.tools.suggestions.apply as apply_module
from review_tool.tools.suggestions.apply import ApplyRefused, kept_share, sentences
from step_5_same_complaint.claim_records import (
    add_manual_contradiction,
    load_claim_records,
    load_manual_pairs,
)
from step_6_plan.model import validation_errors
from step_9_review.inputs import REVIEW_CONFIG
from tests.pages_fixtures import IDS, FakeWriter, Responder
from tests.review_fixtures import (
    FakeReviewer,
    ReviewWorkspace,
    corrector,
    flat,
    no_change,
    sentence_a,
    valid_answers,
)

FIXED = f"הטענה {IDS[4]} מספרת על הניסיון השני.[^{IDS[4]}]"
LINK = tuple(sorted((IDS[0], IDS[2])))  # the pair the contradiction suggestion links


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
    llm = FakeWriter("fake-apply", respond=corrector(sentence_a(IDS[4]), FIXED))
    before = ws.effective_page("agencies")["body_md"]
    result = ws.applier(llm).apply(_suggestion(ws, "mistake"))
    after = ws.effective_page("agencies")
    assert FIXED in after["body_md"] and sentence_a(IDS[4]) not in after["body_md"]
    assert f"[^{IDS[4]}]" in after["body_md"] and "]:" not in after["body_md"]  # marks only
    untouched = [s for s in sentences(before) if IDS[4] not in s]
    assert all(s in after["body_md"] for s in untouched)
    assert result.details["pages"]["agencies"]["kept_sentence_share"] == 1.0
    assert after["front_matter"]["summary"] == "תקציר מתוקן"
    assert [p["claims"] for p in after["points"]] == [[IDS[0]], [IDS[4]], [IDS[8]]]
    assert after["left_out"] == []
    prompt = "".join(segment.text for segment in llm.calls[0]["user"])
    assert "editor request" in prompt and f"<current_points>\n- fact | נקודה {IDS[0]}" in prompt
    assert f"(claims {IDS[4]})" in prompt
    assert ws.pages()["pages"]["agencies"]["body_md"] == before


def test_step_7_keeps_the_correction_as_the_pages_text(ws: ReviewWorkspace) -> None:
    llm = FakeWriter("fake-apply", respond=corrector(sentence_a(IDS[4]), FIXED))
    ws.applier(llm).apply(_suggestion(ws, "mistake"))
    writer = FakeWriter()
    ws.run(llm=writer, update_llm=FakeWriter("fake-update"))
    assert writer.calls == []
    assert FIXED in ws.pages(edited=True)["pages"]["agencies"]["body_md"]


@pytest.mark.parametrize("respond,expected", [
    (no_change, "no_change"),
    (corrector(f"[^{IDS[8]}]", ""), "too_much_changed"),
    (corrector(f"[^{IDS[0]}]", f"[^{IDS[3]}]"), "failed_checks"),
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
        sentence_a(IDS[4]), f"נוסח חדש של הבעלים.[^{IDS[4]}]")
    ws.write_edited(payload)
    llm = FakeWriter("fake-apply")
    with pytest.raises(ApplyRefused, match="not in page agencies"):
        ws.applier(llm).apply(_suggestion(ws, "mistake"))
    assert llm.calls == []


def test_a_contradiction_links_the_claims_and_corrects_both_pages(ws: ReviewWorkspace) -> None:
    def both_sides(system: Any, user: Any, task: str) -> dict[str, Any]:
        answer = corrector("פתיח קצר", "פתיח קצר, ויש בו שני צדדים")(system, user, task)
        prompt = "".join(s.text for s in user)
        assert f"contradicts: {IDS[2]}" in prompt or f"contradicts: {IDS[0]}" in prompt
        assert f"Claims {IDS[0]} and {IDS[2]} contradict each other" in prompt
        return answer

    llm = FakeWriter("fake-apply", respond=both_sides)
    result = ws.applier(llm).apply(_suggestion(ws, "contradiction"))
    assert load_manual_pairs(ws.sources.contradictions) == [LINK]
    claims = {claim["short_id"]: claim for claim in load_claim_records(ws.sources)}
    assert IDS[2] in claims[IDS[0]]["contradicts"] and IDS[0] in claims[IDS[2]]["contradicts"]
    for page_id in ("agencies", "clinics"):
        assert "שני צדדים" in ws.effective_page(page_id)["body_md"]
    assert set(result.details["pages"]) == {"agencies", "clinics"}
    assert result.details["links"] == [[IDS[0], IDS[2]]]
    assert result.details["links_file"] == str(ws.sources.contradictions)
    assert result.details["links_backup"] is None  # there were no manual pairs before


def test_a_contradiction_whose_page_correction_fails_links_nothing(ws: ReviewWorkspace) -> None:
    before = ws.snapshot()
    with pytest.raises(ApplyRefused):
        ws.applier(FakeWriter("fake-apply", respond=corrector(f"[^{IDS[0]}]", f"[^{IDS[3]}]"))
                   ).apply(_suggestion(ws, "contradiction"))
    assert not ws.sources.contradictions.exists() and ws.snapshot() == before


def test_a_contradiction_whose_link_cannot_be_saved_changes_nothing(
        ws: ReviewWorkspace, monkeypatch: pytest.MonkeyPatch) -> None:
    add_manual_contradiction(IDS[5], IDS[9], ws.sources.contradictions)  # an earlier pair

    def disk_full(a: str, b: str, path: Path) -> bool:
        add_manual_contradiction(a, b, path)
        raise OSError("disk full")

    monkeypatch.setattr(apply_module, "add_manual_contradiction", disk_full)
    before = ws.snapshot()
    llm = FakeWriter("fake-apply", respond=corrector("פתיח קצר", "פתיח קצר, ויש בו שני צדדים"))
    with pytest.raises(ApplyRefused) as refused:
        ws.applier(llm).apply(_suggestion(ws, "contradiction"))
    assert str(refused.value).startswith(REVIEW_CONFIG["messages"]["link_refused"].split("{")[0])
    assert ws.snapshot() == before  # the earlier pairs are back, no page was written


def test_a_contradiction_already_shown_on_the_pages_only_links_the_claims(ws: ReviewWorkspace) -> None:
    pages = ws.artifact.original.read_bytes()
    result = ws.applier(FakeWriter("fake-apply", respond=no_change)).apply(
        _suggestion(ws, "contradiction"))
    assert load_manual_pairs(ws.sources.contradictions) == [LINK]
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
