"""Step 7's code-side guarantees, prompts and front matter, offline on synthetic data."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from step_6_plan import operations
from step_7_write_pages.checks import AnswerRules, cited_ids, problems, repair, rules_for, said, with_definitions
from step_7_write_pages.front_matter import LabelRule, page_front_matter
from step_7_write_pages.inputs import (
    PAGES_CONFIG,
    Corpus,
    PageContext,
    input_fingerprint,
    page_state,
    writer_fingerprint,
)
from step_7_write_pages.prompts import write_prompt
from step_7_write_pages.writer import GroupDelta, PageWriter
from tests.pages_fixtures import SECRET, FakeWriter, PagesWorkspace, article, group_ids_in
from utils.llm_client import _flatten
from utils.okf import validate_front_matter

LOGGER = logging.getLogger("test-pages")


@pytest.fixture()
def corpus(tmp_path: Path) -> Corpus:
    ws = PagesWorkspace(tmp_path)
    return Corpus.load(ws.groups_path, ws.plan_path, ws.assignments_path)


def _context(corpus: Corpus, page_id: str = "agencies") -> PageContext:
    return corpus.context(page_id, corpus.catalogue())


# ------------------------------------------------------------------ inputs and prompt


def test_primary_and_second_page_and_unplaced_groups(corpus: Corpus) -> None:
    agencies = {r["id"]: r for r in _context(corpus, "agencies").records}
    costs = {r["id"]: r for r in _context(corpus, "costs").records}
    assert agencies["g-00000004"]["role"] == "main"
    assert costs["g-00000004"]["role"] == "mention" and costs["g-00000004"]["primary"] == "agencies"
    _, user = write_prompt(_context(corpus, "costs"))
    assert "g-00000004 | mention -> agencies |" in _flatten(user)
    every_prompt = "".join(_flatten(write_prompt(_context(corpus, pid))[1])
                           for pid in ("agencies", "costs", "clinics", "hospital"))
    assert "g-0000000b" not in every_prompt  # group 11 is unplaced


def test_prompt_never_sees_identities_claims_or_reactions(corpus: Corpus) -> None:
    for page_id in ("agencies", "costs", "clinics", "hospital"):
        system, user = write_prompt(_context(corpus, page_id))
        text = system + _flatten(user)
        assert SECRET not in text and "SECRET" not in text  # claim ids hold "SECRET"
        assert "❤️" not in text and "reaction" not in text.lower()


def test_group_line_says_support_recency_and_contradictions_in_words(corpus: Corpus) -> None:
    _, user = write_prompt(_context(corpus, "agencies"))
    text = _flatten(user)
    line = next(line for line in text.splitlines() if line.startswith("g-00000000 |"))
    assert "last backed by 3 in יוני 2024" in line and "by month:" in line
    _, user = write_prompt(_context(corpus, "hospital"))
    line = next(line for line in _flatten(user).splitlines() if line.startswith("g-00000003 |"))
    assert 'contradicts: g-00000006 (on clinics: "Synthetic claim number 6' in line
    assert "<as_of>2024-06-01 (יוני 2024)</as_of>" in text


def test_groups_without_assignment_are_skipped_and_counted(tmp_path: Path) -> None:
    ws = PagesWorkspace(tmp_path)
    payload = ws.assignments()
    del payload["assignments"]["g-00000001"]
    ws.write_assignments(payload)
    corpus = Corpus.load(ws.groups_path, ws.plan_path, ws.assignments_path)
    assert corpus.skipped == ["g-00000001"]
    assert "g-00000001" not in _context(corpus, "costs").ids


def test_fingerprint_follows_text_inputs_not_the_catalogue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = PagesWorkspace(tmp_path)

    def fp() -> str:
        corpus = Corpus.load(ws.groups_path, ws.plan_path, ws.assignments_path)
        return input_fingerprint(page_state(_context(corpus), writer_fingerprint("m")))

    first = fp()
    assert fp() == first
    plan = ws.plan()
    plan = operations.rename(plan, "costs", "שם חדש").plan  # another page: catalogue only
    plan = operations.rename(plan, "usa", "תווית").plan
    ws.write_plan(plan)
    assert fp() == first
    groups = ws.groups()
    groups[0]["supporter_count"] += 1
    ws.write_groups(groups)
    assert fp() != first
    changed = fp()
    monkeypatch.setitem(PAGES_CONFIG, "prompt_version", 99)
    assert fp() != changed


# ------------------------------------------------------------------------ checks


def _rules(corpus: Corpus, page_id: str = "agencies") -> tuple[PageContext, AnswerRules]:
    context = _context(corpus, page_id)
    return context, rules_for(context.page_id, context.records, corpus.plan,
                              required=context.required_ids(), coverage_ids=context.main_ids)


def test_problems_name_unknown_ids_links_and_uncited_groups(corpus: Corpus) -> None:
    context, rules = _rules(corpus, "hospital")
    body = "טקסט [^g-00000007][^g-deadbeef] ראו [[nowhere|שם]] ו[[hospital|כאן]] ו[[costs#x|עוגן]]."
    found = problems({"body": body}, rules)
    assert any("g-deadbeef" in p for p in found)
    assert any("nowhere" in p and "hospital" in p for p in found)
    assert any("malformed" in p for p in found)
    required = context.required_ids()
    assert required == ["g-00000003", "g-00000007"]
    assert any("disputed or contradicted" in p and "g-00000003" in p for p in found)
    assert any("main groups are cited" in p for p in found)
    full = article(sorted(context.ids), "hospital")
    assert problems({"body": full}, rules) == []


def test_repair_removes_what_code_can_fix(corpus: Corpus) -> None:
    _, rules = _rules(corpus)
    body = ("---\ntitle: x\n---\n# כותרת\n\nטקסט [^g-00000000][^g-deadbeef] [[nowhere|שם]] "
            "[[costs|עלויות]] [[costs#x|עוגן]] [[label:mars|מאדים]].\n\n[^g-00000000]: הגדרה\n")
    fixed, repairs = repair({"body": body, "summary": "תקציר [^g-00000000] [[costs|עם קישור]]",
                             "questions": ["ש?", ""],
                             "related": [{"page_id": "costs", "relation": "See Also"},
                                         {"page_id": "agencies", "relation": "self"},
                                         {"page_id": "ghost", "relation": "x"}]}, rules)
    assert fixed["body"].startswith("## כותרת")
    assert "[^g-deadbeef]" not in fixed["body"] and "[^g-00000000]" in fixed["body"]
    assert "[[costs|עלויות]]" in fixed["body"] and "[[nowhere" not in fixed["body"]
    assert " שם " in fixed["body"] and "עוגן" in fixed["body"] and "[[costs#x" not in fixed["body"]
    assert "מאדים" in fixed["body"] and "label:mars" not in fixed["body"]
    assert "]:" not in fixed["body"]
    assert fixed["summary"] == "תקציר עם קישור"
    assert fixed["questions"] == ["ש?"]
    assert fixed["related"] == [{"slug": "costs", "relation": "see_also"}]
    assert len(repairs) >= 6


def test_footnote_definitions_are_exactly_the_cited_groups_without_counts(corpus: Corpus) -> None:
    context = _context(corpus)
    records = {r["id"]: r for r in context.records}
    body = with_definitions("א [^g-00000004]. ב [^g-00000000][^g-00000004].\n\n[^g-x]: ישן\n", records)
    definitions = [line for line in body.splitlines() if line.startswith("[^")]
    assert [line.split("]:")[0][2:] for line in definitions] == ["g-00000004", "g-00000000"]
    assert definitions[1] == f"[^g-00000000]: {records['g-00000000']['text']} ({said(records['g-00000000'])})"
    assert said(records["g-00000000"]) == "נאמר בין ינואר 2023 ליוני 2024"
    assert said(records["g-00000004"]) == "נאמר בין ינואר 2023 ליוני 2024"
    assert "backed" not in body and "תומכ" not in body
    assert cited_ids(body) == ["g-00000004", "g-00000000"]


# ------------------------------------------------------------------------ writer


def test_one_retry_names_the_problems_then_the_answer_is_repaired(corpus: Corpus) -> None:
    context = _context(corpus, "hospital")
    answers = iter([{"body": "טקסט [^g-deadbeef].", "summary": "", "questions": [], "related": []},
                    {"body": "עדיין [^g-deadbeef][^g-00000007].", "summary": "ס",
                     "questions": ["ש"], "related": []}])
    llm = FakeWriter(respond=lambda system, user, task: next(answers))
    text = PageWriter(llm, FakeWriter(), corpus.plan, LOGGER).write(context)
    assert len(llm.calls) == 2
    retry = _flatten(llm.calls[1]["user"])
    assert "<previous_answer>" in retry and "g-deadbeef" in retry.split("broke these rules:")[1]
    assert "[^g-deadbeef]" not in text.body and "[^g-00000007]: " in text.body
    assert text.report["calls"] == 2 and text.report["final_problems"]
    assert "removed citation g-deadbeef" in text.report["repairs"]
    assert text.report["uncited_required"] == ["g-00000003"]  # recorded, not invented


def test_a_good_answer_takes_one_call(corpus: Corpus) -> None:
    llm = FakeWriter()
    text = PageWriter(llm, FakeWriter(), corpus.plan, LOGGER).write(_context(corpus))
    assert len(llm.calls) == 1 and llm.calls[0]["task"] == "write:agencies"
    assert text.report["cited_share"] == 1.0 and text.report["links"] == 2


def test_update_sends_the_current_page_and_the_changes(corpus: Corpus) -> None:
    context = _context(corpus)
    update = FakeWriter("fake-update", respond=lambda s, u, t: {
        "no_change": True, "body": "", "summary": "", "questions": [], "related": []})
    current = {"body": article(sorted(context.ids), "agencies", "עריכה ידנית של הבעלים."),
               "summary": "תקציר", "questions": ["ש?"], "related": []}
    text = PageWriter(FakeWriter(), update, corpus.plan, LOGGER).update(
        context, current, GroupDelta(changed=("g-00000000",), removed=("g-000000ff",)))
    user = _flatten(update.calls[0]["user"])
    assert "עריכה ידנית של הבעלים." in user
    assert "changed: g-00000000 |" in user and "removed: g-000000ff" in user
    assert set(group_ids_in(update.calls[0]["user"])) == context.ids
    assert not text.changed and "עריכה ידנית של הבעלים." in text.body
    assert text.summary == "תקציר"


# ------------------------------------------------------------------- front matter


def test_front_matter_is_valid_and_sources_cover_every_footnote(corpus: Corpus) -> None:
    context = _context(corpus)
    body = with_definitions(article(["g-00000000", "g-00000004"], "agencies"),
                            {r["id"]: r for r in context.records})
    front_matter = page_front_matter(
        context, corpus.plan, body=body, summary="ת", questions=["ש?"],
        related=[{"slug": "costs", "relation": "see_also"}, {"slug": "gone", "relation": "x"}],
        labels=[{"id": "usa", "kind": "country"}], model="m", generated_at="2026-01-01T00:00:00Z")
    validate_front_matter(front_matter)
    assert [s["id"] for s in front_matter["sources"]] == cited_ids(body)
    assert front_matter["sources"][0]["resource"] == "claim-group:g-00000000"
    assert front_matter["generated"] == {"by": "wiki-from-whatsapp/m", "at": "2026-01-01T00:00:00Z"}
    assert front_matter["related"] == [{"slug": "costs", "relation": "see_also"}]
    assert front_matter["claims"]["total"] == len(context.records)
    assert front_matter["path"] == [{"id": "before", "title": "לפני"}, {"id": "choosing", "title": "בחירה"}]


def test_label_rule_needs_concentration_and_gives_every_label_a_home(corpus: Corpus) -> None:
    rule = LabelRule(corpus, list(corpus.plan["pages"]))
    # usa is on every even group: agencies and clinics hold only even groups -> lift 2
    assert rule("agencies") == [{"id": "usa", "kind": "country"}]
    assert rule("costs") == []  # odd groups + one mention: usa is not concentrated there
    assert all(lab["id"] != "georgia" for pid in corpus.plan["pages"] for lab in rule(pid))
