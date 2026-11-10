"""Step 6b assignment: decision rules, the C8 shape, questions, proposals, registry entries."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from review_tool.artifacts import GRAPH
from review_tool.steps import SERVED_STEPS
from step_6_plan.assign_rules import ASSIGN_CONFIG, decide_labels, decide_pages, groups_on_page
from step_6_plan.coverage import NONE
from tests.assign_fixtures import FakeClassifier, Workspace, topic
from tests.plan_fixtures import PAGE_IDS, FakeLLM, make_groups

CONFIDENT = ASSIGN_CONFIG["confident_probability"]
SECOND = ASSIGN_CONFIG["second_page"]


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    return Workspace(tmp_path)


# --------------------------------------------------------------------------- decision rules


def test_confident_page_is_primary_and_alone() -> None:
    assert decide_pages({"a": 0.9, "b": 0.05, NONE: 0.05}) == ["a"]
    assert decide_pages({"a": CONFIDENT, "b": 0.1}) == ["a"]


def test_below_the_confident_probability_the_group_is_unplaced() -> None:
    assert decide_pages({"a": CONFIDENT - 0.01, "b": 0.1, NONE: 0.4}) == []
    assert decide_pages({}) == []
    assert decide_pages({NONE: 1.0}) == []


def test_a_clear_split_gives_two_pages_most_probable_first() -> None:
    p2 = SECOND["min_probability"]
    p1 = SECOND["min_pair_probability"] - p2
    assert decide_pages({"b": p2, "a": p1, "c": 0.01}) == ["a", "b"]
    assert decide_pages({"a": 0.6, "b": p2}) == ["a", "b"]


def test_no_second_page_without_a_clear_split() -> None:
    assert decide_pages({"a": 0.6, "b": SECOND["min_probability"] - 0.01}) == ["a"]
    low = SECOND["min_pair_probability"] - 0.01
    assert decide_pages({"a": low / 2 + 0.005, "b": low / 2 - 0.005}) == []


def test_labels_apply_above_their_threshold_in_plan_order() -> None:
    threshold = ASSIGN_CONFIG["label_probability"]
    probs = {"b": threshold, "a": 0.99, "c": threshold - 0.01}
    assert decide_labels(probs, ["a", "b", "c", "d"]) == ["a", "b"]


# --------------------------------------------------------------------------- a first run


def test_first_run_writes_c8_in_group_order(ws: Workspace) -> None:
    result = ws.run()
    payload = ws.edited()
    assert json.loads(ws.artifact.original.read_text(encoding="utf-8")) == payload
    assert result["written"] and result["full"] == 12
    assert set(payload) == {"assignments", "proposals", "metadata"}
    assert list(payload["assignments"]) == [g["group_id"] for g in make_groups()]
    for index, (gid, record) in enumerate(payload["assignments"].items()):
        assert set(record) == {"pages", "labels", "p", "label_p"}
        assert record["pages"] == [PAGE_IDS[index % 4]]
        assert record["p"] == {PAGE_IDS[index % 4]: 0.9}  # only kept probabilities, no "none"
        assert record["labels"] == (["usa"] if index % 2 == 0 else [])
        assert all(v >= ASSIGN_CONFIG["kept_probability"] for v in record["label_p"].values())
    meta = payload["metadata"]
    assert set(meta["inputs"]) == set(payload["assignments"])
    assert set(meta["plan"]) == {"pages", "labels"}
    assert meta["classifier"] == "fake/fake-jev"
    assert meta["summary"]["page_sizes"]["agencies"] == {"all": 3, "primary": 3}
    assert meta["summary"]["label_counts"] == {"usa": 6, "georgia": 0}
    assert payload["proposals"] == []
    assert groups_on_page(payload["assignments"], "costs") == ["g-00000001", "g-00000005",
                                                                "g-00000009"]


def test_page_and_labels_go_in_one_request(ws: Workspace) -> None:
    clf = FakeClassifier()
    ws.run(clf)
    assert len(clf.items) == 12
    for _, questions in clf.items:
        assert [q.id for q in questions] == ["page", "label:usa", "label:georgia"]
        assert questions[0].options == (*PAGE_IDS, NONE)


def test_many_labels_are_split_over_requests(ws: Workspace, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(ASSIGN_CONFIG, "max_label_questions", 1)
    clf = FakeClassifier()
    ws.run(clf)
    assert len(clf.items) == 24
    assert ws.edited()["assignments"]["g-00000000"]["labels"] == ["usa"]


def test_two_pages_when_the_classifier_splits(ws: Workspace) -> None:
    def split(state: str, options: Any) -> dict[str, float]:
        return {**{o: 0.0 for o in options}, "costs": 0.45, "agencies": 0.4, NONE: 0.15}

    ws.run(FakeClassifier(page=split))
    assert {tuple(r["pages"]) for r in ws.edited()["assignments"].values()} == {("costs", "agencies")}
    assert ws.edited()["metadata"]["summary"]["two_pages"] == 12


def test_the_classifier_sees_group_text_and_entity_names_only(ws: Workspace) -> None:
    clf = FakeClassifier()
    ws.run(clf)
    states = "\n".join(state for state, _ in clf.items)
    assert "Synthetic claim number 1 about topic 1." in states and "Entity1" in states
    for hidden in ("SECRET", "e0001", "2023-01", "by_month", "supporter"):
        assert hidden not in states


# --------------------------------------------------------------------------- proposals


def _unplaced_odd(state: str, options: Any) -> dict[str, float]:
    """Odd topics fit no page; even topics go to their page."""
    probs = {o: 0.0 for o in options}
    if topic(state) % 2:
        probs[NONE] = 0.8
    else:
        probs[PAGE_IDS[topic(state)]] = 0.9
    return probs


def _proposal(kind: str, title: str, group_ids: list[str], **fields: str) -> dict[str, Any]:
    base = {"kind": kind, "target_id": "", "parent_id": "", "label_kind": "", "title_he": title,
            "description": f"תיאור {title}", "scope": f"תחום {title}", "reason": "סיבה",
            "group_ids": group_ids}
    return {**base, **fields}


def test_unplaced_groups_become_pending_proposals(ws: Workspace) -> None:
    answer = {"proposals": [
        _proposal("page", "עמוד חדש", ["g-00000001", "g-00000003", "g-not-a-group"],
                  target_id="new-page", parent_id="choosing"),
        _proposal("label", "תווית", ["g-00000005"], target_id="usa", label_kind="Bad Kind"),
        _proposal("scope", "הרחבה", ["g-00000007"], target_id="costs"),
        _proposal("scope", "לעמוד שלא קיים", ["g-00000007"], target_id="nope"),
        _proposal("page", "", []),
        _proposal("page", "עמוד  חדש", ["g-00000009"]),  # same title again
    ]}
    llm = FakeLLM([answer])
    result = ws.run(FakeClassifier(page=_unplaced_odd), llm)
    proposals = ws.edited()["proposals"]
    assert result["proposal_calls"] == 1 and result["proposal_candidates"] == 6
    assert [p["kind"] for p in proposals] == ["page", "label", "scope"]
    page, label, scope = proposals
    assert page["group_ids"] == ["g-00000001", "g-00000003"]
    assert (page["target_id"], page["parent_id"], page["status"]) == ("new-page", "choosing", "pending")
    assert page["id"].startswith(ASSIGN_CONFIG["proposals"]["id_prefix"])
    assert (label["target_id"], label["label_kind"]) == ("", "")  # taken id, bad slug: blanked
    assert "scope" not in label and "parent_id" not in label
    assert scope["target_id"] == "costs"
    assert len({p["id"] for p in proposals}) == 3
    # the step never touches the plan
    assert "new-page" not in ws.plan()["pages"]


def test_proposals_are_never_made_twice(ws: Workspace) -> None:
    first = {"proposals": [_proposal("page", "עמוד חדש", ["g-00000001", "g-00000003"])]}
    ws.run(FakeClassifier(page=_unplaced_odd), FakeLLM([first]))
    # Unchanged inputs: the remaining unplaced groups were already shown -> no call.
    assert ws.run(FakeClassifier(page=_unplaced_odd), FakeLLM([]))["proposal_calls"] == 0
    payload = ws.edited()
    payload["proposals"][0]["status"] = "rejected"
    ws.write_edited(payload)
    assert ws.run(FakeClassifier(page=_unplaced_odd), FakeLLM([]))["proposal_calls"] == 0
    assert ws.edited()["proposals"][0]["status"] == "rejected"
    # New unplaced groups arrive: the model sees the rejected proposal and may not repeat it.
    ws.write_groups(make_groups(18))
    again = {"proposals": [_proposal("page", "עמוד חדש", ["g-0000000d"]),
                           _proposal("label", "תווית אחרת", ["g-0000000d", "g-0000000f"])]}
    llm = FakeLLM([again])
    result = ws.run(FakeClassifier(page=_unplaced_odd), llm)
    assert result["proposal_candidates"] == 3  # g-0d, g-0f, g-11; not g-01 / g-03 (named)
    prompt = llm.calls[0]["user"]
    assert "עמוד חדש" in prompt and "rejected" in prompt
    assert "SECRET" not in prompt and "by_month" not in prompt and "t-SECRET" not in prompt
    titles = [(p["title_he"], p["status"]) for p in ws.edited()["proposals"]]
    assert titles == [("עמוד חדש", "rejected"), ("תווית אחרת", "pending")]


def test_too_few_unplaced_groups_wait(ws: Workspace) -> None:
    def one_unplaced(state: str, options: Any) -> dict[str, float]:
        return _unplaced_odd(state, options) if "number 1 " in state else {"costs": 0.9}

    result = ws.run(FakeClassifier(page=one_unplaced), FakeLLM([]))
    assert result["proposal_candidates"] == 1 and result["proposal_calls"] == 0
    assert ws.edited()["metadata"]["considered"] == {}


# --------------------------------------------------------------------------- review tool


def test_assignments_are_in_the_graph_and_the_registry() -> None:
    node = GRAPH.node("assignments")
    assert node.upstream == ("groups", "plan")
    assert node.original.name == "assignments.json"
    assert node.edited.name == "assignments_edited.json"
    keys = [n.key for n in GRAPH.nodes]
    assert keys.index("plan") < keys.index("assignments") < keys.index("pages")
    step = SERVED_STEPS.step(node.step)
    assert step.spec.incremental_safe and step.spec.paid
    assert step.command()[1:] == ["-m", "step_6_plan.assign"]
    assert step.command(rebuild=True)[-1] == "--rebuild"
