"""The Tagged members page (``review_tool.tools.tagged``) and the update's notice of waiting
decisions (task 67). Synthetic export, conversations and claims; made-up ids and names."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from review_tool.artifacts import ArtifactNode
from review_tool.steps import STEP_SPECS
from review_tool.tools.tagged.tool import TaggedTool
from review_tool.toolset import TOOL_SPECS
from update_run.driver import COMPLETED, UPDATE_CONFIG, Options, UpdateDriver
from tests.review_tool_support import BASE_URL, build_app, build_graph, locked_elsewhere, write_file
from tests.update_support import PAID_STEPS, World, newer_chat
from utils.paths import BACKUPS_DIR, STEP_3

ALICE, BOB, CAROL, DAN, ERIN = (f"97250000000{i}@s.whatsapp.net" for i in range(1, 6))
NAME = "Clinic Alpha"


def entry(wa_id: str, minute: int, sender: str, text: str, tags: dict[str, str] | None = None) -> dict:
    return {"id": wa_id, "date": "05/01/2024", "time": f"10:{minute:02d}:00",
            "sender": {"user_name": sender, "phone": None}, "text": text, "reactions": [],
            "quote": None, "mentions": [{"tag": t, "member": m} for t, m in (tags or {}).items()]}


EXPORT = [
    entry("W1", 1, ALICE, "ask @41", {"41": DAN}),
    entry("W2", 2, BOB, "yes @41", {"41": DAN}),
    entry("W3", 3, DAN, "thanks"),
    entry("W4", 4, CAROL, "@42 is good", {"42": DAN}),
    entry("W5", 5, ALICE, "and @51", {"51": ERIN}),
]


def claim(claim_id: str, thread_id: str, entities: list[str], supporting: list[str]) -> dict:
    return {"claim_id": claim_id, "thread_id": thread_id, "claim_text": "x", "entities": entities,
            "supporting_message_ids": supporting, "opposing_message_ids": []}


class Setup:
    def __init__(self, root: Path) -> None:
        self.graph = build_graph(root)
        self.table = root / "tagged_members.json"
        self.graph.add(ArtifactNode("tagged_members", "Tagged members", self.table))
        write_file(root / "export.json", EXPORT)  # type: ignore[arg-type]  # a list is fine
        write_file(root / "threads.json", {"threads": [
            {"thread_id": "t-W1", "start_time": "2024-01-05T10:01:00", "message_ids": ["W1", "W2", "W3"]},
            {"thread_id": "t-W4", "start_time": "2024-01-05T10:04:00", "message_ids": ["W4", "W5"]},
        ]})
        write_file(root / "claims.json", {"claims": [
            claim("c1", "t-W1", ["Alpha"], ["W2"]),
            claim("c2", "t-W1", ["Israel"], ["W3"]),
            claim("c3", "t-W4", ["Israel"], ["W5"]),
        ]})
        tool = TaggedTool(self.graph, lock_timeout=0.2)
        self.client = TestClient(build_app(root, specs=(tool.spec,), graph=self.graph), base_url=BASE_URL)

    def get(self, path: str, **params: Any) -> Any:
        response = self.client.get(f"/api/tagged{path}", params=params)
        assert response.status_code == 200, response.text
        return response.json()

    def post(self, path: str, body: dict | None = None, status: int = 200) -> Any:
        response = self.client.post(f"/api/tagged{path}", json=body or {})
        assert response.status_code == status, response.text
        return response.json()


@pytest.fixture
def tagged(tmp_path: Path) -> Setup:
    return Setup(tmp_path)


def test_the_page_lists_the_tagged_members_most_tagged_first_waiting_by_default(tagged: Setup) -> None:
    summary = tagged.get("/summary")
    assert summary["min_taggers"] == 3 and summary["table_exists"] is False
    assert summary["counts"] == {"all": 2, "waiting": 1, "undecided": 2, "provider": 0, "not_provider": 0}
    assert [row["member"] for row in tagged.get("/members")["items"]] == [DAN]
    rows = tagged.get("/members", show="all")["items"]
    assert [row["member"] for row in rows] == [DAN, ERIN]
    assert {k: rows[0][k] for k in ("tags", "tag_count", "tagger_count", "written", "thread_count", "status")} == {
        "tags": ["41", "42"], "tag_count": 3, "tagger_count": 3, "written": 1, "thread_count": 2,
        "status": "undecided"}
    assert (rows[0]["first"], rows[0]["last"]) == ("2024-01-05T10:01:00", "2024-01-05T10:04:00")
    assert tagged.client.get("/api/tagged/members", params={"show": "nope"}).status_code == 422


def test_a_member_shows_the_tagging_messages_their_conversations_and_suggested_names(tagged: Setup) -> None:
    detail = tagged.get(f"/members/{DAN}")
    assert [(m["id"], m["thread_id"], m["sender"]) for m in detail["messages"]] == [
        ("W1", "t-W1", ALICE), ("W2", "t-W1", BOB), ("W4", "t-W4", CAROL)]
    assert [(t["thread_id"], t["tag_count"]) for t in detail["threads"]] == [("t-W1", 2), ("t-W4", 1)]
    # A name of a claim a tagging message supports comes first, then the most frequent.
    assert detail["suggestions"] == [
        {"name": "Alpha", "claims": 1, "near": 1},
        {"name": "Israel", "claims": 2, "near": 0},
    ]
    assert tagged.client.get("/api/tagged/members/nobody@s.whatsapp.net").status_code == 404


def test_decisions_are_saved_read_back_and_cleared_with_a_backup(tagged: Setup) -> None:
    backups = len(list(BACKUPS_DIR.glob("tagged_members_*"))) if BACKUPS_DIR.is_dir() else 0
    tagged.post(f"/members/{DAN}/decision", {"provider": True, "public_name": "  "}, status=409)
    answer = tagged.post(f"/members/{DAN}/decision", {"provider": True, "public_name": f" {NAME}  "})
    assert (answer["member"]["status"], answer["member"]["public_name"]) == ("provider", NAME)
    assert answer["summary"]["counts"]["provider"] == 1 and answer["summary"]["counts"]["waiting"] == 0
    tagged.post(f"/members/{ERIN}/decision", {"provider": False})
    table = json.loads(tagged.table.read_text("utf-8"))
    assert list(table) == ["members"]
    assert {member: {k: v for k, v in d.items() if k != "decided_at"} for member, d in table["members"].items()} == {
        DAN: {"provider": True, "public_name": NAME}, ERIN: {"provider": False}}
    assert all(d["decided_at"] for d in table["members"].values())
    assert len(list(BACKUPS_DIR.glob("tagged_members_*"))) == backups + 1  # the second save
    assert tagged.get(f"/members/{DAN}")["public_name"] == NAME
    assert [row["member"] for row in tagged.get("/members", show="decided")["items"]] == [DAN, ERIN]

    assert tagged.post(f"/members/{ERIN}/clear")["member"]["status"] == "undecided"
    tagged.post(f"/members/{ERIN}/clear", status=409)
    tagged.post("/members/nobody@s.whatsapp.net/decision", {"provider": False}, status=404)
    assert ERIN not in json.loads(tagged.table.read_text("utf-8"))["members"]


def test_a_save_waits_for_the_tables_lock(tagged: Setup) -> None:
    tagged.post(f"/members/{DAN}/decision", {"provider": False})
    with locked_elsewhere(tagged.table):
        tagged.post(f"/members/{DAN}/clear", status=423)
    assert DAN in json.loads(tagged.table.read_text("utf-8"))["members"]


def test_the_page_sits_next_to_privacy_and_step_3_names_it_as_the_review_before_the_step() -> None:
    names = [spec.name for spec in TOOL_SPECS]
    assert names.index("tagged") + 1 == names.index("pii")
    step_3 = next(spec for spec in STEP_SPECS if spec.produces == "claims")
    assert (step_3.review_before, step_3.review_tool) == ("tagged", "pii")
    assert all(spec.review_before is None for spec in STEP_SPECS if spec is not step_3)


@pytest.mark.parametrize("waiting", [4, 0])
def test_the_update_says_how_many_decisions_wait_and_goes_on(tmp_path: Path, waiting: int) -> None:
    world = World(tmp_path)
    driver = UpdateDriver(
        world.steps, world.graph, estimator=world.estimate, server_check=lambda: None, paths=world.paths,
        runners={step.key: world.runner(step) for step in world.steps}, waiting={"tagged": lambda: waiting},
        logger=logging.getLogger("test_update"),
    )
    outcome = driver.run(world.export(newer_chat()), Options(pay=PAID_STEPS, no_stops=True))
    assert outcome.status == COMPLETED
    [report_file] = world.paths.updates_dir.glob("*_report.json")
    report = json.loads(report_file.read_text("utf-8"))
    notices = [event for event in report["events"] if event["kind"] == "waiting"]
    if not waiting:
        assert notices == []
        return
    assert [(event["step"], event["count"]) for event in notices] == [(STEP_3, waiting)]
    assert notices[0]["message"] == UPDATE_CONFIG["waiting_hints"]["tagged"].format(count=waiting)
