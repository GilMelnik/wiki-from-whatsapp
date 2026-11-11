"""The owner's rule (task 29): a negative statement about a named individual that fewer than two
members reported is withheld from every page, accounted for and reported. Synthetic groups and
a fake writer only (no paid call)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from step_7_write_pages.checks import problems, repair, rules_for
from step_7_write_pages.inputs import Corpus
from step_7_write_pages.withhold import REASON, WithholdRule
from step_8_review.inputs import ReviewInput
from tests.pages_fixtures import FakeWriter, PagesWorkspace
from tests.plan_fixtures import make_entities, make_groups
from tests.site_fixtures import issues_of, make_page, make_site_plan, run_site
from utils.llm_client import _flatten

DR, CLINIC = "g-00000004", "g-00000006"   # withheld: a person, and an entity of unknown kind
COMPLAINT = "g-00000001"                   # a lone complaint about a business: published


def _group(stance: str = "negative", supporters: int = 1, entities: tuple[str, ...] = ("e1",)
           ) -> dict[str, Any]:
    return {"group_id": "g-1", "stance": stance, "supporter_count": supporters,
            "entities": list(entities)}


@pytest.mark.parametrize(("group", "kinds", "withheld"), [
    (_group(), {"e1": "person"}, True),
    (_group(supporters=0), {"e1": "person"}, True),
    (_group(supporters=2), {"e1": "person"}, False),
    (_group(stance="positive"), {"e1": "person"}, False),
    (_group(stance="neutral"), {"e1": "person"}, False),
    (_group(stance="factual"), {"e1": "person"}, False),
    (_group(), {"e1": "organisation"}, False),
    (_group(), {"e1": "place"}, False),
    (_group(), {"e1": "other"}, False),
    (_group(), {"e1": None}, True),               # no kind yet: counts as a person
    (_group(), {"e1": "a kind nobody knows"}, True),
    (_group(), {}, True),                          # an entity the file does not hold
    (_group(entities=("e1", "e2")), {"e1": "organisation", "e2": "person"}, True),
    (_group(entities=()), {}, False),              # nobody named: not covered
    ({**_group(), "supporter_count": None}, {"e1": "person"}, True),
])
def test_the_rule(group: dict[str, Any], kinds: dict[str, str | None], withheld: bool) -> None:
    assert WithholdRule(kinds).withholds(group) is withheld


@pytest.fixture()
def ws(tmp_path: Path) -> PagesWorkspace:
    """Group 4 (agencies, a mention on costs) is a lone complaint about a person, group 6
    (clinics, contradicted by group 3 on hospital) one about an entity without a kind; group 1
    (costs) is a lone complaint about an organisation."""
    workspace = PagesWorkspace(tmp_path)
    groups = workspace.groups()
    for index in (4, 6):
        groups[index].update(stance="negative", supporter_count=1,
                             entity_names=[f"Doctor {index}"],
                             claim_text=f"Private complaint {index} about a doctor.")
    workspace.write_groups(groups)
    workspace.write_entities(make_entities(groups, e0004="person", e0006=None))
    return workspace


def _prompts(llm: FakeWriter) -> str:
    return "\n".join(_flatten(call["user"]) + _flatten(call["system"]) for call in llm.calls)


def test_a_withheld_group_reaches_no_prompt_and_no_front_matter(ws: PagesWorkspace) -> None:
    llm = FakeWriter()
    ws.run(llm)
    text = _prompts(llm)
    for gid, index in ((DR, 4), (CLINIC, 6)):
        assert gid not in text and f"Private complaint {index}" not in text and f"Doctor {index}" not in text
    assert COMPLAINT in text  # a business is not covered
    pages = ws.pages()["pages"]
    for page_id, page in pages.items():
        front = page["front_matter"]
        assert DR not in page["body_md"] and CLINIC not in page["body_md"]
        assert {DR, CLINIC}.isdisjoint(source["id"] for source in front["sources"])
        assert not {"Doctor 4", "Doctor 6"} & set(front["entities"])
    groups_on = {"agencies": 2, "costs": 3, "clinics": 2, "hospital": 2}  # without 4 and 6
    assert {pid: p["front_matter"]["claims"]["total"] for pid, p in pages.items()} == groups_on


def test_a_withheld_group_is_accounted_for_and_listed(ws: PagesWorkspace) -> None:
    result = ws.run()
    pages = ws.pages()
    left = {pid: page["left_out"] for pid, page in pages["pages"].items()}
    assert left["agencies"] == [{"group": DR, "reason": REASON}]
    assert left["clinics"] == [{"group": CLINIC, "reason": REASON}]
    assert left["costs"] == [] and left["hospital"] == []  # a mention is accounted on its page
    assert pages["metadata"]["withheld"] == [
        {"group_id": DR, "page_id": "agencies", "entity_ids": ["e0004"], "supporter_count": 1},
        {"group_id": CLINIC, "page_id": "clinics", "entity_ids": ["e0006"], "supporter_count": 1}]
    assert result["summary"]["withheld"] == 2
    reports = pages["metadata"]["pages"]
    assert all(r["report"]["unaccounted"] == [] for r in reports.values())


def test_step_8_reads_what_the_writer_read(ws: PagesWorkspace) -> None:
    ws.run()
    review = ReviewInput.load(ws.artifact, *ws.paths()[:3], ws.entities_path)
    shown = {gid for page in review.pages.values() for gid in page.group_ids}
    assert shown and {DR, CLINIC}.isdisjoint(shown)
    for page in review.pages.values():
        for record in page.records:
            assert all(other["id"] not in (DR, CLINIC) for other in record["contradicts"])


def test_a_second_reporter_releases_the_group(ws: PagesWorkspace) -> None:
    ws.run()
    groups = ws.groups()
    groups[4]["supporter_count"] = 2
    ws.write_groups(groups)
    update = FakeWriter("fake-update")
    ws.run(FakeWriter(), update)
    assert sorted(update.tasks()) == ["update:agencies", "update:costs"]  # main and mention
    assert DR in _prompts(update)
    pages = ws.pages()
    assert {"group": DR, "reason": REASON} not in pages["pages"]["agencies"]["left_out"]
    assert [w["group_id"] for w in pages["metadata"]["withheld"]] == [CLINIC]


def test_the_owner_naming_a_business_releases_it_and_a_person_withholds_it(ws: PagesWorkspace) -> None:
    ws.run()
    ws.write_entities(make_entities(ws.groups(), e0004="person", e0006="organisation"))
    update = FakeWriter("fake-update")
    ws.run(FakeWriter(), update)
    # clinics gets group 6 back; on hospital, group 3 shows its contradiction again
    assert sorted(update.tasks()) == ["update:clinics", "update:hospital"]
    ws.write_entities(make_entities(ws.groups(), e0004="person", e0006="organisation",
                                    e0001="person"))
    again = FakeWriter("fake-update")
    ws.run(FakeWriter(), again)
    assert again.tasks() == ["update:costs"] and COMPLAINT not in _prompts(again).split("<groups")[-1]
    assert {"group": COMPLAINT, "reason": REASON} in ws.pages()["pages"]["costs"]["left_out"]


def test_the_contradiction_of_a_withheld_group_is_not_shown(ws: PagesWorkspace) -> None:
    corpus = Corpus.load(*ws.paths())
    record = corpus.record("g-00000003", "hospital")
    assert record["contradicts"] == []
    assert corpus.withheld == {DR, CLINIC} and corpus.withheld_on("costs") == []
    assert corpus.primary_page(DR) is None and DR not in corpus.page_group_ids("costs")


def test_a_sentence_resting_on_a_withheld_group_is_deleted_whole() -> None:
    records = [{"id": "g-0000000a", "role": "main", "opposers": 0, "contradicts": []}]
    rules = rules_for("p", records, {"pages": {"p": {}}, "labels": {}}, required=[],
                      withheld=["g-0000000w"])
    answer = {"points": [{"kind": "fact", "statement": "s", "groups": ["g-0000000a"]}],
              "left_out": [], "summary": "", "questions": [], "related": [],
              "body": "אחד.[^g-0000000a] שתיים על הרופא.[^g-0000000w] שלוש.[^g-0000000a]\n"}
    found = problems(answer, rules)
    assert any("g-0000000w" in problem and "delete every sentence" in problem for problem in found)
    fixed, repairs = repair(answer, rules)
    assert "הרופא" not in fixed["body"] and "g-0000000w" not in fixed["body"]
    assert fixed["body"].count("[^g-0000000a]") == 2
    assert repairs == ["deleted a sentence resting on withheld group(s) g-0000000w"]


def test_step_9_reports_a_page_that_cites_a_withheld_group(tmp_path: Path) -> None:
    plan = make_site_plan()
    pages = {"agencies": make_page(plan, "agencies", f"טקסט.[^{COMPLAINT}]\n"),
             "costs": make_page(plan, "costs", "טקסט.[^g-00000002]\n")}
    groups = make_groups(4)  # group 1: negative, one supporter, entity e0001
    for name in "abc":
        (tmp_path / name).mkdir()
    published = run_site(tmp_path / "a", plan, pages, groups)
    assert issues_of(published, "withheld_reference") == []
    flagged = run_site(tmp_path / "b", plan, pages, groups, make_entities(groups, e0001="person"))
    assert issues_of(flagged, "withheld_reference") == [("agencies", COMPLAINT)]
    unknown = run_site(tmp_path / "c", plan, pages, groups, make_entities(groups, kind=None))
    assert issues_of(unknown, "withheld_reference") == [("agencies", COMPLAINT)]
