"""Step 8b, proofreading after the review, with fake models on the synthetic wiki: one batch of
the pages holding text never proofread, the record of what was proofread, the edited file,
what an update, an apply or the owner adds, a failed call, and why nothing else becomes due."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from review_tool.steps import SERVED_STEPS
from step_7_write_pages.checks import sentences
from step_7_write_pages.proofread import PROOFREAD_CONFIG, prompt_chars
from step_8_review.proofread import MOVED, run
from step_10_update.estimate import Estimator
from tests.pages_fixtures import FakeProofreader, FakeWriter, main_ids_in, points_of
from tests.site_fixtures import make_page, make_site_plan, run_site
from tests.review_fixtures import (
    FakeReviewer,
    ReviewWorkspace,
    corrector,
    count_tokens,
    current_body,
    sentence_a,
    valid_answers,
)
from utils.json_io import write_json_file
from utils.paths import STEP_8_PROOFREAD, resolve
from utils.reconcile import fingerprint

PAGES = ["agencies", "costs", "clinics", "hospital"]
NEW = "משפט חדש ונוסף."
NEW_EDIT = {"old": "ונוסף", "new": "נוסף", "kind": "grammar", "why": "reads better"}


def edit(old: str, new: str) -> dict[str, str]:
    return {"old": old, "new": new, "kind": "grammar", "why": "reads better"}


def lead(page_id: str) -> str:
    return f"פתיח קצר על הדף {page_id}"


def proofread_lead(page_id: str) -> str:
    return f"פתיחה קצרה לדף {page_id}"


def lead_edit(page_id: str, body: str) -> list[dict[str, str]]:
    return [edit(lead(page_id), proofread_lead(page_id))]


def both_edits(page_id: str, body: str) -> list[dict[str, str]]:
    """An edit of the (already proofread) lead and one of the new sentence."""
    return [edit(proofread_lead(page_id), f"פתיחה לדף {page_id}"), NEW_EDIT]


@pytest.fixture()
def ws(tmp_path: Path) -> ReviewWorkspace:
    return ReviewWorkspace(tmp_path)


def record_path(ws: ReviewWorkspace) -> Path:
    return ws.groups_path.parent / "proofread.json"


def proofread(ws: ReviewWorkspace, llm: FakeProofreader | None = None, **kwargs: Any
              ) -> dict[str, Any]:
    return run(pages_artifact=ws.artifact, record_path=record_path(ws),
               aggregated_path=ws.groups_path, plan_path=resolve(ws.plan_artifact),
               assignments_path=ws.assignments_path, llm=llm or FakeProofreader(), **kwargs)


def record(ws: ReviewWorkspace) -> dict[str, Any]:
    return json.loads(record_path(ws).read_text(encoding="utf-8"))


def adds(sentence: str) -> Any:
    """A fake ``update`` model that keeps the page it is given and adds ``sentence``."""

    def respond(system: Any, user: Any, task: str) -> dict[str, Any]:
        return {**points_of(main_ids_in(user)), "no_change": False,
                "body": f"{current_body(user)}\n{sentence}", "summary": "תקציר",
                "questions": ["שאלה?"], "related": []}

    return respond


def test_every_page_never_proofread_goes_in_one_batch_into_the_edited_file(
        ws: ReviewWorkspace) -> None:
    original = ws.pages()
    estimate = proofread(ws, estimate=True)
    assert estimate["calls"] == 4 and not record_path(ws).exists()
    assert estimate["estimate"] == [
        {"page": pid, "chars": prompt_chars(len(original["pages"][pid]["body_md"])),
         "new_sentences": len(sentences(original["pages"][pid]["body_md"]))} for pid in PAGES]
    fake = FakeProofreader(lead_edit)
    result = proofread(ws, fake)
    assert fake.batches == [PAGES] and fake.calls == []
    assert (result["proofread"], result["language_edits"], result["left"]) == (4, 4, [])
    assert ws.pages() == original  # step 7's own file is not touched
    edited = ws.pages(edited=True)
    for page_id, page in edited["pages"].items():
        assert page["body_md"].startswith(f"{proofread_lead(page_id)}.[^")
        assert list(page)[-2:] == ["language_edits", "input_fingerprint"]
        record_ = page["language_edits"]
        assert record_["applied"] == lead_edit(page_id, "") and record_["model"] == "fake-proofread"
        assert record_["sentences"] == sorted({fingerprint(s) for s in sentences(page["body_md"])})
    assert record(ws)["metadata"]["pages_due"] == 4 and record(ws)["left"] == []
    assert record(ws)["pages"]["costs"]["applied"] == 1
    again = FakeProofreader(lead_edit)
    assert proofread(ws, again)["written"] is False and again.pages() == []
    assert proofread(ws, estimate=True)["calls"] == 0


def test_only_and_sync_and_an_unknown_page(ws: ReviewWorkspace) -> None:
    fake = FakeProofreader(lead_edit)
    proofread(ws, fake, only=["costs"], use_batch=False)
    assert fake.calls == ["costs"] and fake.batches == []
    assert record(ws)["left"] == ["agencies", "clinics", "hospital"]
    with pytest.raises(ValueError, match="nope"):
        proofread(ws, only=["nope"])


def test_proofreading_off_does_nothing(ws: ReviewWorkspace, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(PROOFREAD_CONFIG, "enabled", False)
    fake = FakeProofreader(lead_edit)
    assert proofread(ws, fake)["written"] is False and fake.pages() == []
    assert not ws.artifact.edited.exists() and not record_path(ws).exists()


def test_a_failed_call_loses_only_its_page_and_the_next_pass_sends_it_again(
        ws: ReviewWorkspace) -> None:
    result = proofread(ws, FakeProofreader(lead_edit, fail={"costs"}))
    assert result["failed"] == ["costs"] and result["left"] == ["costs"]
    costs = ws.pages(edited=True)["pages"]["costs"]
    assert costs["body_md"].startswith(lead("costs")) and "language_edits" not in costs
    assert record(ws)["pages"]["costs"]["failed"]
    second = FakeProofreader(lead_edit)
    proofread(ws, second)
    assert second.batches == [["costs"]]
    assert ws.pages(edited=True)["pages"]["costs"]["body_md"].startswith(proofread_lead("costs"))


def test_a_page_changed_while_the_editor_read_it_is_left_for_the_next_pass(
        ws: ReviewWorkspace) -> None:
    def owner_edits_meanwhile(page_id: str, body: str) -> list[dict[str, str]]:
        if page_id == "agencies":
            payload = ws.pages()
            payload["pages"]["agencies"]["body_md"] += "תוספת של הבעלים.\n"
            ws.write_edited(payload)
        return lead_edit(page_id, body)

    result = proofread(ws, FakeProofreader(owner_edits_meanwhile))
    assert result["failed"] == ["agencies"] and record(ws)["pages"]["agencies"]["failed"] == MOVED
    agencies = ws.pages(edited=True)["pages"]["agencies"]
    assert agencies["body_md"].startswith(lead("agencies"))
    assert agencies["body_md"].endswith("תוספת של הבעלים.\n")


def test_a_sentence_the_owner_typed_is_proofread_alone_on_the_next_pass(
        ws: ReviewWorkspace) -> None:
    proofread(ws, FakeProofreader(lead_edit))
    payload = ws.pages(edited=True)
    payload["pages"]["clinics"]["body_md"] += f"{NEW}\n"
    ws.write_edited(payload)
    fake = FakeProofreader(both_edits)
    proofread(ws, fake)
    assert fake.batches == [["clinics"]]
    clinics = ws.pages(edited=True)["pages"]["clinics"]
    assert clinics["body_md"].startswith(proofread_lead("clinics"))  # frozen
    assert clinics["body_md"].endswith("משפט חדש נוסף.\n")
    assert clinics["language_edits"]["applied"] == [NEW_EDIT]
    assert clinics["language_edits"]["dropped"] == {"frozen": 1}


def test_step_7_keeps_the_proofread_text_and_an_update_sends_only_its_new_sentence(
        ws: ReviewWorkspace) -> None:
    proofread(ws, FakeProofreader(lead_edit))
    writer = FakeWriter()
    assert ws.run(llm=writer)["written"] is False and writer.calls == []
    assert ws.run(estimate=True, count_tokens=count_tokens)["estimate"] == []
    assert ws.pages(edited=True)["pages"]["costs"]["body_md"].startswith(proofread_lead("costs"))
    groups = ws.groups()
    groups[0]["supporter_count"] += 1  # agencies changes: an update
    ws.write_groups(groups)
    assert ws.run(update_llm=FakeWriter("fake-update", respond=adds(NEW)))["update"] == 1
    agencies = ws.pages()["pages"]["agencies"]
    assert agencies["body_md"].startswith(proofread_lead("agencies"))  # from the effective text
    assert agencies["language_edits"]["applied"] == lead_edit("agencies", "")  # carried over
    fake = FakeProofreader(both_edits)
    proofread(ws, fake)
    assert fake.batches == [["agencies"]]
    page = ws.pages(edited=True)["pages"]["agencies"]
    assert page["body_md"].startswith(proofread_lead("agencies"))
    assert page["body_md"].endswith("משפט חדש נוסף.\n")
    assert page["language_edits"]["applied"] == [NEW_EDIT]


def test_the_review_does_not_become_due_and_marks_nothing(ws: ReviewWorkspace) -> None:
    ws.review(FakeReviewer(valid_answers()))
    assert ws.review(FakeReviewer([]), estimate=True)["calls"] == 0
    proofread(ws, FakeProofreader(lead_edit))
    assert ws.review(FakeReviewer([]), estimate=True)["calls"] == 0
    reviewer = FakeReviewer([])
    ws.review(reviewer)
    assert reviewer.passes() == []
    assert all(s["page_marks"] == {} for s in ws.suggestions_payload()["suggestions"])
    payload = ws.pages(edited=True)
    payload["pages"]["costs"]["body_md"] += "תוספת של הבעלים.\n"  # a real change still counts
    ws.write_edited(payload)
    assert ws.review(FakeReviewer([]), estimate=True)["calls"] == 1


FIXED = "הקבוצה g-00000004 מספרת על הניסיון השני.[^g-00000004]"


def test_an_apply_after_proofreading_works_on_the_proofread_text(ws: ReviewWorkspace) -> None:
    ws.review(FakeReviewer(valid_answers()))
    proofread(ws, FakeProofreader(lead_edit))
    mistake = next(s for s in ws.suggestions_payload()["suggestions"] if s["kind"] == "mistake")
    ws.applier(FakeWriter("fake-apply", respond=corrector(sentence_a("g-00000004"), FIXED))
               ).apply(mistake)
    page = ws.effective_page("agencies")
    assert page["body_md"].startswith(proofread_lead("agencies")) and FIXED in page["body_md"]
    assert page["language_edits"]["applied"] == lead_edit("agencies", "")  # kept
    fake = FakeProofreader(lambda page_id, body: [
        edit(proofread_lead(page_id), "פתיחה"), edit("על הניסיון השני", "על ניסיונה השני")])
    proofread(ws, fake)
    assert fake.batches == [["agencies"]]
    page = ws.effective_page("agencies")
    assert "מספרת על ניסיונה השני.[^g-00000004]" in page["body_md"]
    assert page["language_edits"]["dropped"] == {"frozen": 1}


def test_the_estimate_prices_proofreading_as_its_own_step(ws: ReviewWorkspace) -> None:
    estimator = Estimator(lambda step: lambda **kwargs: proofread(ws, **kwargs))
    first = estimator(SERVED_STEPS.step(STEP_8_PROOFREAD))
    assert first.paid_calls == 4 and first.usd > 0 and "4 pages" in first.what
    proofread(ws, FakeProofreader(lead_edit))
    after = estimator(SERVED_STEPS.step(STEP_8_PROOFREAD))
    assert after.paid_calls == 0 and after.usd == 0 and after.what.startswith("no call")
    write_json_file(ws.pages(), ws.artifact.edited)  # an apply-like rewrite of every page
    assert estimator(SERVED_STEPS.step(STEP_8_PROOFREAD)).paid_calls == 4


def test_the_site_names_the_pages_holding_text_never_proofread(
        tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    plan = make_site_plan()
    body = "פתיח.\n\nמשפט אחד.[^g-00000001]\n"
    done = make_page(plan, "costs", body)
    done["language_edits"] = {"model": "m", "applied": [], "dropped": {},
                              "sentences": sorted(fingerprint(s) for s in sentences(body))}
    pages = {"agencies": make_page(plan, "agencies", body), "costs": done}
    with caplog.at_level("WARNING"):
        result = run_site(tmp_path, plan, pages)
    assert result["unproofread"] == ["agencies"] and result["published"] == 2
    assert any("never proofread" in r.message and "agencies" in r.message for r in caplog.records)
    monkeypatch.setitem(PROOFREAD_CONFIG, "enabled", False)
    assert run_site(tmp_path, plan, pages)["unproofread"] == []
