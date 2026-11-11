"""Points stored with the page (C9) and updates that build on them (task 27b), fake model."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from step_7_write_pages.inputs import PAGES_CONFIG
from tests.pages_fixtures import FakeWriter, PagesWorkspace, main_ids_in, points_of
from tests.plan_fixtures import make_groups
from tests.review_fixtures import current_body
from utils.json_io import write_json_file
from utils.llm_client import _flatten

PAGES = ["agencies", "costs", "clinics", "hospital"]
NEW = "g-0000000c"
EIGHT = "משפט על הקבוצה g-00000008.[^g-00000008]"


@pytest.fixture()
def ws(tmp_path: Path) -> PagesWorkspace:
    workspace = PagesWorkspace(tmp_path)
    workspace.run()
    return workspace


def _add_new_group(ws: PagesWorkspace) -> None:
    """Group 12 (no dispute) joins ``agencies``."""
    groups = ws.groups()
    groups.append(make_groups(13)[12])
    ws.write_groups(groups)
    payload = ws.assignments()
    payload["assignments"][NEW] = {"pages": ["agencies"], "labels": [], "p": {}, "label_p": {}}
    ws.write_assignments(payload)


def _answer(user: Any, **fields: Any) -> dict[str, Any]:
    return {**points_of(main_ids_in(user)), "no_change": False, "body": "", "summary": "ת",
            "questions": ["ש?"], "related": [], **fields}


def test_the_stored_points_are_the_repaired_answer(tmp_path: Path) -> None:
    def respond(system: Any, user: Any, task: str) -> dict[str, Any]:
        answer = FakeWriter().complete_json(system, user, task)
        answer["points"][0]["groups"] += ["g-deadbeef", "g-00000004"]  # unknown; a mention on costs
        answer["left_out"] = [{"group": "g-deadbeef", "reason": "trivial"}]
        return answer

    ws = PagesWorkspace(tmp_path)
    ws.run(FakeWriter(respond=respond))
    payload = ws.pages()
    costs = payload["pages"]["costs"]
    assert [p["groups"] for p in costs["points"]] == [["g-00000001"], ["g-00000005"], ["g-00000009"]]
    assert costs["left_out"] == []
    report = payload["metadata"]["pages"]["costs"]["report"]
    assert report["calls"] == 2 and any("not main groups" in p for p in report["first_problems"])
    assert any("g-deadbeef" in r and "g-00000004" in r for r in report["repairs"])
    assert payload["metadata"]["summary"]["unaccounted"] == 0
    assert set(payload["metadata"]["summary"]["left_out_share"]) == set(PAGES)


def test_a_new_group_that_repeats_a_point_joins_it_and_leaves_the_text_alone(
        ws: PagesWorkspace) -> None:
    before = ws.pages()["pages"]["agencies"]
    _add_new_group(ws)

    def join(system: Any, user: Any, task: str) -> dict[str, Any]:
        answer = _answer(user, no_change=True, summary="", questions=[])
        answer["points"] = [p for p in answer["points"] if p["groups"] != [NEW]]
        answer["points"][0]["groups"].append(NEW)
        return answer

    update = FakeWriter("fake-update", respond=join)
    result = ws.run(FakeWriter(), update)
    user = _flatten(update.calls[0]["user"])
    assert update.tasks() == ["update:agencies"] and result["no_change"] == 1
    assert "<current_points>\n- fact | נקודה g-00000000 | g-00000000\n" in user
    assert f"new: {NEW} |" in user
    after = ws.pages()["pages"]["agencies"]
    assert after["body_md"] == before["body_md"]
    assert after["front_matter"]["generated"] == before["front_matter"]["generated"]
    assert after["points"][0]["groups"] == ["g-00000000", NEW] and len(after["points"]) == 3
    assert after["input_fingerprint"] != before["input_fingerprint"]
    third = FakeWriter("fake-update")
    assert ws.run(FakeWriter(), third)["written"] is False and third.calls == []


def test_a_new_group_that_adds_something_becomes_a_point_worked_in(ws: PagesWorkspace) -> None:
    before = ws.pages()["pages"]["agencies"]["body_md"]
    _add_new_group(ws)

    def add(system: Any, user: Any, task: str) -> dict[str, Any]:
        body = current_body(user).replace("\nטקסט עם קישור", f" אזהרה חדשה.[^{NEW}]\nטקסט עם קישור")
        answer = _answer(user, body=body)
        answer["points"][-1]["kind"] = "warning"
        return answer

    result = ws.run(FakeWriter(), FakeWriter("fake-update", respond=add))
    page = ws.pages()["pages"]["agencies"]
    assert result["update"] == 1 and f"אזהרה חדשה.[^{NEW}]" in page["body_md"]
    assert page["body_md"].replace(f" אזהרה חדשה.[^{NEW}]", "") == before
    assert page["points"][-1] == {"kind": "warning", "statement": f"נקודה {NEW}", "groups": [NEW]}


def test_a_removed_group_leaves_its_point_and_the_emptied_point_goes(ws: PagesWorkspace) -> None:
    payload = ws.assignments()
    payload["assignments"]["g-00000008"]["pages"] = []
    ws.write_assignments(payload)

    def drop(system: Any, user: Any, task: str) -> dict[str, Any]:
        return _answer(user, body=current_body(user).replace(f" {EIGHT}", ""))

    update = FakeWriter("fake-update", respond=drop)
    ws.run(FakeWriter(), update)
    user = _flatten(update.calls[0]["user"])
    points_block = user.split("<current_points>")[1].split("</current_points>")[0]
    assert "g-00000008" not in points_block and "removed: g-00000008" in user
    assert ("point gone (its last group left the page): fact | נקודה g-00000008 | g-00000008"
            in user)
    page = ws.pages()["pages"]["agencies"]
    assert [p["groups"] for p in page["points"]] == [["g-00000000"], ["g-00000004"]]
    assert EIGHT not in page["body_md"]
    report = ws.pages()["metadata"]["pages"]["agencies"]["report"]
    assert report["final_problems"] == [] and report["uncarried"] == 0


def test_a_page_stored_without_points_gets_them_listed_at_its_update(ws: PagesWorkspace) -> None:
    payload = ws.pages()
    for page in payload["pages"].values():  # as step 7 stored pages before task 27b
        del page["points"], page["left_out"]
    write_json_file(payload, ws.artifact.original)
    groups = ws.groups()
    groups[8]["supporter_count"] += 1
    ws.write_groups(groups)
    update = FakeWriter("fake-update", respond=lambda s, u, t: _answer(u, body=current_body(u)))
    ws.run(FakeWriter(), update)
    assert "none stored: list the points" in _flatten(update.calls[0]["user"])
    assert len(ws.pages()["pages"]["agencies"]["points"]) == 3


def test_the_points_never_change_a_fingerprint(tmp_path: Path) -> None:
    def merged(system: Any, user: Any, task: str) -> dict[str, Any]:
        answer = FakeWriter().complete_json(system, user, task)
        answer["points"] = [{**p, "kind": "common_view"} for p in reversed(answer["points"])]
        return answer

    first, second = PagesWorkspace(tmp_path / "a"), PagesWorkspace(tmp_path / "b")
    first.run()
    second.run(FakeWriter(respond=merged))
    a, b = first.pages(), second.pages()
    assert a["metadata"]["inputs"] == b["metadata"]["inputs"]
    assert all(a["pages"][pid]["points"] != b["pages"][pid]["points"]
               for pid in ("agencies", "costs"))
    assert {pid: p["input_fingerprint"] for pid, p in a["pages"].items()} == {
        pid: p["input_fingerprint"] for pid, p in b["pages"].items()}


def test_the_prompt_version_bump_writes_every_page_anew(ws: PagesWorkspace,
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    llm, update = FakeWriter(), FakeWriter("fake-update")
    assert ws.run(llm, update)["written"] is False and llm.calls == []
    monkeypatch.setitem(PAGES_CONFIG, "prompt_version", PAGES_CONFIG["prompt_version"] + 1)
    result = ws.run(llm, update)
    assert sorted(llm.tasks()) == sorted(f"write:{pid}" for pid in PAGES) and update.calls == []
    assert result["write"] == 4
