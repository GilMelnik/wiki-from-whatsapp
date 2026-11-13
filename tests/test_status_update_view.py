"""The status tool's view of an update (``GET /api/status/update``), on states the real driver
wrote: a synthetic pipeline with fake steps and estimates (``tests.update_support``)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from review_tool.status_tool import StatusTool
from review_tool.update_status import UpdateStatus
from step_10_update.driver import StepFailed
from tests.review_tool_support import BASE_URL, build_app
from tests.update_support import PAID_STEPS, World, newer_chat
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


def view(world: World) -> dict[str, Any]:
    update = UpdateStatus(world.paths.state, world.paths.lease, world.steps, world.graph)
    tool = StatusTool(world.graph, world.root, update)
    client = TestClient(build_app(world.root, specs=(tool.spec,), graph=world.graph), base_url=BASE_URL)
    response = client.get("/api/status/update")
    assert response.status_code == 200, response.text
    return response.json()


def statuses(answer: dict[str, Any]) -> dict[str, str]:
    return {step["key"]: step["status"] for step in answer["steps"]}


def test_no_update_yet(world: World) -> None:
    assert view(world) == {"state": "none"}


def test_stopped_before_a_paid_step_with_its_estimate(world: World) -> None:
    world.run(world.export(newer_chat()))
    answer = view(world)
    assert answer["state"] == "in_progress" and answer["export"].endswith("_chat_android.json")
    assert answer["counts"] == {"new": 2, "changed": 0, "missing": 0}
    assert [statuses(answer)[key] for key in UPDATE_ORDER] == [
        "done", "done", "stopped", "waiting", "waiting", "waiting", "waiting", "waiting", "waiting",
        "waiting",
    ]
    assert answer["stopped"]["kind"] == "paid" and answer["stopped"]["review_tool"] is None
    assert f"--pay {STEP_3}" in answer["stopped"]["message"]
    assert answer["estimate"] == {
        "step": STEP_3, "paid_calls": 2, "usd": 0.5, "what": "2 calls", "counted": "test figures"
    }
    assert answer["command"] == f"python -m step_10_update.run --pay {STEP_3}"
    assert answer["input_backup"].endswith("_previous_chat_android.json")
    paid = {step["key"] for step in answer["steps"] if step["paid"]}
    assert paid == PAID_STEPS


def test_stopped_for_a_review_with_the_tool_to_open(world: World) -> None:
    world.run(world.export(newer_chat()), pay=frozenset({STEP_3}))
    answer = view(world)
    assert [statuses(answer)[key] for key in UPDATE_ORDER[:5]] == ["done", "done", "review", "next", "waiting"]
    assert answer["stopped"]["kind"] == "review" and answer["stopped"]["review_tool"] == "pii"
    assert answer["estimate"] is None and answer["command"] == "python -m step_10_update.run"
    step_3 = next(step for step in answer["steps"] if step["key"] == STEP_3)
    assert step_3["runs"] == 1 and step_3["changed"] is True and step_3["review_tool"] == "pii"


def test_a_failed_step(world: World) -> None:
    world.failing = {STEP_4}
    with pytest.raises(StepFailed):
        world.run(world.export(newer_chat()), pay=PAID_STEPS, no_stops=True)
    answer = view(world)
    assert [statuses(answer)[key] for key in UPDATE_ORDER[2:6]] == ["done", "failed", "waiting", "waiting"]
    assert answer["failed"]["step"] == STEP_4 and answer["failed"]["error"].startswith("RuntimeError")
    assert answer["failed"]["title"] == "Step 4 · Find entities"
    assert answer["command"] == "python -m step_10_update.run"  # the next run retries the step


def test_a_running_step_comes_from_the_lease(world: World) -> None:
    world.run(world.export(newer_chat()))
    lease = StepLease(world.paths.lease)
    with lease.hold():
        lease.announce(STEP_3, "claims", "Step 3 · Extract claims")
        answer = view(world)
    assert statuses(answer)[STEP_3] == "running" and answer["running"]["step"] == STEP_3
    assert answer["stopped"] is None and answer["command"] is None
    assert view(world)["stopped"]["kind"] == "paid"  # the lease is gone: the stop shows again


def test_a_completed_update(world: World) -> None:
    world.run(world.export(newer_chat()), pay=PAID_STEPS, no_stops=True)
    answer = view(world)
    assert answer["state"] == "completed" and answer["completed_at"]
    assert set(statuses(answer).values()) == {"done"}
    assert answer["stopped"] is None and answer["command"] is None


def test_an_unreadable_state_file_says_so(world: World) -> None:
    world.paths.state.parent.mkdir(parents=True, exist_ok=True)
    world.paths.state.write_text("{not json", encoding="utf-8")
    answer = view(world)
    assert answer["state"] == "unreadable" and "not valid JSON" in answer["problem"]
