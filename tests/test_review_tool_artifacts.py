"""Artifact graph (existence, staleness) and the file watcher."""

from __future__ import annotations

from pathlib import Path

import pytest

from review_tool.artifacts import GRAPH, ArtifactGraph, ArtifactNode, ArtifactStatus
from review_tool.events import ARTIFACT_CHANGED
from review_tool.watcher import ArtifactWatcher
from tests.review_tool_support import build_graph, new_bus, write_file
from utils.file_lock import file_lock
from utils.json_io import write_json_file

DEBOUNCE = 0.3


def status_by_key(graph: ArtifactGraph) -> dict[str, ArtifactStatus]:
    return {status.key: status for status in graph.status()}


def make_watcher(graph: ArtifactGraph) -> ArtifactWatcher:
    return ArtifactWatcher(graph, new_bus(), interval_seconds=1.0, debounce_seconds=DEBOUNCE)


# ------------------------------------------------------------------ graph


def test_graph_rejects_duplicate_key(tmp_path: Path) -> None:
    graph = build_graph(tmp_path)
    with pytest.raises(ValueError, match="already in the graph"):
        graph.add(ArtifactNode("threads", "Again", tmp_path / "again.json"))


def test_graph_rejects_upstream_that_is_not_listed_yet(tmp_path: Path) -> None:
    graph = build_graph(tmp_path)
    with pytest.raises(ValueError, match="plan"):
        graph.add(ArtifactNode("pages", "Pages", tmp_path / "pages.json", upstream=("plan",)))


def test_unknown_key_error_names_the_known_keys(tmp_path: Path) -> None:
    with pytest.raises(KeyError, match="known keys.*export.*threads.*claims"):
        build_graph(tmp_path).node("nope")


def test_default_graph_is_the_pipeline_in_order() -> None:
    keys = [node.key for node in GRAPH.nodes]
    assert keys == [
        "export", "threads", "gold_threads", "classified", "claims", "entities",
        "claim_aggregations", "groups", "plan", "assignments", "pages", "site",
    ]  # fmt: skip
    assert (GRAPH.node("site").step, GRAPH.node("site").upstream) == ("step_9_site", ("plan", "pages"))
    assert GRAPH.node("site").edited is None
    assert GRAPH.node("export").edited is None
    assert (GRAPH.node("gold_threads").step, GRAPH.node("gold_threads").upstream) == (None, ())
    assert (GRAPH.node("claim_aggregations").step, GRAPH.node("claim_aggregations").edited) == (
        None,
        None,
    )
    assert GRAPH.node("groups").upstream == ("claims", "entities", "claim_aggregations")
    assert GRAPH.node("claims").upstream == ("threads", "classified")
    assert GRAPH.node("claims").original.name == "claims.json"
    assert GRAPH.node("claims").edited.name == "claims_edited.json"


def test_status_of_missing_files(tmp_path: Path) -> None:
    for status in build_graph(tmp_path).status():
        assert (status.exists, status.mtime, status.edited_exists) == (False, None, False)
        assert status.stale_because == ()


def test_status_reports_existence_and_edited_copy(tmp_path: Path) -> None:
    write_file(tmp_path / "threads.json", {}, mtime=2000)
    write_file(tmp_path / "threads_edited.json", {}, mtime=2500)
    status = status_by_key(build_graph(tmp_path))
    assert status["threads"].exists and status["threads"].edited_exists
    assert status["threads"].mtime == "1970-01-01T00:33:20.000+00:00"
    assert status["threads"].edited_mtime == "1970-01-01T00:41:40.000+00:00"
    assert status["threads"].has_edited_slot and not status["export"].has_edited_slot
    assert (status["claims"].exists, status["claims"].edited_exists) == (False, False)


def test_artifact_is_stale_when_an_upstream_file_is_newer(tmp_path: Path) -> None:
    graph = build_graph(tmp_path)
    write_file(tmp_path / "export.json", {}, mtime=1000)
    write_file(tmp_path / "threads.json", {}, mtime=2000)
    write_file(tmp_path / "claims.json", {}, mtime=3000)
    assert all(status.stale_because == () for status in graph.status())

    write_file(tmp_path / "export.json", {}, mtime=5000)
    status = status_by_key(graph)
    assert status["threads"].stale_because == ("export",)
    assert status["claims"].stale_because == ()  # staleness is per direct upstream


def test_editing_the_upstream_copy_makes_downstream_stale(tmp_path: Path) -> None:
    graph = build_graph(tmp_path)
    write_file(tmp_path / "threads.json", {}, mtime=2000)
    write_file(tmp_path / "claims.json", {}, mtime=3000)
    write_file(tmp_path / "claims_edited.json", {}, mtime=3500)
    write_file(tmp_path / "threads_edited.json", {}, mtime=4000)
    assert status_by_key(graph)["claims"].stale_because == ("threads",)


def test_a_curated_artifact_is_never_stale_but_its_downstream_is(tmp_path: Path) -> None:
    """The plan: new groups do not make it stale (a step-6 run would keep it as is), they
    make the assignments stale (the assignment step places them)."""
    graph = ArtifactGraph([
        ArtifactNode("groups", "Groups", tmp_path / "groups.json", step="step_5"),
        ArtifactNode("plan", "Plan", tmp_path / "plan.json", step="step_6", upstream=("groups",),
                     curated=True),
        ArtifactNode("assignments", "Assignments", tmp_path / "assignments.json",
                     step="step_6b", upstream=("groups", "plan")),
    ])
    write_file(tmp_path / "plan.json", {}, mtime=2000)
    write_file(tmp_path / "assignments.json", {}, mtime=3000)
    write_file(tmp_path / "groups.json", {}, mtime=5000)
    status = status_by_key(graph)
    assert status["plan"].stale_because == ()
    assert status["assignments"].stale_because == ("groups",)
    assert graph.node("plan").upstream == ("groups",)  # still ordered after step 5's jobs


def test_the_pipeline_plan_is_curated() -> None:
    assert GRAPH.node("plan").curated and GRAPH.node("plan").upstream == ("groups",)
    assert not any(node.curated for node in GRAPH.nodes if node.key != "plan")


def test_edited_copy_is_the_effective_upstream_file(tmp_path: Path) -> None:
    graph = build_graph(tmp_path)
    write_file(tmp_path / "threads.json", {}, mtime=5000)  # newer than claims, but superseded
    write_file(tmp_path / "threads_edited.json", {}, mtime=2500)
    write_file(tmp_path / "claims.json", {}, mtime=3000)
    assert status_by_key(graph)["claims"].stale_because == ()
    assert graph.node("threads").effective == tmp_path / "threads_edited.json"
    assert graph.node("claims").effective == tmp_path / "claims.json"


def test_missing_lists_artifacts_without_any_file(tmp_path: Path) -> None:
    graph = build_graph(tmp_path)
    write_file(tmp_path / "threads_edited.json", {})
    assert graph.missing(["export", "threads", "claims"]) == ["export", "claims"]


def test_files_returns_every_file_of_the_given_keys(tmp_path: Path) -> None:
    assert build_graph(tmp_path).files("export", "claims") == (
        tmp_path / "export.json",
        tmp_path / "claims.json",
        tmp_path / "claims_edited.json",
    )


# ---------------------------------------------------------------- watcher


def test_watcher_is_silent_while_nothing_changes(tmp_path: Path) -> None:
    write_file(tmp_path / "threads.json", {"n": 1})
    watcher = make_watcher(build_graph(tmp_path))
    assert watcher.poll_once(100.0) == []
    assert watcher.poll_once(101.0) == []


def test_rewritten_file_publishes_exactly_one_event(tmp_path: Path) -> None:
    write_file(tmp_path / "threads.json", {"n": 1})
    watcher = make_watcher(build_graph(tmp_path))

    write_json_file({"n": 2}, tmp_path / "threads.json")
    assert watcher.poll_once(100.0) == []  # seen, waiting for it to settle
    assert watcher.poll_once(100.1) == []  # unchanged, but for less than the debounce
    events = watcher.poll_once(100.5)
    assert len(events) == 1
    assert events[0]["type"] == ARTIFACT_CHANGED
    assert events[0]["key"] == "threads"
    assert events[0]["payload"] == {"files": ["original"]}
    assert watcher.poll_once(101.0) == []
    assert watcher.poll_once(102.0) == []


def test_burst_of_writes_is_one_event(tmp_path: Path) -> None:
    write_file(tmp_path / "threads.json", {"n": 1})
    watcher = make_watcher(build_graph(tmp_path))

    write_json_file({"n": 2}, tmp_path / "threads.json")
    assert watcher.poll_once(100.0) == []
    write_json_file({"n": 3}, tmp_path / "threads.json")  # still being written
    assert watcher.poll_once(100.5) == []  # the debounce restarts at the latest change
    assert watcher.poll_once(100.6) == []
    assert len(watcher.poll_once(101.0)) == 1
    assert watcher.poll_once(110.0) == []


def test_file_appearing_and_disappearing_are_changes(tmp_path: Path) -> None:
    watcher = make_watcher(build_graph(tmp_path))
    edited = tmp_path / "claims_edited.json"

    write_file(edited, {})
    watcher.poll_once(100.0)
    (event,) = watcher.poll_once(101.0)
    assert (event["key"], event["payload"]) == ("claims", {"files": ["edited"]})

    edited.unlink()
    watcher.poll_once(102.0)
    (event,) = watcher.poll_once(103.0)
    assert (event["key"], event["payload"]) == ("claims", {"files": ["edited"]})


def test_both_files_of_an_artifact_are_one_event(tmp_path: Path) -> None:
    watcher = make_watcher(build_graph(tmp_path))
    write_file(tmp_path / "threads.json", {})
    write_file(tmp_path / "threads_edited.json", {})
    write_file(tmp_path / "export.json", {})
    watcher.poll_once(100.0)
    events = watcher.poll_once(101.0)
    assert [(event["key"], event["payload"]["files"]) for event in events] == [
        ("export", ["original"]),
        ("threads", ["original", "edited"]),
    ]


def test_node_added_after_start_is_watched_without_a_false_event(tmp_path: Path) -> None:
    graph = build_graph(tmp_path)
    watcher = make_watcher(graph)
    write_file(tmp_path / "plan.json", {})
    graph.add(ArtifactNode("plan", "Plan", tmp_path / "plan.json", upstream=("claims",)))
    assert watcher.poll_once(100.0) == []
    assert watcher.poll_once(101.0) == []

    write_json_file({"pages": 1}, tmp_path / "plan.json")
    watcher.poll_once(102.0)
    assert [event["key"] for event in watcher.poll_once(103.0)] == ["plan"]


def test_lock_sidecars_and_temp_files_are_not_changes(tmp_path: Path) -> None:
    write_file(tmp_path / "threads.json", {"n": 1})
    watcher = make_watcher(build_graph(tmp_path))
    with file_lock(tmp_path / "threads.json"):  # leaves threads.json.lock behind
        (tmp_path / ".threads.json.tmp-123-abcd").write_text("{", encoding="utf-8")
    assert watcher.poll_once(100.0) == []
    assert watcher.poll_once(101.0) == []


def test_reconciled_write_of_both_files_across_polls_is_one_event(tmp_path: Path) -> None:
    """``utils.reconcile`` writes the edited file, then the original, under one lock."""
    original, edited = tmp_path / "threads.json", tmp_path / "threads_edited.json"
    write_file(original, {"n": 1})
    write_file(edited, {"n": 1})
    watcher = make_watcher(build_graph(tmp_path))

    with file_lock(original), file_lock(edited):
        write_json_file({"n": 2}, edited)
        assert watcher.poll_once(100.0) == []  # a poll lands between the two writes
        write_json_file({"n": 2}, original)
    assert watcher.poll_once(100.4) == []  # the artifact changed again: debounce restarts
    (event,) = watcher.poll_once(101.0)
    assert (event["key"], event["payload"]) == ("threads", {"files": ["original", "edited"]})
    assert watcher.poll_once(102.0) == []
