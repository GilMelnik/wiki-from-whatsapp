"""Step 8's review passes: the cutting rule, what each pass's prompt holds, the cross pass and
its pages, the scope of a pass, batch and sync, a failing pass, what a change re-runs,
``--only``, a file of the one-call review, and the estimate (offline: fake review model,
synthetic wiki whose parts are ``before`` and ``during``, plus ``cross``)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from step_6_plan.model import iter_nodes
from step_8_review.inputs import REVIEW_CONFIG
from step_8_review.parts import CROSS_KINDS, cut
from step_8_review.proposals import answer_schema
from tests.pages_fixtures import SECRET
from tests.review_fixtures import FakeReviewer, ReviewWorkspace, flat, sentence_a, valid_answers
from utils.paths import resolve

PASSES = ["before", "during", "cross"]


@pytest.fixture()
def ws(tmp_path: Path) -> ReviewWorkspace:
    return ReviewWorkspace(tmp_path)


def _node(node_id: str, *children: dict[str, Any]) -> dict[str, Any]:
    return {"id": node_id, "title_he": node_id, "description": "", "children": list(children)}


TREE = [_node("a", _node("a1", _node("p1"), _node("p2")), _node("a2", _node("p3"))),
        _node("b", _node("p4")),
        _node("c", _node("p5", _node("p6"), _node("p7"))),
        _node("empty", _node("unwritten"))]
SIZES = {"p1": 10, "p2": 10, "p3": 10, "p4": 50, "p5": 10, "p6": 10, "p7": 10}


def test_a_node_whose_pages_fit_is_one_part() -> None:
    assert cut(TREE, SIZES, 100) == [("a", ("p1", "p2", "p3")), ("b", ("p4",)),
                                     ("c", ("p5", "p6", "p7"))]


def test_a_node_that_does_not_fit_is_cut_along_its_children() -> None:
    # a (30) does not fit, a1 (20) does; b's single page is a part although it is over the
    # limit; the page p5 with pages below it is a part of its own, then its children.
    assert cut(TREE, SIZES, 25) == [("a1", ("p1", "p2")), ("a2", ("p3",)), ("b", ("p4",)),
                                    ("p5", ("p5",)), ("p6", ("p6",)), ("p7", ("p7",))]


def test_with_no_room_every_page_is_a_part_and_small_siblings_are_never_packed() -> None:
    assert [part for part, _ in cut(TREE, SIZES, 0)] == ["p1", "p2", "a2", "b", "p5", "p6", "p7"]


def test_the_development_cut_follows_the_configured_size(
        ws: ReviewWorkspace, monkeypatch: pytest.MonkeyPatch) -> None:
    reviewer = FakeReviewer([])
    ws.review(reviewer)
    assert reviewer.passes() == PASSES and reviewer.batches == [PASSES]
    monkeypatch.setitem(REVIEW_CONFIG["part"], "max_input_tokens", 1)
    reviewer = FakeReviewer([])
    ws.review(reviewer)
    assert reviewer.passes() == ["agencies", "costs", "clinics", "hospital"]  # cross unchanged


def test_a_part_reads_its_pages_with_their_evidence_and_the_whole_plan(
        ws: ReviewWorkspace, monkeypatch: pytest.MonkeyPatch) -> None:
    reviewer = FakeReviewer([])
    ws.review(reviewer)
    before = reviewer.calls[0]["user"]
    assert '<part id="before" pages="agencies, costs">' in before
    assert sentence_a("g-00000000") in before and sentence_a("g-00000002") not in before
    assert '<page id="clinics"' not in before and "- page clinics |" in before  # the plan only
    assert "g-00000004 | mention -> agencies (full line on that page)" in before
    assert "Synthetic claim number 0 " in before and "Synthetic claim number 2 " not in before
    assert "ONE PART" in reviewer.calls[0]["system"]
    # Alone, costs gets the full line of the group whose main page is outside its part.
    monkeypatch.setitem(REVIEW_CONFIG["part"], "max_input_tokens", 1)
    reviewer = FakeReviewer([])
    ws.review(reviewer)
    costs = next(call["user"] for call in reviewer.calls if call["pass_id"] == "costs")
    assert "g-00000004 | mention -> agencies | " in costs and "full line on that page" not in costs


def test_the_cross_pass_reads_points_not_bodies(ws: ReviewWorkspace) -> None:
    reviewer = FakeReviewer([])
    ws.review(reviewer)
    cross = next(call for call in reviewer.calls if call["pass_id"] == "cross")
    user = cross["user"]
    assert '<page id="agencies" title=' in user and 'labels="' in user
    assert "- fact | נקודה g-00000000 | g-00000000" in user and "- page clinics |" in user
    assert "<body>" not in user and "<groups" not in user and "Synthetic claim" not in user
    assert SECRET not in user and "never propose a `mistake`" in cross["system"]
    assert cross["schema"] == answer_schema(CROSS_KINDS) and "mistake" not in CROSS_KINDS


def _drop_points(ws: ReviewWorkspace, *page_ids: str) -> None:
    payload = ws.pages(edited=ws.artifact.edited.exists())
    for page_id in page_ids:
        payload["pages"][page_id].pop("points")
    ws.write_edited(payload)


def test_pages_without_points_are_left_out_of_the_cross_pass_and_listed(ws: ReviewWorkspace) -> None:
    _drop_points(ws, "hospital")
    reviewer = FakeReviewer([])
    ws.review(reviewer)
    assert "<page id=\"hospital\"" not in reviewer.calls[-1]["user"]
    run = ws.suggestions_payload()["metadata"]["run"]
    assert run["without_points"] == ["hospital"]
    assert next(row for row in run["passes"] if row["id"] == "cross")["pages"] == [
        "agencies", "costs", "clinics"]
    _drop_points(ws, "agencies", "costs")
    reviewer = FakeReviewer([])
    ws.review(reviewer, rebuild=True)
    assert reviewer.passes() == ["before", "during"]  # one page with points: no cross pass


def test_a_cross_pass_never_keeps_a_mistake_and_no_pass_names_what_it_did_not_see(
        ws: ReviewWorkspace) -> None:
    outside = flat("contradiction", groups_a=["g-00000000"], groups_b=["g-00000002"],
                   pages=["agencies", "clinics"], clash="סותרות", correction="שני הצדדים")
    result = ws.review(FakeReviewer([], by_pass={"cross": [valid_answers()[0]],
                                                 "before": [outside]}))
    assert result["raised"] == 0
    assert any(line.startswith("cross #0 mistake") and "cannot raise a mistake" in line
               for line in result["dropped"])
    assert any(line.startswith("before #0 contradiction") and "did not see" in line
               for line in result["dropped"])


def test_batch_and_sync_write_the_same_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("step_8_review.run._now", lambda: "2026-01-01T00:00:00Z")
    payloads = []
    for name, batch in (("batch", True), ("sync", False)):
        root = tmp_path / name
        root.mkdir()
        workspace = ReviewWorkspace(root)
        reviewer = FakeReviewer(valid_answers(), batch=batch)
        workspace.review(reviewer)
        assert reviewer.batches == ([PASSES] if batch else [])
        assert reviewer.passes() == PASSES
        payloads.append(workspace.suggestions_payload())
    batch_file, sync_file = payloads
    assert batch_file["suggestions"] == sync_file["suggestions"]
    assert batch_file["metadata"]["inputs"] == sync_file["metadata"]["inputs"]
    runs = [{**p["metadata"]["run"], "batch": None} for p in payloads]
    assert runs[0] == runs[1]


@pytest.mark.parametrize("batch", [True, False])
def test_a_failing_pass_keeps_the_others_and_is_asked_again_alone(
        ws: ReviewWorkspace, batch: bool) -> None:
    result = ws.review(FakeReviewer(valid_answers(), fail={"during"}, batch=batch))
    assert result["failed"] == ["during"] and result["raised"] == 6  # cross raised split, label
    payload = ws.suggestions_payload()
    assert {s["raised_by"] for s in payload["suggestions"]} == {"before", "cross"}
    assert set(payload["metadata"]["inputs"]["passes"]) == {"before", "cross"}
    row = next(r for r in payload["metadata"]["run"]["passes"] if r["id"] == "during")
    assert row["failed"].startswith("no usable answer")
    reviewer = FakeReviewer(valid_answers(), batch=batch)
    again = ws.review(reviewer)
    assert reviewer.passes() == ["during"] and again["failed"] == [] and again["raised"] == 0
    assert set(ws.suggestions_payload()["metadata"]["inputs"]["passes"]) == set(PASSES)


def _edit(ws: ReviewWorkspace, page_id: str, *, body: str = "", statement: str = "") -> None:
    payload = ws.pages(edited=ws.artifact.edited.exists())
    page = payload["pages"][page_id]
    page["body_md"] += body
    if statement:
        page["points"][0]["statement"] = statement
    ws.write_edited(payload)


def test_a_change_reruns_only_the_passes_that_read_it(ws: ReviewWorkspace) -> None:
    ws.review(FakeReviewer([]))
    reviewer = FakeReviewer([])
    _edit(ws, "agencies", body="\nפסקה חדשה.")
    ws.review(reviewer)
    assert reviewer.passes() == ["before"]  # the body changed, the points did not
    reviewer = FakeReviewer([])
    _edit(ws, "hospital", statement="נקודה אחרת")
    ws.review(reviewer)
    assert reviewer.passes() == ["cross"]  # the points changed, the text did not
    plan = json.loads(resolve(ws.plan_artifact).read_text(encoding="utf-8"))
    plan["pages"]["clinics"]["description"] = "תיאור חדש"
    next(node for node, _ in iter_nodes(plan["tree"]) if node["id"] == "clinics")[
        "description"] = "תיאור חדש"
    ws.plan_artifact.edited.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    reviewer = FakeReviewer([])
    ws.review(reviewer)
    assert reviewer.passes() == ["during", "cross"]


def test_only_runs_the_named_passes_and_leaves_the_others_alone(ws: ReviewWorkspace) -> None:
    reviewer = FakeReviewer(valid_answers())
    ws.review(reviewer, only=["during"])
    assert reviewer.passes() == ["during"]
    payload = ws.suggestions_payload()
    assert set(payload["metadata"]["inputs"]["passes"]) == {"during"}
    assert {s["raised_by"] for s in payload["suggestions"]} == {"during"}
    reviewer = FakeReviewer(valid_answers())
    ws.review(reviewer)
    assert reviewer.passes() == ["before", "cross"]
    with pytest.raises(ValueError, match="before, during, cross"):
        ws.review(FakeReviewer([]), only=["no-such-part"])


def test_only_with_rebuild_drops_the_undecided_suggestions_of_the_named_pass_only(
        ws: ReviewWorkspace) -> None:
    ws.review(FakeReviewer(valid_answers()))
    by_kind = {s["kind"]: s["id"] for s in ws.suggestions_payload()["suggestions"]}
    ws.store().reject(by_kind["split"])
    stored = ws.suggestions_payload()["metadata"]["inputs"]["passes"]
    reviewer = FakeReviewer([])
    ws.review(reviewer, only=["during"], rebuild=True)
    assert reviewer.passes() == ["during"]
    kept = {s["id"]: s["status"] for s in ws.suggestions_payload(edited=True)["suggestions"]}
    assert by_kind["add_label"] not in kept and kept[by_kind["split"]] == "rejected"
    assert {by_kind[k] for k in ("mistake", "merge", "contradiction", "add_page")} <= set(kept)
    assert ws.suggestions_payload()["metadata"]["inputs"]["passes"] == stored


def test_a_file_of_the_one_call_review_loads_with_its_decisions_and_every_pass_due(
        ws: ReviewWorkspace) -> None:
    ws.review(FakeReviewer(valid_answers()))
    payload = ws.suggestions_payload()
    for suggestion in payload["suggestions"]:
        suggestion.pop("raised_by")
    payload["metadata"]["inputs"] = {"reviewer": "r", "plan": "p", "pages": {"agencies": "x"}}
    payload["metadata"]["run"] = {"calls": 1, "raised": 6, "dropped": [], "note": "ישן"}
    ws.suggestions.original.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    by_kind = {s["kind"]: s["id"] for s in payload["suggestions"]}
    store = ws.store()
    store.reject(by_kind["merge"])
    store.edit(by_kind["split"], {**payload["suggestions"][2]["proposal"], "new_page_id": "prices"})
    reviewer = FakeReviewer(valid_answers())
    result = ws.review(reviewer)
    assert reviewer.passes() == PASSES and result["raised"] == 0
    items = {s["id"]: s for s in ws.store().list()["items"]}
    assert len(items) == 6 and items[by_kind["merge"]]["status"] == "rejected"
    assert items[by_kind["split"]]["edited_proposal"]["new_page_id"] == "prices"
    assert set(ws.suggestions_payload()["metadata"]["inputs"]["passes"]) == set(PASSES)


def test_the_estimate_lists_every_pass_and_counts_the_due_ones(ws: ReviewWorkspace) -> None:
    reviewer = FakeReviewer([])
    first = ws.review(reviewer, estimate=True)
    assert reviewer.calls == [] and not ws.suggestions.original.exists()
    assert [row["id"] for row in first["passes"]] == PASSES and first["calls"] == 3
    assert first["passes"][0]["pages"] == ["agencies", "costs"] and first["batch"] is True
    assert first["input_tokens"] == sum(row["input_tokens"] for row in first["passes"]) > 0
    ws.review(reviewer)
    _edit(ws, "agencies", body="\nפסקה חדשה.")
    again = ws.review(FakeReviewer([]), estimate=True, use_batch=False)
    assert again["calls"] == 1 and again["batch"] is False
    assert [row["id"] for row in again["passes"] if row["due"]] == ["before"]
    assert again["input_tokens"] == again["passes"][0]["input_tokens"]


class ShapelessReviewer(FakeReviewer):
    """Answers the cross pass with JSON that is not a review answer."""

    def complete_batch_json(self, requests: Any) -> dict[str, Any]:
        answers = super().complete_batch_json(requests)
        return {**answers, "cross": {"note": "no list"}}


def test_an_answer_of_the_wrong_shape_fails_its_pass_only(ws: ReviewWorkspace) -> None:
    result = ws.review(ShapelessReviewer(valid_answers()))
    assert result["failed"] == ["cross"] and result["raised"] == 4
    row = next(row for row in result["passes"] if row["id"] == "cross")
    assert "no suggestions list" in row["failed"]
