"""The owner's rule: a negative statement about a named entity that fewer than two members
reported is withheld from every page, accounted for and reported. Synthetic claims and a fake
writer only (no paid call)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from step_5_same_complaint.release import withheld_ids
from step_5_same_complaint.withhold import REASON, withholds
from step_8_write_pages.checks import problems, repair, rules_for
from step_8_write_pages.inputs import Corpus
from step_9_review.inputs import ReviewInput
from tests.claim_fixtures import claim_record, claim_records
from tests.pages_fixtures import IDS, FakeWriter, PagesWorkspace
from tests.site_fixtures import issues_of, make_page, make_site_plan, run_site
from utils.llm_client import _flatten

AGENCY, CLINIC = IDS[4], IDS[6]   # lone complaints that name an entity: withheld
TWO_REPORTS = IDS[1]              # a complaint two members made: published


def _claim(stance: str = "negative", supporters: int = 1, entities: tuple[str, ...] = ("e0001",)
           ) -> dict[str, Any]:
    return claim_record(1, stance=stance, supporter_count=supporters, entity_ids=list(entities),
                        entity_names=[f"Entity {e}" for e in entities])


@pytest.mark.parametrize(("claim", "withheld"), [
    (_claim(), True),
    (_claim(supporters=0), True),
    (_claim(supporters=1, entities=("e0001", "e0002")), True),
    (_claim(supporters=2), False),
    (_claim(supporters=7), False),
    (_claim(stance="positive"), False),
    (_claim(stance="neutral"), False),
    (_claim(stance="factual"), False),
    (_claim(entities=()), False),                    # nobody named: not covered
    (_claim(supporters=0, entities=()), False),
])
def test_the_rule(claim: dict[str, Any], withheld: bool) -> None:
    assert withholds(claim) is withheld


def test_the_ids_are_the_short_ids_of_the_withheld_claims() -> None:
    lone, backed = _claim(), claim_record(5, supporter_count=2)
    assert withheld_ids([lone, backed]) == {lone["short_id"]}


@pytest.mark.parametrize("key", ["entity_ids", "stance", "supporter_count"])
@pytest.mark.parametrize("stance", ["negative", "positive"])
def test_a_record_without_a_key_of_the_rule_raises(key: str, stance: str) -> None:
    claim = _claim(stance=stance)
    del claim[key]
    with pytest.raises(KeyError):
        withholds(claim)


def test_the_old_entities_key_raises_instead_of_releasing_a_lone_complaint() -> None:
    claim = _claim()
    claim["entities"] = claim.pop("entity_ids")
    with pytest.raises(KeyError):
        withholds(claim)


def test_withheld_ids_needs_the_short_id() -> None:
    claim = _claim()
    del claim["short_id"]
    with pytest.raises(KeyError):
        withheld_ids([claim])


def _complaint(claim: dict[str, Any], **fields: Any) -> dict[str, Any]:
    """The workspace's ``claim`` as a lone complaint about a doctor (``fields`` override)."""
    index = IDS.index(claim["short_id"])
    return claim_record(index, **{
        "stance": "negative", "supporter_count": 1, "entity_ids": [f"e{index:04d}"],
        "entity_names": [f"Doctor {index}"], "contradicts": claim["contradicts"],
        "claim_text": f"Private complaint {index} about a doctor.", **fields})


@pytest.fixture()
def ws(tmp_path: Path) -> PagesWorkspace:
    """Claim 4 (agencies, a mention on costs) and claim 6 (clinics, contradicted by claim 3 on
    hospital) are lone complaints that name an entity; claim 1 (costs) is a complaint two
    members made."""
    workspace = PagesWorkspace(tmp_path)
    claims = workspace.claims()
    for index in (4, 6):
        claims[index] = _complaint(claims[index])
    workspace.write_claims(claims)
    return workspace


def _prompts(llm: FakeWriter) -> str:
    return "\n".join(_flatten(call["user"]) + _flatten(call["system"]) for call in llm.calls)


def test_a_withheld_claim_reaches_no_prompt_and_no_front_matter(ws: PagesWorkspace) -> None:
    llm = FakeWriter()
    ws.run(llm)
    text = _prompts(llm)
    for sid, index in ((AGENCY, 4), (CLINIC, 6)):
        assert sid not in text and f"Private complaint {index}" not in text and f"Doctor {index}" not in text
    assert TWO_REPORTS in text  # two members reported it
    pages = ws.pages()["pages"]
    for page_id, page in pages.items():
        front = page["front_matter"]
        assert AGENCY not in page["body_md"] and CLINIC not in page["body_md"]
        assert {AGENCY, CLINIC}.isdisjoint(source["id"] for source in front["sources"])
        assert not {"Doctor 4", "Doctor 6"} & set(front["entities"])
    claims_on = {"agencies": 2, "costs": 3, "clinics": 2, "hospital": 2}  # without 4 and 6
    assert {pid: p["front_matter"]["claims"]["total"] for pid, p in pages.items()} == claims_on


def test_steps_7_and_8_run_with_no_entities_file(ws: PagesWorkspace, tmp_path: Path) -> None:
    ws.run()
    ws.sources.entities.unlink()
    ws.run(rebuild=True)
    ReviewInput.load(ws.artifact, ws.sources, ws.plan_path, ws.assignments_path)
    assert not list(tmp_path.glob("entities*"))


def test_a_withheld_claim_is_accounted_for_and_listed(ws: PagesWorkspace) -> None:
    result = ws.run()
    pages = ws.pages()
    left = {pid: page["left_out"] for pid, page in pages["pages"].items()}
    assert left["agencies"] == [{"claim": AGENCY, "reason": REASON}]
    assert left["clinics"] == [{"claim": CLINIC, "reason": REASON}]
    assert left["costs"] == [] and left["hospital"] == []  # a mention is accounted on its page
    assert pages["metadata"]["withheld"] == [
        {"claim": AGENCY, "page_id": "agencies", "entity_ids": ["e0004"], "supporter_count": 1},
        {"claim": CLINIC, "page_id": "clinics", "entity_ids": ["e0006"], "supporter_count": 1}]
    assert result["summary"]["withheld"] == 2
    reports = pages["metadata"]["pages"]
    assert all(r["report"]["unaccounted"] == [] for r in reports.values())


def test_step_8_reads_what_the_writer_read(ws: PagesWorkspace) -> None:
    ws.run()
    review = ReviewInput.load(ws.artifact, ws.sources, ws.plan_path, ws.assignments_path)
    shown = {sid for page in review.pages.values() for sid in page.claim_ids}
    assert shown and {AGENCY, CLINIC}.isdisjoint(shown)
    for page in review.pages.values():
        for record in page.records:
            assert all(other["id"] not in (AGENCY, CLINIC) for other in record["contradicts"])


def test_a_second_reporter_releases_the_claim(ws: PagesWorkspace) -> None:
    ws.run()
    claims = ws.claims()
    claims[4]["supporter_count"] = 2
    ws.write_claims(claims)
    update = FakeWriter("fake-update")
    ws.run(FakeWriter(), update)
    assert sorted(update.tasks()) == ["update:agencies", "update:costs"]  # main and mention
    assert AGENCY in _prompts(update)
    pages = ws.pages()
    assert {"claim": AGENCY, "reason": REASON} not in pages["pages"]["agencies"]["left_out"]
    assert [w["claim"] for w in pages["metadata"]["withheld"]] == [CLINIC]


def test_a_claim_is_withheld_when_it_drops_to_one_reporter_and_released_when_it_names_nothing(
        ws: PagesWorkspace) -> None:
    ws.run()
    claims = ws.claims()
    claims[1] = claim_record(1, supporter_count=1)
    ws.write_claims(claims)
    again = FakeWriter("fake-update")
    ws.run(FakeWriter(), again)
    assert again.tasks() == ["update:costs"] and TWO_REPORTS not in _prompts(again).split("<claims")[-1]
    assert {"claim": TWO_REPORTS, "reason": REASON} in ws.pages()["pages"]["costs"]["left_out"]
    # a complaint that names nothing is published as before
    claims[6] = _complaint(claims[6], entity_ids=[], entity_names=[])
    ws.write_claims(claims)
    update = FakeWriter("fake-update")
    ws.run(FakeWriter(), update)
    # clinics gets claim 6 back; on hospital, claim 3 shows its contradiction again
    assert sorted(update.tasks()) == ["update:clinics", "update:hospital"]
    assert [w["claim"] for w in ws.pages()["metadata"]["withheld"]] == [TWO_REPORTS, AGENCY]


def test_the_contradiction_of_a_withheld_claim_is_not_shown(ws: PagesWorkspace) -> None:
    corpus = Corpus.load(ws.sources, ws.plan_path, ws.assignments_path)
    assert corpus.claims[IDS[3]]["contradicts"] == [CLINIC]
    record = corpus.record(IDS[3], "hospital")
    assert record["contradicts"] == []
    assert corpus.withheld == {AGENCY, CLINIC} and corpus.withheld_on("costs") == []
    assert corpus.primary_page(AGENCY) is None and AGENCY not in corpus.page_claims("costs")


def test_a_sentence_resting_on_a_withheld_claim_is_deleted_whole() -> None:
    records = [{"id": "g-0000000a", "role": "main", "opposers": 0, "contradicts": [],
                "supporters": 1, "last": "2024-01-01"}]
    rules = rules_for("p", records, {"pages": {"p": {}}, "labels": {}}, required=[],
                      withheld=["g-0000000w"])
    answer = {"points": [{"kind": "fact", "statement": "s", "claims": ["g-0000000a"]}],
              "left_out": [], "summary": "", "questions": [], "related": [],
              "body": "אחד.[^g-0000000a] שתיים על הרופא.[^g-0000000w] שלוש.[^g-0000000a]\n"}
    found = problems(answer, rules)
    assert any("g-0000000w" in problem and "delete every sentence" in problem for problem in found)
    fixed, repairs = repair(answer, rules)
    assert "הרופא" not in fixed["body"] and "g-0000000w" not in fixed["body"]
    assert fixed["body"].count("[^g-0000000a]") == 2
    assert repairs == ["deleted a sentence resting on withheld claim(s) g-0000000w"]


def test_step_9_reports_a_page_that_cites_a_withheld_claim(tmp_path: Path) -> None:
    plan = make_site_plan()
    pages = {"agencies": make_page(plan, "agencies", f"טקסט.[^{TWO_REPORTS}]\n"),
             "costs": make_page(plan, "costs", f"טקסט.[^{IDS[2]}]\n")}
    claims = claim_records(4)  # claim 1: negative, two supporters, entity e0001
    for name in "ab":
        (tmp_path / name).mkdir()
    published = run_site(tmp_path / "a", plan, pages, claims)
    assert issues_of(published, "withheld_reference") == []
    claims[1] = claim_record(1, supporter_count=1, contradicts=claims[1]["contradicts"])
    flagged = run_site(tmp_path / "b", plan, pages, claims)
    assert issues_of(flagged, "withheld_reference") == [("agencies", TWO_REPORTS)]
