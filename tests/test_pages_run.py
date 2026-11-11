"""Step 7 end to end with a fake model: C9 output, incremental re-runs (C14), registry rows."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from review_tool.artifacts import GRAPH
from review_tool.steps import SERVED_STEPS
from step_6_plan import operations
from step_7_write_pages.checks import cited_ids
from step_9_site.content import Site
from tests.pages_fixtures import FakeWriter, PagesWorkspace
from utils.llm_client import _flatten
from utils.okf import validate_front_matter

PAGES = ["agencies", "costs", "clinics", "hospital"]


@pytest.fixture()
def ws(tmp_path: Path) -> PagesWorkspace:
    workspace = PagesWorkspace(tmp_path)
    workspace.run()
    return workspace


def _bump(ws: PagesWorkspace, *indexes: int) -> None:
    groups = ws.groups()
    for index in indexes:
        groups[index]["supporter_count"] += 1
    ws.write_groups(groups)


def _edit(ws: PagesWorkspace, page_id: str, text: str) -> None:
    payload = ws.pages()
    payload["pages"][page_id]["body_md"] = payload["pages"][page_id]["body_md"].replace(
        "פתיח קצר על הדף.", f"פתיח קצר על הדף. {text}", 1)
    ws.write_edited(payload)


def _make_newer(ws: PagesWorkspace) -> None:
    """The output older than an input (as after an upstream run that changed nothing)."""
    stamp = ws.artifact.original.stat().st_mtime_ns + 10**9
    os.utime(ws.groups_path, ns=(stamp, stamp))


def test_first_run_writes_c9_pages_that_step_9_accepts(ws: PagesWorkspace) -> None:
    payload = ws.pages()
    assert list(payload["pages"]) == PAGES
    for page_id, page in payload["pages"].items():
        assert set(page) == {"front_matter", "body_md", "input_fingerprint"}
        validate_front_matter(page["front_matter"])
        assert not page["body_md"].startswith(("---", "# "))
        cited = cited_ids(page["body_md"])
        assert cited and [s["id"] for s in page["front_matter"]["sources"]] == cited
        assert payload["metadata"]["inputs"][page_id] == page["input_fingerprint"]
        assert "report" in payload["metadata"]["pages"][page_id]
    assert not ws.artifact.edited.exists()  # reconcile creates no edited copy
    site = Site(ws.plan(), payload["pages"], ws.groups())
    assert not [i for i in site.issues if i["issue"].startswith("broken")]
    assert not [i for i in site.issues if i["issue"].endswith("_reference")]
    assert payload["metadata"]["run"]["write"] == 4
    assert all("]:" not in page["body_md"] for page in payload["pages"].values())


def test_participant_counts_and_baked_definitions_change_no_page(ws: PagesWorkspace) -> None:
    payload = ws.pages()
    for page in payload["pages"].values():  # pages as an older step 7 stored them
        page["body_md"] += "".join(f"\n[^{gid}]: טענה (נאמר במאי 2024)" for gid in cited_ids(page["body_md"]))
    ws.artifact.original.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    groups = ws.groups()
    for group in groups:
        group["participant_count"] = 5
    ws.write_groups(groups)
    llm, update = FakeWriter(), FakeWriter("fake-update")
    result = ws.run(llm, update)
    assert llm.calls == [] and update.calls == []
    assert (result.get("write", 0), result.get("update", 0), result["kept"]) == (0, 0, 4)
    assert ws.pages()["pages"] == payload["pages"]


def test_second_run_makes_no_call_and_writes_nothing(ws: PagesWorkspace) -> None:
    llm, update = FakeWriter(), FakeWriter("fake-update")
    result = ws.run(llm, update)
    assert not result["written"] and llm.calls == [] and update.calls == []
    _make_newer(ws)
    result = ws.run(llm, update)
    assert result["written"] and llm.calls == [] and update.calls == []  # stale notice clears


def test_one_group_changed_updates_only_its_page_through_the_update_prompt(ws: PagesWorkspace) -> None:
    before = ws.pages()["pages"]
    llm, update = FakeWriter(), FakeWriter("fake-update")
    result = ws.run(llm, update)
    assert result["written"] is False
    _bump(ws, 8)  # group 8 is on agencies only
    result = ws.run(llm, update)
    assert llm.calls == [] and update.tasks() == ["update:agencies"]
    assert "changed: g-00000008 |" in _flatten(update.calls[0]["user"])
    after = ws.pages()["pages"]
    assert {pid for pid in PAGES if after[pid] != before[pid]} == {"agencies"}
    assert result["update"] == 1


def test_manual_edits_survive_on_untouched_pages_and_feed_the_update(ws: PagesWorkspace) -> None:
    _edit(ws, "costs", "עריכת בעלים בעלויות.")
    edited = ws.pages(edited=True)
    _edit_both = edited["pages"]
    _edit_both["agencies"]["body_md"] = _edit_both["agencies"]["body_md"].replace(
        "פתיח קצר על הדף.", "פתיח קצר על הדף. עריכת בעלים בסוכנויות.", 1)
    ws.write_edited(edited)
    _bump(ws, 8)
    update = FakeWriter("fake-update")
    ws.run(FakeWriter(), update)
    assert "עריכת בעלים בסוכנויות." in _flatten(update.calls[0]["user"])
    assert "עריכת בעלים בעלויות." in ws.pages(edited=True)["pages"]["costs"]["body_md"]
    assert "עריכת בעלים בעלויות." not in ws.pages()["pages"]["costs"]["body_md"]


def test_no_change_keeps_generated_at_and_records_the_new_fingerprint(ws: PagesWorkspace) -> None:
    before = ws.pages()["pages"]["agencies"]
    _bump(ws, 8)
    update = FakeWriter("fake-update", respond=lambda s, u, t: {
        "no_change": True, "body": "", "summary": "", "questions": [], "related": []})
    result = ws.run(FakeWriter(), update)
    after = ws.pages()["pages"]["agencies"]
    assert result["no_change"] == 1
    assert after["front_matter"]["generated"] == before["front_matter"]["generated"]
    assert after["input_fingerprint"] != before["input_fingerprint"]
    third = FakeWriter("fake-update")
    assert ws.run(FakeWriter(), third)["written"] is False and third.calls == []


def test_large_change_rewrites_the_page_in_full(ws: PagesWorkspace) -> None:
    _bump(ws, 0, 4, 8)  # every group on agencies
    llm, update = FakeWriter(), FakeWriter("fake-update")
    ws.run(llm, update)
    assert llm.tasks() == ["write:agencies"] and "update:costs" in update.tasks()


def test_a_new_writer_rewrites_every_page(ws: PagesWorkspace) -> None:
    llm = FakeWriter("another-model")
    ws.run(llm)
    assert sorted(llm.tasks()) == sorted(f"write:{pid}" for pid in PAGES)


def test_page_left_the_plan_or_without_groups_is_dropped(ws: PagesWorkspace) -> None:
    ws.write_plan(operations.delete(ws.plan(), "hospital").plan)
    payload = ws.assignments()
    for record in payload["assignments"].values():
        record["pages"] = [p for p in record["pages"] if p != "clinics"]
    ws.write_assignments(payload)
    llm = FakeWriter()
    result = ws.run(llm)
    assert list(ws.pages()["pages"]) == ["agencies", "costs"]
    assert llm.calls == [] and result["dropped"] == 1 and result["no_groups"] == 1


def test_front_matter_follows_a_renamed_category_without_a_call(ws: PagesWorkspace) -> None:
    _edit(ws, "agencies", "עריכה.")
    ws.write_plan(operations.rename(ws.plan(), "choosing", "בחירה חדשה").plan)
    llm, update = FakeWriter(), FakeWriter("fake-update")
    result = ws.run(llm, update)
    assert result["written"] and llm.calls == [] and update.calls == []
    page = ws.pages(edited=True)["pages"]["agencies"]
    assert page["front_matter"]["path"][1]["title"] == "בחירה חדשה"
    assert "עריכה." in page["body_md"]


def test_only_restricts_the_run(ws: PagesWorkspace) -> None:
    _bump(ws, 4)  # group 4: agencies first, costs second
    update = FakeWriter("fake-update")
    ws.run(FakeWriter(), update, only=["costs"])
    assert update.tasks() == ["update:costs"]
    update = FakeWriter("fake-update")
    ws.run(FakeWriter(), update)
    assert update.tasks() == ["update:agencies"]


def test_rebuild_rewrites_everything_after_a_backup(ws: PagesWorkspace) -> None:
    _edit(ws, "costs", "עריכה שתיגבה.")
    llm = FakeWriter()
    result = ws.run(llm, rebuild=True)
    assert len(llm.calls) == 4 and result["backup"]
    assert "עריכה שתיגבה." in Path(result["backup"]).read_text(encoding="utf-8")
    assert "עריכה שתיגבה." not in ws.pages(edited=True)["pages"]["costs"]["body_md"]


def test_estimate_counts_tokens_and_makes_no_writing_call(tmp_path: Path) -> None:
    ws = PagesWorkspace(tmp_path)
    llm, counted = FakeWriter(), []

    def count(system: str, user: str) -> int:
        counted.append(user)
        return len(user)

    result = ws.run(llm, estimate=True, count_tokens=count)
    assert llm.calls == [] and not ws.artifact.original.exists()
    assert [row["page"] for row in result["estimate"]] == PAGES and len(counted) == 4
    assert result["input_tokens"] == sum(len(u) for u in counted)


def test_a_failed_page_keeps_its_old_text(ws: PagesWorkspace) -> None:
    before = ws.pages()["pages"]["agencies"]
    _bump(ws, 8)

    def fail(system: Any, user: Any, task: str) -> dict[str, Any]:
        raise RuntimeError("provider down")

    result = ws.run(FakeWriter(), FakeWriter("fake-update", respond=fail))
    assert result["failed"] == {"agencies": "RuntimeError('provider down')"}
    assert ws.pages()["pages"]["agencies"] == before
    update = FakeWriter("fake-update")
    ws.run(FakeWriter(), update)
    assert update.tasks() == ["update:agencies"]  # retried on the next run


def test_pages_node_and_step_row() -> None:
    node = GRAPH.node("pages")
    assert node.upstream == ("groups", "plan", "assignments")
    step = SERVED_STEPS.step(node.step)
    assert step.spec.incremental_safe and step.spec.paid
    assert step.command()[1:] == ["-m", "step_7_write_pages.run"]
