"""Step 8's code-side guarantees, prompts and front matter, offline on synthetic data."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from step_6_plan import operations
from step_8_write_pages.checks import AnswerRules, cited_ids, problems, repair, rules_for
from step_8_write_pages.front_matter import LabelRule, page_front_matter
from step_8_write_pages.inputs import (
    PAGES_CONFIG,
    Corpus,
    PageContext,
    input_fingerprint,
    page_state,
    writer_fingerprint,
)
from step_8_write_pages.prompts import update_prompt, write_prompt
from step_8_write_pages.writer import ClaimDelta, PageWriter
from tests.pages_fixtures import (
    IDS,
    SECRET,
    FakeWriter,
    PagesWorkspace,
    article,
    claim_ids_in,
    main_ids_in,
    points_of,
)
from utils.llm_client import _flatten
from utils.okf import validate_front_matter

LOGGER = logging.getLogger("test-pages")


@pytest.fixture()
def corpus(tmp_path: Path) -> Corpus:
    ws = PagesWorkspace(tmp_path)
    return Corpus.load(ws.sources, ws.plan_path, ws.assignments_path)


def _context(corpus: Corpus, page_id: str = "agencies") -> PageContext:
    return corpus.context(page_id, corpus.catalogue())


# ------------------------------------------------------------------ inputs and prompt


def test_primary_and_second_page_and_unplaced_claims(corpus: Corpus) -> None:
    agencies = {r["id"]: r for r in _context(corpus, "agencies").records}
    costs = {r["id"]: r for r in _context(corpus, "costs").records}
    assert agencies[IDS[4]]["role"] == "main"
    assert costs[IDS[4]]["role"] == "mention" and costs[IDS[4]]["primary"] == "agencies"
    _, user = write_prompt(_context(corpus, "costs"))
    assert f"{IDS[4]} | mention -> agencies |" in _flatten(user)
    every_prompt = "".join(_flatten(write_prompt(_context(corpus, pid))[1])
                           for pid in ("agencies", "costs", "clinics", "hospital"))
    assert IDS[11] not in every_prompt  # claim 11 is unplaced


def test_prompt_never_sees_identities_claim_or_thread_ids_or_reactions(corpus: Corpus) -> None:
    for page_id in ("agencies", "costs", "clinics", "hospital"):
        system, user = write_prompt(_context(corpus, page_id))
        text = system + _flatten(user)
        assert SECRET not in text  # claim, thread and person ids hold "SECRET"
        assert "t-SECRET" not in str(_context(corpus, page_id).records)
        assert "❤️" not in text and "reaction" not in text.lower()


def test_claim_line_says_support_recency_and_contradictions_in_words(corpus: Corpus) -> None:
    _, user = write_prompt(_context(corpus, "agencies"))
    text = _flatten(user)
    line = next(line for line in text.splitlines() if line.startswith(f"{IDS[0]} |"))
    assert "last backed by 3 in יוני 2024" in line and "by month:" in line
    _, user = write_prompt(_context(corpus, "hospital"))
    line = next(line for line in _flatten(user).splitlines() if line.startswith(f"{IDS[3]} |"))
    assert f'contradicts: {IDS[6]} (on clinics: "Synthetic claim number 6' in line
    assert "<as_of>2024-06-01 (יוני 2024)</as_of>" in text


def test_claims_without_assignment_are_skipped_and_counted(tmp_path: Path) -> None:
    ws = PagesWorkspace(tmp_path)
    payload = ws.assignments()
    del payload["assignments"][IDS[1]]
    ws.write_assignments(payload)
    corpus = Corpus.load(ws.sources, ws.plan_path, ws.assignments_path)
    assert corpus.skipped == [IDS[1]]
    assert IDS[1] not in _context(corpus, "costs").ids


def test_fingerprint_follows_text_inputs_not_the_catalogue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = PagesWorkspace(tmp_path)

    def fp() -> str:
        corpus = Corpus.load(ws.sources, ws.plan_path, ws.assignments_path)
        return input_fingerprint(page_state(_context(corpus), writer_fingerprint("m")))

    first = fp()
    assert fp() == first
    plan = ws.plan()
    plan = operations.rename(plan, "costs", "שם חדש").plan  # another page: catalogue only
    plan = operations.rename(plan, "usa", "תווית").plan
    ws.write_plan(plan)
    assert fp() == first
    claims = ws.claims()
    claims[0]["participant_count"] = 7  # rendered by step 11 only: no rewrite
    ws.write_claims(claims)
    assert fp() == first
    claims[0]["supporter_count"] += 1
    ws.write_claims(claims)
    assert fp() != first
    changed = fp()
    monkeypatch.setitem(PAGES_CONFIG, "prompt_version", 99)
    assert fp() != changed


# ------------------------------------------------------------------------ checks


def _rules(corpus: Corpus, page_id: str = "agencies") -> tuple[PageContext, AnswerRules]:
    context = _context(corpus, page_id)
    return context, rules_for(context.page_id, context.records, corpus.plan,
                              required=context.required_ids())


def test_problems_name_unknown_ids_links_and_uncited_claims(corpus: Corpus) -> None:
    context, rules = _rules(corpus, "hospital")
    body = f"טקסט [^{IDS[7]}][^g-deadbeef] ראו [[nowhere|שם]] ו[[hospital|כאן]] ו[[costs#x|עוגן]]."
    found = problems({"body": body, **points_of([IDS[7]])}, rules)
    assert any("g-deadbeef" in p for p in found)
    assert any("nowhere" in p and "hospital" in p for p in found)
    assert any("malformed" in p for p in found)
    required = context.required_ids()
    assert required == [IDS[3], IDS[7]]
    assert any("disputed or contradicted" in p and IDS[3] in p for p in found)
    assert any("in no point and not in left_out" in p and IDS[3] in p for p in found)
    full = article(sorted(context.ids), "hospital")
    assert problems({"body": full, **points_of(context.main_ids)}, rules) == []


def test_repair_removes_what_code_can_fix(corpus: Corpus) -> None:
    _, rules = _rules(corpus)
    body = (f"---\ntitle: x\n---\n# כותרת\n\nטקסט [^{IDS[0]}][^g-deadbeef] [[nowhere|שם]] "
            f"[[costs|עלויות]] [[costs#x|עוגן]] [[label:mars|מאדים]].\n\n[^{IDS[0]}]: הגדרה\n")
    fixed, repairs = repair({"body": body, **points_of([IDS[0]]),
                             "summary": f"תקציר [^{IDS[0]}] [[costs|עם קישור]]",
                             "questions": ["ש?", ""],
                             "related": [{"page_id": "costs", "relation": "See Also"},
                                         {"page_id": "agencies", "relation": "self"},
                                         {"page_id": "ghost", "relation": "x"}]}, rules)
    assert fixed["body"].startswith("## כותרת")
    assert "[^g-deadbeef]" not in fixed["body"] and f"[^{IDS[0]}]" in fixed["body"]
    assert "[[costs|עלויות]]" in fixed["body"] and "[[nowhere" not in fixed["body"]
    assert " שם " in fixed["body"] and "עוגן" in fixed["body"] and "[[costs#x" not in fixed["body"]
    assert "מאדים" in fixed["body"] and "label:mars" not in fixed["body"]
    assert "]:" not in fixed["body"]
    assert fixed["summary"] == "תקציר עם קישור"
    assert fixed["questions"] == ["ש?"]
    assert fixed["related"] == [{"slug": "costs", "relation": "see_also"}]
    assert len(repairs) >= 6


def test_the_written_body_keeps_its_marks_and_no_definitions(corpus: Corpus) -> None:
    context = _context(corpus)
    answer = {"body": f"א [^{IDS[4]}]. ב [^{IDS[0]}][^{IDS[4]}].\n\n[^{IDS[0]}]: ישן\n",
              "summary": "", "questions": [], "related": [], **points_of([IDS[0], IDS[4]])}
    text = PageWriter(FakeWriter(respond=lambda s, u, t: answer), FakeWriter(), corpus.plan,
                      LOGGER).write(context)
    assert "]:" not in text.body and cited_ids(text.body) == [IDS[4], IDS[0]]
    assert "removed footnote definitions written by the model" in text.report["repairs"]


def test_the_participant_count_never_reaches_the_prompt(tmp_path: Path) -> None:
    ws = PagesWorkspace(tmp_path)
    before = {pid: write_prompt(_context(Corpus.load(ws.sources, ws.plan_path, ws.assignments_path), pid))
              for pid in ("agencies", "hospital")}
    claims = ws.claims()
    for claim in claims:
        claim["participant_count"] = 987654
    ws.write_claims(claims)
    corpus = Corpus.load(ws.sources, ws.plan_path, ws.assignments_path)
    for page_id, prompt in before.items():
        assert write_prompt(_context(corpus, page_id)) == prompt
        assert "987654" not in str(_context(corpus, page_id).records)


# ------------------------------------------------------------------------ writer


def test_one_retry_names_the_problems_then_the_answer_is_repaired(corpus: Corpus) -> None:
    context = _context(corpus, "hospital")
    answers = iter([{"body": "טקסט [^g-deadbeef].", "summary": "", "questions": [], "related": [],
                     "points": [], "left_out": []},
                    {"body": f"עדיין [^g-deadbeef][^{IDS[7]}].", "summary": "ס",
                     "questions": ["ש"], "related": [], **points_of([IDS[7]])}])
    llm = FakeWriter(respond=lambda system, user, task: next(answers))
    text = PageWriter(llm, FakeWriter(), corpus.plan, LOGGER).write(context)
    assert len(llm.calls) == 2
    retry = _flatten(llm.calls[1]["user"])
    assert "<previous_answer>" in retry and "g-deadbeef" in retry.split("broke these rules:")[1]
    assert "[^g-deadbeef]" not in text.body and f"[^{IDS[7]}]" in text.body and "]:" not in text.body
    assert text.report["calls"] == 2 and text.report["final_problems"]
    assert "removed citation g-deadbeef" in text.report["repairs"]
    assert text.report["uncited_required"] == [IDS[3]]  # recorded, not invented
    assert text.report["unaccounted"] == [IDS[3]]
    assert text.points == points_of([IDS[7]])["points"]


def test_a_good_answer_takes_one_call(corpus: Corpus) -> None:
    llm = FakeWriter()
    text = PageWriter(llm, FakeWriter(), corpus.plan, LOGGER).write(_context(corpus))
    assert len(llm.calls) == 1 and llm.calls[0]["task"] == "write:agencies"
    assert text.report["final_problems"] == [] and text.report["links"] == 2
    assert text.report["points"] == 3 and text.report["unaccounted"] == []
    assert [p["claims"] for p in text.points] == [[IDS[0]], [IDS[4]], [IDS[8]]]


def test_update_sends_the_current_page_and_the_changes(corpus: Corpus) -> None:
    context = _context(corpus)
    update = FakeWriter("fake-update", respond=lambda s, u, t: {
        **points_of(main_ids_in(u)), "no_change": True, "body": "", "summary": "",
        "questions": [], "related": []})
    current = {"body": article(sorted(context.ids), "agencies", "עריכה ידנית של הבעלים."),
               "summary": "תקציר", "questions": ["ש?"], "related": [],
               **points_of(context.main_ids)}
    text = PageWriter(FakeWriter(), update, corpus.plan, LOGGER).update(
        context, current, ClaimDelta(changed=(IDS[0],), removed=("g-000000ff",)))
    user = _flatten(update.calls[0]["user"])
    assert "עריכה ידנית של הבעלים." in user
    assert f"changed: {IDS[0]} |" in user and "removed: g-000000ff" in user
    assert set(claim_ids_in(update.calls[0]["user"])) == context.ids
    assert not text.changed and "עריכה ידנית של הבעלים." in text.body
    assert text.summary == "תקציר"


def test_an_editor_request_goes_into_the_changes_and_leaves_other_updates_alone(corpus: Corpus) -> None:
    context = _context(corpus)
    current = {"body": article(sorted(context.ids), "agencies"), "summary": "תקציר",
               "questions": [], "related": [], **points_of(context.main_ids)}
    kwargs = {"new": (), "changed": (IDS[0],), "removed": (), "brief_changed": False}
    plain = update_prompt(context, current, **kwargs)
    assert update_prompt(context, current, request="", **kwargs) == plain
    assert "editor request" not in _flatten(plain[1])
    system, user = update_prompt(context, current, request="לתקן   את התאריך", **kwargs)
    changes = _flatten(user).split("<changes>")[1].split("</changes>")[0]
    assert system == plain[0] and f"changed: {IDS[0]} |" in changes
    assert changes.rstrip().endswith("לתקן את התאריך") and "editor request" in changes
    update = FakeWriter("fake-update")
    PageWriter(FakeWriter(), update, corpus.plan, LOGGER).update(
        context, current, ClaimDelta(), request="להוסיף את שני הצדדים")
    assert "להוסיף את שני הצדדים" in _flatten(update.calls[0]["user"])


# ------------------------------------------------------------------- front matter


def test_front_matter_is_valid_and_sources_cover_every_footnote(corpus: Corpus) -> None:
    context = _context(corpus)
    body = article([IDS[0], IDS[4]], "agencies")
    front_matter = page_front_matter(
        context, corpus.plan, body=body, summary="ת", questions=["ש?"],
        related=[{"slug": "costs", "relation": "see_also"}, {"slug": "gone", "relation": "x"}],
        labels=[{"id": "usa", "kind": "country"}], model="m", generated_at="2026-01-01T00:00:00Z")
    validate_front_matter(front_matter)
    assert [s["id"] for s in front_matter["sources"]] == cited_ids(body)
    assert front_matter["sources"][0]["resource"] == f"claim:{IDS[0]}"
    assert front_matter["generated"] == {"by": "wiki-from-whatsapp/m", "at": "2026-01-01T00:00:00Z"}
    assert front_matter["related"] == [{"slug": "costs", "relation": "see_also"}]
    assert front_matter["claims"]["total"] == len(context.records)
    assert front_matter["path"] == [{"id": "before", "title": "לפני"}, {"id": "choosing", "title": "בחירה"}]


def test_label_rule_needs_concentration_and_gives_every_label_a_home(corpus: Corpus) -> None:
    rule = LabelRule(corpus, list(corpus.plan["pages"]))
    # usa is on every even claim: agencies and clinics hold only even claims -> lift 2
    assert rule("agencies") == [{"id": "usa", "kind": "country"}]
    assert rule("costs") == []  # odd claims + one mention: usa is not concentrated there
    assert all(lab["id"] != "georgia" for pid in corpus.plan["pages"] for lab in rule(pid))
