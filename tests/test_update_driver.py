"""Step 10's driver on a synthetic pipeline (fake steps, estimates and server; no API call):
C13 files and state, review stops, paid stops, resume, ``--plan``, failures, refusals, the
re-run after applied suggestions, and a second identical export."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import pipeline
from review_tool.steps import SERVED_STEPS, Step
from review_tool.toolset import TOOL_SPECS
from step_10_update.driver import (
    COMPLETED,
    NOTHING,
    REFUSED,
    STOPPED,
    UPDATE_CONFIG,
    Options,
    StepFailed,
)
from tests.update_support import PAID_STEPS, World, message, newer_chat, old_chat
from utils.paths import (
    STEP_1,
    STEP_2,
    STEP_3,
    STEP_4,
    STEP_5,
    STEP_6_ASSIGN,
    STEP_7,
    STEP_8,
    STEP_8_PROOFREAD,
    STEP_9,
)
from utils.step_lease import StepLease

UPDATE_ORDER = [STEP_1, STEP_2, STEP_3, STEP_4, STEP_5, STEP_6_ASSIGN, STEP_7, STEP_8,
                STEP_8_PROOFREAD, STEP_9]


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path)


def run_to_the_end(world: World, export: Path | None = None, **options: object) -> list[str]:
    """Run, then go on after every stop, until the update completes; the stops' steps."""
    stops = []
    outcome = world.run(export, **options)
    while outcome.status == STOPPED:
        stops.append(world.state()["update"]["stopped"]["step"])
        outcome = world.run(**options)
    assert outcome.status == COMPLETED, outcome.message
    return stops


def test_first_update_writes_the_c13_files_and_state(world: World) -> None:
    assert not world.paths.state.exists()
    export = world.export(newer_chat())
    outcome = world.run(export, pay=PAID_STEPS, no_stops=True)
    assert outcome.status == COMPLETED and world.calls == UPDATE_ORDER
    state = world.state()
    update = state["update"]
    stamp = update["stamp"]
    files = world.paths.files(stamp)
    assert files.export.read_bytes() == export.read_bytes()
    assert Path(update["export"]) == files.export and update["completed_at"]
    assert update["counts"] == {"new": 2, "changed": 0, "missing": 0}
    new_messages = json.loads(files.new_messages.read_text(encoding="utf-8"))
    assert [m["id"] for m in new_messages["new"]] == ["n1", "n2"]
    assert new_messages["missing_ids"] == [] and "PRIVATE" in new_messages["metadata"]["warning"]
    assert state["last_export"] == str(files.export)
    assert state["processed_message_count"] == 32
    assert state["last_datetime"] == "2026-03-02T10:01:00"
    assert json.loads(world.paths.input.read_text(encoding="utf-8")) == newer_chat()
    assert files.previous_input.read_bytes() == json.dumps(old_chat()).encode("utf-8")
    report = json.loads(files.report.read_text(encoding="utf-8"))
    assert report["export_sha1"] == update["export_sha1"] and report["git_commit"]
    assert "update" in report["config"]
    runs = [event["step"] for event in report["events"] if event["kind"] == "run"]
    assert runs == UPDATE_ORDER and report["events"][-1]["kind"] == "completed"
    assert files.log.is_file()


def test_an_export_already_in_the_updates_folder_keeps_its_stamp(world: World) -> None:
    export = world.paths.updates_dir / "2026_11_07_chat_android.json"
    world.write_json(export, newer_chat())
    world.run(export, pay=PAID_STEPS, no_stops=True)
    assert world.state()["update"]["stamp"] == "2026_11_07"
    assert sorted(p.name for p in world.paths.updates_dir.glob("*chat_android.json")) == [
        "2026_11_07_chat_android.json", "2026_11_07_previous_chat_android.json"]


def test_it_stops_at_every_review_point_and_goes_on_when_run_again(world: World) -> None:
    stops = run_to_the_end(world, world.export(newer_chat()), pay=PAID_STEPS)
    assert stops == [STEP_2, STEP_3, STEP_4, STEP_5, STEP_6_ASSIGN, STEP_8]
    assert world.calls == UPDATE_ORDER  # no step was redone after a stop


def test_a_review_point_whose_output_did_not_change_does_not_stop(world: World) -> None:
    world.write_json(world.node("entities").original, {"items": ["same"], "metadata": {}})
    world.unchanged = {STEP_4}
    stops = run_to_the_end(world, world.export(newer_chat()), pay=PAID_STEPS)
    assert STEP_4 not in stops


def test_through_runs_without_review_stops_up_to_the_step(world: World) -> None:
    outcome = world.run(world.export(newer_chat()), pay=PAID_STEPS, through=STEP_5)
    assert outcome.status == STOPPED and world.calls == UPDATE_ORDER[:5]
    assert world.state()["update"]["stopped"]["kind"] == "through"
    assert world.run(pay=PAID_STEPS, through=STEP_5).status == STOPPED
    assert world.calls == UPDATE_ORDER[:5]


def test_a_paid_step_stops_with_its_estimate_until_it_is_allowed(world: World) -> None:
    assert world.run(world.export(newer_chat())).status == STOPPED  # the review after step 2
    assert world.state()["update"]["stopped"]["kind"] == "review"
    outcome = world.run()
    assert outcome.status == STOPPED and world.calls == [STEP_1, STEP_2]
    assert "2 calls; about $0.50" in outcome.message
    assert f"--pay {STEP_3}" in outcome.message
    assert world.state()["update"]["stopped"]["kind"] == "paid"
    before = world.snapshot()
    again = world.run()
    assert again.status == STOPPED and world.calls == [STEP_1, STEP_2]
    changed = {path for path, entry in world.snapshot().items() if before.get(path) != entry}
    # Only the record of the stop changed: state, report and log; no step output, no input.
    assert all(Path(path).name == "state.json" or path.endswith(("_report.json", "_update.log"))
               for path in changed)
    assert world.run(pay=frozenset({STEP_3})).status == STOPPED  # now the review of step 3
    assert world.calls == [STEP_1, STEP_2, STEP_3]


def test_a_paid_step_with_nothing_to_pay_for_runs_without_the_flag(world: World) -> None:
    world.paid_calls = 0
    stops = run_to_the_end(world, world.export(newer_chat()), no_stops=True)
    assert stops == [] and world.calls == UPDATE_ORDER


def test_plan_says_what_would_happen_and_writes_nothing(world: World) -> None:
    export = world.export(newer_chat())
    before = world.snapshot()
    outcome = world.driver().plan(export, Options())
    assert outcome.status == NOTHING and "nothing was changed" in outcome.message
    assert world.snapshot() == before and world.calls == []
    world.run(export)  # stops before step 3
    before = world.snapshot()
    world.driver().plan(None, Options())
    assert world.snapshot() == before


def test_a_failing_first_step_puts_the_old_input_back_and_can_be_retried(world: World) -> None:
    world.failing = {STEP_1}
    with pytest.raises(StepFailed, match="retry"):
        world.run(world.export(newer_chat()), pay=PAID_STEPS, no_stops=True)
    assert json.loads(world.paths.input.read_text(encoding="utf-8")) == old_chat()
    update = world.state()["update"]
    assert update["failed"]["step"] == STEP_1 and update["input"]["replaced"] is False
    world.failing = set()
    assert world.run(pay=PAID_STEPS, no_stops=True).status == COMPLETED
    assert json.loads(world.paths.input.read_text(encoding="utf-8")) == newer_chat()


def test_a_failing_later_step_is_retried_alone(world: World) -> None:
    world.failing = {STEP_4}
    with pytest.raises(StepFailed):
        world.run(world.export(newer_chat()), pay=PAID_STEPS, no_stops=True)
    failed = world.state()["update"]["failed"]
    assert failed["step"] == STEP_4 and failed["error"].startswith("RuntimeError")
    world.failing = set()
    world.calls.clear()
    assert world.run(pay=PAID_STEPS, no_stops=True).status == COMPLETED
    assert world.calls == UPDATE_ORDER[3:] and world.state()["update"]["failed"] is None


def test_a_busy_review_server_or_another_update_refuses_to_start(world: World) -> None:
    world.busy = "the review server on port 8780 is running Step 2"
    outcome = world.run(world.export(newer_chat()), pay=PAID_STEPS)
    assert outcome.status == STOPPED and "running Step 2" in outcome.message
    assert world.calls == [] and world.state()["update"]["stopped"]["kind"] == "busy"
    world.busy = None
    with StepLease(world.paths.lease).hold():
        assert world.run(pay=PAID_STEPS).status == REFUSED
    assert world.calls == []
    assert world.run(pay=PAID_STEPS).status == STOPPED and world.calls[0] == STEP_1


def test_the_lease_names_the_running_step(world: World) -> None:
    seen = {}
    plain = world.runner(world.steps[1])

    def classify() -> dict:
        seen.update(StepLease(world.paths.lease).current() or {})
        return plain()

    driver = world.driver()
    driver.runners = {**driver.runners, STEP_2: classify}
    driver.run(world.export(newer_chat()), Options())
    assert seen["step"] == STEP_2 and seen["produces"] == "classified"
    assert StepLease(world.paths.lease).current() is None


def test_after_applied_suggestions_6b_and_7_run_again_before_the_site(world: World) -> None:
    world.run(world.export(newer_chat()), pay=PAID_STEPS, through=STEP_8)
    assert world.calls == UPDATE_ORDER[:-2]
    world.write_json(world.node("plan").edited, {"pages": {"new": {}}, "tree": [], "labels": {}})
    world.calls.clear()
    stops = run_to_the_end(world, pay=PAID_STEPS)
    # step 8 is not asked again; proofreading reads the pages step 7 rewrote
    assert world.calls == [STEP_6_ASSIGN, STEP_7, STEP_8_PROOFREAD, STEP_9]
    assert stops == [STEP_6_ASSIGN]  # its output changed: proposals to look at


def test_a_second_identical_export_is_a_no_op(world: World) -> None:
    export = world.export(newer_chat())
    world.run(export, pay=PAID_STEPS, no_stops=True)
    before = world.snapshot()
    calls = list(world.calls)
    again = world.run(world.export(newer_chat(), name="same_again.json"), pay=PAID_STEPS)
    assert again.status == NOTHING and world.calls == calls
    after = {p: e for p, e in world.snapshot().items() if "same_again" not in p}
    assert after == before


def test_refusals_change_nothing(world: World) -> None:
    unrelated = world.export([message("x1", 0, day=1)], name="other.json")
    before = world.snapshot()
    outcome = world.run(unrelated)
    assert outcome.status == REFUSED and "Nothing was changed" in outcome.message
    assert world.snapshot() == before and not world.paths.state.exists()
    world.run(world.export(newer_chat()))  # an update in progress (stopped before step 3)
    other = world.export([*newer_chat(), message("n3", 2, day=2)], name="later.json")
    outcome = world.run(other)
    assert outcome.status == REFUSED and "still in progress" in outcome.message


def test_missing_messages_are_kept_unless_dropped(world: World) -> None:
    partial = world.export([*old_chat()[5:], message("n1", 0, day=2)])
    world.run(partial, pay=PAID_STEPS, no_stops=True)
    assert world.state()["update"]["counts"] == {"new": 1, "changed": 0, "missing": 5}
    kept = json.loads(world.paths.input.read_text(encoding="utf-8"))
    assert [m["id"] for m in kept][:5] == ["m0", "m1", "m2", "m3", "m4"] and len(kept) == 31
    assert world.state()["processed_message_count"] == 31



def test_the_pipeline_walks_the_same_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[tuple[str, dict]] = []

    def fake_run_function(step: Step) -> object:
        return lambda **options: called.append((step.key, options)) or {"written": False}

    monkeypatch.setattr(Step, "run_function", fake_run_function)
    pipeline.run(use_batch=True)
    # every step but the proofreading, which waits for the owner's decisions on the suggestions
    assert [key for key, _ in called] == [step.key for step in SERVED_STEPS.steps
                                          if step.key != STEP_8_PROOFREAD]
    assert dict(called)[STEP_3] == {"use_batch": True} and dict(called)[STEP_4] == {}


def test_a_failing_estimate_is_recorded_like_a_failing_step(world: World) -> None:
    def broken(step: Step) -> None:
        raise ValueError("no plan yet")

    driver = world.driver()
    driver.estimator = broken
    with pytest.raises(StepFailed, match="no plan yet"):
        driver.run(world.export(newer_chat()), Options(no_stops=True))
    assert world.calls == [STEP_1, STEP_2]
    assert world.state()["update"]["failed"]["step"] == STEP_3


def test_through_the_plan_step_is_refused(world: World) -> None:
    outcome = world.run(world.export(newer_chat()), through="step_6_plan")
    assert outcome.status == REFUSED and world.calls == [] and not world.paths.state.exists()


def test_every_review_point_names_a_served_tool_with_a_hint() -> None:
    tools = {step.spec.review_tool for step in SERVED_STEPS.steps} - {None}
    assert tools == {"threads", "pii", "entities", "groups", "plan", "suggestions"}
    assert tools <= {spec.name for spec in TOOL_SPECS} and tools <= set(UPDATE_CONFIG["review_hints"])
