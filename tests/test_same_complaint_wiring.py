"""The same-complaint check wired into the pipeline: what is withheld now follows the check's
answers and the owner's marks ("keep withheld", "released" by hand) in step 8 (the writer), step 11 (the site), the
Plan page and the update run. Synthetic claims, people and answers, a fake model; no paid call.
"""

from __future__ import annotations

from functools import partial
from pathlib import Path
from typing import Any

import pytest

from review_tool.artifacts import GRAPH
from review_tool.steps import SERVED_STEPS
from review_tool.toolset import TOOL_SPECS
from step_5_same_complaint.claim_records import ClaimSources, Record, load_claim_records
from step_5_same_complaint import run as same_complaint
from step_5_same_complaint.release import (
    Marks,
    entity_lists,
    list_fingerprint,
    save_marks,
    withheld_ids,
)
from step_5_same_complaint.withhold import REASON
from update_run.driver import STOPPED, UPDATE_CONFIG
from update_run.estimate import Estimator
from tests.claim_fixtures import claim_record, claim_records, write_claim_inputs
from tests.pages_fixtures import IDS, FakeWriter, PagesWorkspace
from tests.plan_fixtures import short_id_of
from tests.same_complaint_support import FakeModel, World, person, same_text, set_backers
from tests.site_fixtures import issues_of, make_page, make_site_plan, run_site
from tests.test_plan_tool_assignments import AssignSetup
from tests.update_support import PAID_STEPS, newer_chat
from tests.update_support import World as UpdateWorld
from utils.json_io import write_json_file
from utils.llm_client import _flatten
from utils.paths import STEP_4, STEP_5, STEP_6, STEP_7

AGENCY_ID, AGENCY_NAME = "e0009", "Agency Nine"
DEPOSIT = {"deposit": "kept the deposit"}
FIRST, SECOND = IDS[4], IDS[6]  # page agencies (and a mention on costs), page clinics


def deposit_complaint(claim: Record, **fields: Any) -> Record:
    """The workspace's ``claim`` as one person's complaint that the agency kept the deposit."""
    index = IDS.index(claim["short_id"])
    return claim_record(index, **{
        "stance": "negative", "supporter_count": 1, "opposer_count": 0,
        "entity_ids": [AGENCY_ID], "entity_names": [AGENCY_NAME],
        "contradicts": claim["contradicts"],
        "claim_text": f"Synthetic: the agency kept the deposit ({index}).", **fields})


def write_claims(ws: PagesWorkspace, claims: list[Record]) -> None:
    """Write the claims; the two complaints come from two different people."""
    ws.write_claims(claims)
    set_backers(ws.sources, {claims[4]["claim_id"]: [person(1)],
                             claims[6]["claim_id"]: [person(2)]})


def check(sources: ClaimSources) -> dict[str, Any]:
    """Run the same-complaint check with a model that groups the deposit complaints."""
    return same_complaint.run(sources=sources, llm=FakeModel(same_text(DEPOSIT)))  # type: ignore[arg-type]


def prompts(llm: FakeWriter) -> str:
    return "\n".join(_flatten(call["user"]) + _flatten(call["system"]) for call in llm.calls)


def withheld(ws: PagesWorkspace) -> list[str]:
    return [w["claim"] for w in ws.pages()["metadata"]["withheld"]]


@pytest.fixture()
def ws(tmp_path: Path) -> PagesWorkspace:
    workspace = PagesWorkspace(tmp_path)
    claims = workspace.claims()
    for index in (4, 6):
        claims[index] = deposit_complaint(claims[index])
    write_claims(workspace, claims)
    return workspace


# ------------------------------------------------------------------- what is withheld now


def test_without_an_answers_file_the_rule_and_the_hand_releases_decide(tmp_path: Path) -> None:
    lone, other = claim_record(1, supporter_count=1), claim_record(2, supporter_count=1)
    marks = tmp_path / "no_marks.json"
    missing = ClaimSources(audit=tmp_path / "no_audit.json",
                           same_complaint=tmp_path / "no_answers.json",
                           same_complaint_vetoes=marks)
    assert withheld_ids([lone, claim_record(5)], missing) == {lone["short_id"]}
    save_marks(Marks(released=frozenset([other["short_id"]])), marks)
    assert withheld_ids([lone, other], missing) == {lone["short_id"]}
    # a broken marks file raises: ignoring it could publish or withhold by accident
    marks.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="marks file"):
        withheld_ids([lone], missing)


def test_without_an_answers_file_step_7_withholds_both(ws: PagesWorkspace) -> None:
    llm = FakeWriter()
    ws.run(llm)
    assert withheld(ws) == [FIRST, SECOND]
    assert FIRST not in prompts(llm) and SECOND not in prompts(llm)


def test_a_released_claim_reaches_the_writer(ws: PagesWorkspace) -> None:
    result = check(ws.sources)
    assert (result["answered"], result["released"]) == (1, 2)
    llm = FakeWriter()
    ws.run(llm)
    text = prompts(llm)
    assert FIRST in text and SECOND in text and "kept the deposit (4)" in text
    pages = ws.pages()
    assert withheld(ws) == [] and pages["metadata"]["summary"]["withheld"] == 0
    assert all({"claim": FIRST, "reason": REASON} not in page["left_out"]
               for page in pages["pages"].values())


def test_a_claim_kept_withheld_stays_out_and_the_mark_can_be_taken_away(
        ws: PagesWorkspace) -> None:
    check(ws.sources)
    save_marks(Marks(frozenset([FIRST])), ws.sources.same_complaint_vetoes)
    llm = FakeWriter()
    ws.run(llm)
    # the mark keeps FIRST back, and without it one person is left behind SECOND
    assert FIRST not in prompts(llm) and SECOND not in prompts(llm)
    assert withheld(ws) == [FIRST, SECOND]
    save_marks(Marks(), ws.sources.same_complaint_vetoes)
    update = FakeWriter("fake-update")
    ws.run(FakeWriter(), update)
    # hospital shows claim 3's contradiction with SECOND again
    assert sorted(update.tasks()) == ["update:agencies", "update:clinics", "update:costs",
                                      "update:hospital"]
    assert withheld(ws) == []


def test_a_claim_released_by_hand_reaches_the_writer_without_the_check(
        ws: PagesWorkspace) -> None:
    save_marks(Marks(released=frozenset([FIRST])), ws.sources.same_complaint_vetoes)
    llm = FakeWriter()
    ws.run(llm)
    assert FIRST in prompts(llm) and SECOND not in prompts(llm)
    assert withheld(ws) == [SECOND]


def test_a_stale_answer_releases_nothing_until_the_check_asks_again(ws: PagesWorkspace) -> None:
    check(ws.sources)
    claims = ws.claims()
    claims[6] = deposit_complaint(claims[6], claim_text="Synthetic: the deposit was kept (6).")
    write_claims(ws, claims)
    ws.run()
    assert withheld(ws) == [FIRST, SECOND]  # the stored answer was given for the old texts
    assert check(ws.sources)["due"] == 1
    ws.run(FakeWriter(), FakeWriter("fake-update"))
    assert withheld(ws) == []


def test_the_same_person_twice_releases_nothing(ws: PagesWorkspace) -> None:
    claims = ws.claims()
    set_backers(ws.sources, {claims[4]["claim_id"]: [person(1)],
                             claims[6]["claim_id"]: [person(1)]})
    assert check(ws.sources)["due"] == 0  # one person in all: nothing to ask
    ws.run()
    assert withheld(ws) == [FIRST, SECOND]


# ------------------------------------------------------------------------------ step 11


def test_the_site_follows_the_check_and_the_marks(tmp_path: Path) -> None:
    world = World(tmp_path)
    first = world.add(["e0001"], [1], text="Synthetic: kept the deposit.")
    world.add(["e0001"], [2], text="Synthetic: the deposit never came back.")
    sources = world.write()
    plan = make_site_plan()
    pages = {"agencies": make_page(plan, "agencies", f"טקסט.[^{first}]\n")}
    site = partial(run_site, tmp_path, plan, pages, sources=sources)
    assert issues_of(site(), "withheld_reference") == [("agencies", first)]
    assert check(sources)["released"] == 2
    assert issues_of(site(), "withheld_reference") == []
    save_marks(Marks(frozenset([first])), sources.same_complaint_vetoes)
    assert issues_of(site(), "withheld_reference") == [("agencies", first)]


# ------------------------------------------------------------------------ the Plan page


def test_the_plan_page_follows_a_release_and_a_mark_without_a_restart(tmp_path: Path) -> None:
    setup = AssignSetup(tmp_path)
    claims = claim_records()
    for n in (1, 5):  # both on the costs page, both about the same entity
        claims[n] = claim_record(n, supporter_count=1, contradicts=claims[n]["contradicts"])
    sources = write_claim_inputs(claims, setup.root)
    set_backers(sources, {claims[1]["claim_id"]: [person(1)], claims[5]["claim_id"]: [person(2)]})
    pair = [short_id_of(1), short_id_of(5)]

    def marked() -> list[bool]:
        rows = {row["short_id"]: row["withheld"]
                for row in setup.claims(scope="page", id="costs")["items"]}
        return [rows[short_id] for short_id in pair]

    assert marked() == [True, True]
    records = load_claim_records(sources)
    entity_id = claims[1]["entity_ids"][0]
    entry = {"fingerprint": list_fingerprint(entity_lists(records)[entity_id]),
             "groups": [{"label": "same problem", "claims": pair}], "problem": None,
             "answered_at": "2026-11-19T00:00:00+00:00"}
    write_json_file({"entities": {entity_id: entry}, "metadata": {}}, sources.same_complaint)
    assert marked() == [False, False]
    save_marks(Marks(frozenset([pair[0]])), sources.same_complaint_vetoes)
    assert marked() == [True, True]


# ------------------------------------------------------- the registry and the update run


def test_the_check_is_a_paid_step_between_the_entities_and_the_plan() -> None:
    keys = [step.key for step in SERVED_STEPS.steps]
    assert keys.index(STEP_4) + 1 == keys.index(STEP_5) == keys.index(STEP_6) - 1
    step = SERVED_STEPS.step(STEP_5)
    assert step.spec.paid and step.spec.incremental_safe and step.spec.review_tool == "withheld"
    assert step.command()[1:] == ["-m", "step_5_same_complaint.run"]
    assert GRAPH.node("same_complaint").upstream == ("claims", "entities")
    assert {"same_complaint", "withheld_vetoes"} <= set(GRAPH.node("pages").upstream)
    spec = next(spec for spec in TOOL_SPECS if spec.name == "withheld")
    assert spec.reads == ("claims", "entities", "same_complaint")
    assert spec.writes == ("withheld_vetoes",)


def test_the_update_estimate_runs_the_check_dry(ws: PagesWorkspace) -> None:
    model = FakeModel(same_text(DEPOSIT))
    run = partial(same_complaint.run, sources=ws.sources, llm=model)
    estimate = Estimator(lambda step: run)(SERVED_STEPS.step(STEP_5))
    assert estimate.paid_calls == 1 and estimate.usd > 0 and model.calls == []
    assert estimate.what.startswith("1 model call(s)") and not ws.sources.same_complaint.exists()
    run()
    again = Estimator(lambda step: run)(SERVED_STEPS.step(STEP_5))
    assert again.paid_calls == 0 and again.what.startswith("no call")


def test_the_update_run_stops_for_the_withheld_page(tmp_path: Path) -> None:
    world = UpdateWorld(tmp_path)
    outcome = world.run(world.export(newer_chat()), pay=PAID_STEPS)
    while world.state()["update"]["stopped"]["step"] != STEP_5:
        outcome = world.run(pay=PAID_STEPS)
    assert outcome.status == STOPPED and world.calls[-1] == STEP_5
    assert "python -m review_tool --tool withheld" in outcome.message
    assert UPDATE_CONFIG["review_hints"]["withheld"] in outcome.message
    assert STEP_7 not in world.calls  # the next step waits for the owner's look
    world.calls.clear()
    world.run(pay=PAID_STEPS)
    assert world.calls[0] == STEP_7
