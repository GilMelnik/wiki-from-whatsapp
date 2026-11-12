"""Step 8's run: C12 shape, stable ids, checks on the model's proposals, and the re-run cases
of the whole run (offline: fake review model, synthetic wiki). The passes themselves (cutting,
prompts, scope, batch, failures, ``--only``) are in ``test_review_parts``."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from review_tool.artifacts import GRAPH
from review_tool.steps import SERVED_STEPS
from step_8_review.inputs import REVIEW_CONFIG
from step_8_review.proposals import KINDS, answer_schema
from tests.pages_fixtures import SECRET
from tests.review_fixtures import FakeReviewer, ReviewWorkspace, flat, sentence_a, valid_answers


@pytest.fixture()
def ws(tmp_path: Path) -> ReviewWorkspace:
    return ReviewWorkspace(tmp_path)


def _by_kind(payload: dict) -> dict[str, dict]:
    return {s["kind"]: s for s in payload["suggestions"]}


def test_first_run_writes_c12_with_one_suggestion_per_kind(ws: ReviewWorkspace) -> None:
    reviewer = FakeReviewer(valid_answers())
    result = ws.review(reviewer)
    assert (result["calls"], result["raised"], result["failed"]) == (3, 6, [])
    payload = ws.suggestions_payload()
    assert set(_by_kind(payload)) == set(KINDS)
    raised_by = {s["kind"]: s["raised_by"] for s in payload["suggestions"]}
    assert raised_by == {"mistake": "before", "merge": "before", "split": "during",
                         "add_label": "during", "contradiction": "cross", "add_page": "cross"}
    for suggestion in payload["suggestions"]:
        assert re.fullmatch(r"sug-[0-9a-f]{8}", suggestion["id"])
        assert suggestion["status"] == "pending" and suggestion["reason"] and suggestion["title"]
        assert set(suggestion["raised_on"]) == set(suggestion["pages"])
        assert suggestion["page_marks"] == {}
    kinds = _by_kind(payload)
    assert kinds["mistake"]["proposal"] == {
        "page_id": "agencies", "sentence": sentence_a("g-00000004"),
        "group_ids": ["g-00000004"], "correction": "לתקן: הניסיון היה השני"}
    assert kinds["contradiction"]["proposal"]["groups_a"] == ["g-00000000"]
    assert kinds["contradiction"]["pages"] == ["agencies", "clinics"]
    assert kinds["split"]["proposal"] == {
        "op": "split", "page_id": "clinics", "new_page_id": "clinic-prices",
        "title_he": "מחירי מרפאות", "description": "תיאור", "scope": "תחום",
        "original_scope": "תחום מצומצם"}
    assert kinds["merge"]["proposal"] == {"op": "merge", "survivor_id": "agencies",
                                         "merged_ids": ["costs"]}
    assert kinds["merge"]["pages"] == ["agencies", "costs"]
    assert kinds["add_page"]["proposal"]["op"] == "add_page"
    assert kinds["add_page"]["pages"] == ["agencies", "hospital"]
    assert kinds["add_label"]["proposal"] == {"op": "add_label", "label_id": "texas",
                                             "title_he": "טקסס", "description": "תיאור",
                                             "kind": "country"}
    meta = payload["metadata"]
    assert set(meta["inputs"]) == {"passes"}
    assert set(meta["inputs"]["passes"]) == {"before", "during", "cross"}
    assert meta["model"] == "fake-opus" and meta["run"]["note"] == "הערה הערה הערה"
    rows = {row["id"]: row for row in meta["run"]["passes"]}
    assert rows["before"]["pages"] == ["agencies", "costs"] and rows["cross"]["raised"] == 2
    assert all(row["failed"] is None and row["input_tokens"] > 0 for row in rows.values())
    assert meta["run"]["batch"] is True and meta["run"]["without_points"] == []
    assert meta["summary"]["suggestions"] == 6 and "git_commit" in meta
    assert not ws.suggestions.edited.exists()


def test_the_same_answer_gives_the_same_ids(tmp_path: Path) -> None:
    ids = []
    for name in ("a", "b"):
        root = tmp_path / name
        root.mkdir()
        workspace = ReviewWorkspace(root)
        workspace.review(FakeReviewer(valid_answers()))
        ids.append([s["id"] for s in workspace.suggestions_payload()["suggestions"]])
    assert ids[0] == ids[1]


def test_unusable_and_repeated_proposals_are_dropped(ws: ReviewWorkspace) -> None:
    bad = [
        flat("mistake", page_id="agencies", sentence="משפט שאינו בדף.", group_ids=["g-00000000"],
             correction="x"),
        flat("mistake", page_id="agencies", sentence=sentence_a("g-00000000"),
             group_ids=["g-ffffffff"], correction="x"),
        flat("add_page", page_id="agencies", parent_id="choosing", title_he="דף", description="d",
             scope="s"),
        flat("contradiction", groups_a=["g-00000000"], groups_b=["g-00000000"], pages=["agencies"],
             clash="c", correction="c"),
        flat("merge", survivor_id="agencies", merged_ids=["no-such-page"]),
        valid_answers()[0], valid_answers()[0],
    ]
    result = ws.review(FakeReviewer([], by_pass={"before": bad}))
    assert result["raised"] == 1
    reasons = " ".join(result["dropped"])
    for expected in ("not in page agencies", "did not see: ['g-ffffffff']", "is used 2 times",
                     "groups on both sides", "did not see: ['no-such-page']", "already raised"):
        assert expected in reasons
    assert len(result["dropped"]) == 6


def test_each_pass_keeps_at_most_its_cap(ws: ReviewWorkspace, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(REVIEW_CONFIG["part"], "max_suggestions", 1)
    monkeypatch.setitem(REVIEW_CONFIG["cross"], "max_suggestions", 1)
    result = ws.review(FakeReviewer(valid_answers()))
    assert result["raised"] == 3
    assert sum("over the pass's max_suggestions" in d for d in result["dropped"]) == 5


def test_the_prompt_never_carries_identities_and_the_schema_is_strict(ws: ReviewWorkspace) -> None:
    reviewer = FakeReviewer([])
    ws.review(reviewer)
    assert reviewer.passes() == ["before", "during", "cross"]
    for call in reviewer.calls:
        prompt = call["system"] + call["user"]
        assert SECRET not in prompt and "t-SECRET" not in prompt and "reaction" not in prompt
        assert "<already_raised count=\"0\">" in prompt
    assert '<page id="agencies"' in reviewer.calls[0]["user"]
    assert "g-00000004 | mention -> agencies" in reviewer.calls[0]["user"]
    item = answer_schema(KINDS)["properties"]["suggestions"]["items"]
    assert item["additionalProperties"] is False and set(item["required"]) == set(item["properties"])
    assert reviewer.calls[0]["schema"] == answer_schema(KINDS)


def test_nothing_to_suggest_is_a_valid_answer(ws: ReviewWorkspace) -> None:
    result = ws.review(FakeReviewer([], note="הוויקי במצב טוב"))
    assert result["written"] and result["raised"] == 0
    assert ws.suggestions_payload()["suggestions"] == []


def test_second_run_on_the_same_wiki_makes_no_call_and_writes_nothing(ws: ReviewWorkspace) -> None:
    ws.review(FakeReviewer(valid_answers()))
    before = ws.suggestions.original.stat().st_mtime_ns
    reviewer = FakeReviewer(valid_answers())
    result = ws.review(reviewer)
    assert reviewer.calls == [] and result["written"] is False and result["calls"] == 0
    assert ws.suggestions.original.stat().st_mtime_ns == before


def test_a_newer_input_file_refreshes_the_output_without_a_call(ws: ReviewWorkspace) -> None:
    ws.review(FakeReviewer(valid_answers()))
    before = ws.suggestions_payload()["suggestions"]
    old = ws.artifact.original.stat().st_mtime_ns - 10**10
    os.utime(ws.suggestions.original, ns=(old, old))
    reviewer = FakeReviewer([])
    result = ws.review(reviewer)
    assert reviewer.calls == [] and result["written"] is True
    assert ws.suggestions_payload()["suggestions"] == before
    assert ws.suggestions.original.stat().st_mtime_ns > ws.artifact.original.stat().st_mtime_ns


def _edit_page(ws: ReviewWorkspace, page_id: str, text: str) -> None:
    payload = ws.pages(edited=ws.artifact.edited.exists())
    payload["pages"][page_id]["body_md"] += f"\n{text}\n"
    ws.write_edited(payload)


def test_a_run_after_changes_keeps_decisions_and_raises_nothing_again(ws: ReviewWorkspace) -> None:
    ws.review(FakeReviewer(valid_answers()))
    store = ws.store()
    kinds = {s["kind"]: s["id"] for s in store.list()["items"]}
    store.reject(kinds["merge"])
    store.accept(kinds["add_label"])
    edited_split = {**_by_kind(ws.suggestions_payload())["split"]["proposal"], "new_page_id": "prices"}
    store.edit(kinds["split"], edited_split)
    _edit_page(ws, "agencies", "עריכה של הבעלים.")
    renamed = valid_answers()[3]
    renamed["title"] = "מיזוג במילים אחרות"
    fresh = flat("add_label", label_id="georgia-state", title_he="מדינת ג׳ורג׳יה", description="ת",
                 label_kind="us-state", pages=["costs"])
    reviewer = FakeReviewer([renamed, valid_answers()[0], fresh])
    result = ws.review(reviewer)
    assert reviewer.passes() == ["before"] and result["raised"] == 1
    shown = reviewer.calls[0]["user"]
    # A pass is shown what was raised about its pages: the rejected merge, not the split.
    assert '"status": "rejected"' in shown and '"new_page_id"' not in shown
    items = {s["id"]: s for s in ws.store().list()["items"]}
    assert items[kinds["merge"]]["status"] == "rejected"
    assert items[kinds["add_label"]]["status"] == "accepted"
    assert items[kinds["split"]]["edited_proposal"]["new_page_id"] == "prices"
    assert items[kinds["mistake"]]["page_marks"] == {"agencies": "changed"}
    edited = {s["id"]: s for s in ws.suggestions_payload(edited=True)["suggestions"]}
    assert edited[kinds["mistake"]]["page_marks"] == {"agencies": "changed"}
    assert edited[kinds["merge"]]["page_marks"] == {}
    assert len(edited) == 7


def test_a_page_that_left_the_plan_is_marked_gone(ws: ReviewWorkspace) -> None:
    ws.review(FakeReviewer(valid_answers()))
    plan = ws.plan()
    from step_6_plan.operations import delete

    ws.plan_artifact.edited.write_text(json.dumps(delete(plan, "hospital").plan), encoding="utf-8")
    payload = ws.assignments()
    for record in payload["assignments"].values():
        record["pages"] = [p for p in record["pages"] if p != "hospital"]
    ws.write_assignments(payload)
    ws.review(FakeReviewer([]))
    marks = {s["kind"]: s["page_marks"] for s in ws.suggestions_payload()["suggestions"]}
    assert marks["add_page"] == {"hospital": "gone"}


def test_rebuild_asks_afresh_but_keeps_every_decided_suggestion(ws: ReviewWorkspace) -> None:
    ws.review(FakeReviewer(valid_answers()))
    store = ws.store()
    kinds = {s["kind"]: s["id"] for s in store.list()["items"]}
    store.reject(kinds["merge"])
    store.accept(kinds["add_label"])
    reviewer = FakeReviewer([valid_answers()[0], valid_answers()[3]])
    result = ws.review(reviewer, rebuild=True)
    assert result["raised"] == 1 and result["backup"]
    ids = {s["id"]: s["status"] for s in ws.suggestions_payload(edited=True)["suggestions"]}
    assert ids[kinds["merge"]] == "rejected" and ids[kinds["add_label"]] == "accepted"
    assert kinds["split"] not in ids and kinds["mistake"] in ids
    assert ws.suggestions_payload() == ws.suggestions_payload(edited=True)


def test_estimate_counts_tokens_and_makes_no_call(ws: ReviewWorkspace) -> None:
    reviewer = FakeReviewer(valid_answers())
    result = ws.review(reviewer, estimate=True)
    assert reviewer.calls == [] and result["written"] is False and result["input_tokens"] > 0
    assert not ws.suggestions.original.exists()


def test_a_pass_too_long_for_the_model_fails_alone_with_the_overshoot(ws: ReviewWorkspace) -> None:
    reviewer = FakeReviewer(valid_answers())
    budget = REVIEW_CONFIG["context_window_tokens"] - REVIEW_CONFIG["max_output_tokens"]

    def count(system: str, user: str) -> int:
        return budget + 1000 if '<part id="during"' in user else 10

    result = ws.review(reviewer, count_tokens=count)
    assert reviewer.passes() == ["before", "cross"] and result["failed"] == ["during"]
    row = next(row for row in result["passes"] if row["id"] == "during")
    assert "it is 1000 tokens" in row["failed"] and "Nothing was sent" in row["failed"]
    assert set(ws.suggestions_payload()["metadata"]["inputs"]["passes"]) == {"before", "cross"}


def test_suggestions_node_and_step_row() -> None:
    node = GRAPH.node("suggestions")
    assert node.upstream == ("plan", "pages") and node.step == "step_8_review"
    assert node.original.name == "suggestions.json" and node.edited.name == "suggestions_edited.json"
    keys = [n.key for n in GRAPH.nodes]
    assert keys.index("pages") < keys.index("suggestions") < keys.index("site")
    step = SERVED_STEPS.step(node.step)
    assert step.spec.incremental_safe and step.spec.paid
    assert step.command(rebuild=True)[1:] == ["-m", "step_8_review.run", "--rebuild"]
