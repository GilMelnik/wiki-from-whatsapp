"""Applying suggestions from the suggestions tool: in the background, one at a time, with
events; what each kind changed; refusals and a failing provider writing nothing; "apply all
accepted"; a suggestion being applied cannot be decided on; the frame's 423 while a step runs.
Synthetic data, a fake page corrector and a fake group linker."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from review_tool.artifacts import ArtifactGraph
from review_tool.events import EventBus
from review_tool.jobs import JOB_SETTINGS, JobRunner
from review_tool.steps import StepRegistry, StepSpec
from review_tool.tools.suggestions.changes import sentence_changes, snapshot_after
from step_6_plan.operations import delete
from step_8_review.inputs import REVIEW_CONFIG
from tests.review_fixtures import corrector, sentence_a
from tests.suggestions_tool_support import FIXED, Gate, ToolSetup, failing_provider
from utils.config import PROJECT_ROOT
from utils.paths import BACKUPS_DIR
from utils.reconcile import guarded_write


@pytest.fixture()
def setup(tmp_path: Path) -> ToolSetup:
    return ToolSetup(tmp_path)


def test_a_structural_apply_runs_in_the_background_and_reports_its_change(setup: ToolSetup) -> None:
    sid = setup.ids()["add_label"]
    setup.decide("accept", sid)
    answer = setup.apply(sid)
    assert answer["queued"] == [sid] and answer["refused"] == {}
    state = setup.wait()
    assert state["running"] is None and state["queue"] == []
    outcome = state["outcomes"][0]
    assert outcome["id"] == sid and outcome["status"] == "applied" and outcome["paid"] is False
    assert outcome["message"] == REVIEW_CONFIG["messages"]["plan_applied"]
    # Events on every change (queued, started, finished); the last one carries the outcome.
    assert any(event["running"] and event["running"]["id"] == sid for event in setup.events)
    assert setup.events[-1]["outcomes"][0]["status"] == "applied"
    assert "texas" in json.loads(setup.ws.plan_artifact.edited.read_text(encoding="utf-8"))["labels"]
    detail = setup.get(f"/suggestions/{sid}")
    assert detail["suggestion"]["status"] == "applied"
    changes = detail["changes"]
    assert changes["plan"]["created"] == ["texas"] and changes["follow_up"] == ["assignments", "pages"]
    assert changes["file"] == str(setup.ws.plan_artifact.edited)


def test_a_mistake_apply_shows_the_sentences_before_and_after(setup: ToolSetup) -> None:
    sid = setup.ids()["mistake"]
    setup.decide("accept", sid)
    setup.apply(sid)
    assert setup.wait()["outcomes"][0]["status"] == "applied"
    assert FIXED in setup.ws.effective_page("agencies")["body_md"]
    changes = setup.get(f"/suggestions/{sid}")["changes"]
    page = changes["pages"][0]
    assert page["page_id"] == "agencies" and page["changed"] and not page["rewritten_since"]
    assert page["sentences"] == [{"before": [sentence_a("g-00000004").split("[^")[0]],
                                  "after": [FIXED.split("[^")[0]]}]
    assert changes["backup"] is None  # the pages had no edited copy before: "before" = original


def test_after_a_later_write_the_change_is_read_from_the_backups(setup: ToolSetup) -> None:
    sid = setup.ids()["mistake"]
    setup.decide("accept", sid)
    setup.apply(sid)
    setup.wait()
    later = setup.ws.pages(edited=True)
    later["pages"]["agencies"]["body_md"] = "כתוב מחדש לגמרי."  # saved as every writer does
    guarded_write(setup.ws.artifact.edited, later, backup=True)
    page = setup.get(f"/suggestions/{sid}")["changes"]["pages"][0]
    assert page["rewritten_since"] is True
    assert page["sentences"][0]["after"] == [FIXED.split("[^")[0]]


def test_a_contradiction_apply_saves_the_links_and_names_them(setup: ToolSetup) -> None:
    respond = corrector("פתיח קצר", "פתיח עם שני הצדדים")
    setup = ToolSetup(setup.ws.artifact.original.parent / "c", respond=respond)
    sid = setup.ids()["contradiction"]
    setup.decide("accept", sid)
    setup.apply(sid)
    outcome = setup.wait()["outcomes"][0]
    assert outcome["status"] == "applied" and outcome["paid"] is True
    assert setup.ws.linker.links == [("g-00000000", "g-00000002", True)]
    changes = setup.get(f"/suggestions/{sid}")["changes"]
    assert changes["links"][0]["a"]["group_id"] == "g-00000000" and changes["links"][0]["b"]["claim_text"]
    assert {page["page_id"] for page in changes["pages"]} == {"agencies", "clinics"}
    assert changes["groups_file"] == str(setup.ws.linker.edited)


def test_a_refused_apply_reports_why_and_writes_nothing(setup: ToolSetup) -> None:
    sid = setup.ids()["merge"]
    setup.decide("accept", sid)
    setup.ws.plan_artifact.edited.write_text(json.dumps(delete(setup.ws.plan(), "costs").plan),
                                             encoding="utf-8")
    before = setup.ws.snapshot()
    setup.apply(sid)
    outcome = setup.wait()["outcomes"][0]
    assert outcome["status"] == "refused" and outcome["details"]
    assert outcome["message"].startswith(REVIEW_CONFIG["messages"]["plan_refused"].split("{")[0])
    assert setup.ws.snapshot() == before
    assert setup.item(sid)["status"] == "accepted"


def test_a_failing_provider_writes_nothing_and_the_suggestion_stays_accepted(tmp_path: Path) -> None:
    setup = ToolSetup(tmp_path, respond=failing_provider)
    sid = setup.ids()["mistake"]
    setup.decide("accept", sid)
    before = setup.ws.snapshot()
    setup.apply(sid)
    outcome = setup.wait()["outcomes"][0]
    assert outcome["status"] == "failed" and "credit balance" in outcome["message"]
    assert setup.ws.snapshot() == before
    assert setup.item(sid)["status"] == "accepted"


def test_apply_all_accepted_queues_them_in_order(setup: ToolSetup) -> None:
    ids = setup.ids()
    setup.decide("accept", ids["add_label"], ids["mistake"], ids["split"])
    answer = setup.post("/applies", {"all": True}, status=202)
    assert answer["queued"] == [ids["mistake"], ids["split"], ids["add_label"]]  # file order
    outcomes = setup.wait()["outcomes"]
    assert [o["id"] for o in outcomes] == [ids["add_label"], ids["split"], ids["mistake"]]
    assert {o["status"] for o in outcomes} == {"applied"}
    assert setup.post("/applies", {"all": True}, status=409)["detail"] == \
        REVIEW_CONFIG["ui"]["messages"]["none_accepted"]


def test_apply_refusals_before_queueing(setup: ToolSetup) -> None:
    ids = setup.ids()
    detail = setup.apply(ids["merge"], status=409)["detail"]  # still pending
    assert ids["merge"] in detail
    setup.apply("sug-00000000", status=404)
    setup.post("/applies", {}, status=422)
    assert setup.get("/applies")["outcomes"] == []


def test_the_tool_keeps_working_while_an_apply_runs(tmp_path: Path) -> None:
    gate = Gate(corrector(sentence_a("g-00000004"), FIXED))
    setup = ToolSetup(tmp_path, respond=gate)
    ids = setup.ids()
    setup.decide("accept", ids["mistake"], ids["add_label"])
    setup.apply(ids["mistake"], ids["add_label"])
    assert gate.started.wait(10)
    overview = setup.get("/suggestions")  # reading is never blocked
    assert overview["applies"]["running"]["id"] == ids["mistake"]
    assert [entry["id"] for entry in overview["applies"]["queue"]] == [ids["add_label"]]
    busy = {item["id"] for item in overview["items"] if item["busy"]}
    assert busy == {ids["mistake"], ids["add_label"]}
    # Deciding on another suggestion works; on the busy ones it is refused.
    setup.decide("reject", ids["merge"])
    assert ids["mistake"] in setup.decide("reject", ids["mistake"], status=409)["detail"]
    proposal = setup.item(ids["mistake"])["proposal"]
    setup.post(f"/suggestions/{ids['mistake']}/edit", {"proposal": proposal}, status=409)
    assert setup.apply(ids["mistake"], status=409)["detail"]
    # A queued one can be taken out again; the running one finishes.
    assert setup.post("/applies/withdraw", {"ids": [ids["add_label"]]})["queue"] == []
    gate.release.set()
    state = setup.wait()
    assert [o["id"] for o in state["outcomes"]] == [ids["mistake"]]
    assert setup.item(ids["add_label"])["status"] == "accepted"
    assert setup.item(ids["merge"])["status"] == "rejected"


def test_writes_get_423_while_a_step_rewrites_a_file_the_tool_writes(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_STEP_HOLD", "60")

    def runner(graph: ArtifactGraph) -> JobRunner:
        steps = StepRegistry((StepSpec("pages", "Pages step", "tests.fake_step",
                                       incremental_safe=True),), graph)
        return JobRunner(steps, EventBus(keepalive_seconds=0.2, queue_size=16),
                         cwd=PROJECT_ROOT, data_dir=tmp_path, settings=JOB_SETTINGS)

    setup = ToolSetup(tmp_path, jobs=runner)
    sid = setup.ids()["merge"]
    try:
        setup.runner.run_step("step_7")
        detail = setup.decide("accept", sid, status=423)["detail"]
        assert "Pages step" in detail
        setup.get("/suggestions")  # reading still works
    finally:
        setup.runner.shutdown()
    assert setup.item(sid)["status"] == "pending"


def test_snapshot_after_and_sentence_changes(tmp_path: Path) -> None:
    edited = tmp_path / "pages_edited.json"
    edited.write_text("{}", encoding="utf-8")
    first, second = BACKUPS_DIR / "pages_edited_20990101_000000.json", \
        BACKUPS_DIR / "pages_edited_20990101_000000_1.json"
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    for path in (first, second):
        path.write_text("{}", encoding="utf-8")
    try:
        assert snapshot_after(edited, first, BACKUPS_DIR, "") == second
        assert snapshot_after(edited, second, BACKUPS_DIR, "") == edited
        assert snapshot_after(edited, None, BACKUPS_DIR, "2098-12-31T00:00:00Z") == first
    finally:
        first.unlink()
        second.unlink()
    assert sentence_changes("אחת. שתיים. שלוש.", "אחת. שתיים ועוד. שלוש.") == [
        {"before": ["שתיים."], "after": ["שתיים ועוד."]}]
