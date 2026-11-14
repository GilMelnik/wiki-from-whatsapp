"""The gold file's context part, the wider score, the edge analysis and the measure helpers.

One window 10:00-12:00. Tagged threads: T1 = p1 a1 a2, T2 = b1 b2, T3 = q1, T4 = u1, T5 = z1.
The old split showed q1 p1 a1 a2 b1 b2 as one thread, so the owner saw q1 too; u1 and z1 were
never shown.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest

from step_1_threads_split.evaluate import evaluate, main
from step_1_threads_split.gold import Window, freeze_gold, load_gold
from step_1_threads_split.gold_context import add_context, edge_cut_labels, without_labels
from step_1_threads_split.measure import disagreements, pooled_f1
from utils import logging_setup

WINDOW = {"start": "2025-01-01T10:00", "end": "2025-01-01T12:00"}
MOMENTS = {
    "q1": "2025-01-01T08:00:00",
    "p1": "2025-01-01T09:00:00",
    "u1": "2025-01-01T09:30:00",
    "a1": "2025-01-01T10:00:00",
    "b1": "2025-01-01T10:10:00",
    "a2": "2025-01-01T10:30:00",
    "b2": "2025-01-01T13:00:00",
    "z1": "2025-01-01T14:00:00",
}
CHAT_ORDER = list(MOMENTS)
TAGGED = [["p1", "a1", "a2"], ["b1", "b2"], ["q1"], ["u1"], ["z1"]]
OLD = [["q1", "p1", "a1", "a2", "b1", "b2"], ["u1"], ["z1"]]


def _threads(clusters: list[list[str]]) -> list[dict[str, Any]]:
    return [
        {
            "thread_id": f"t-{cluster[0]}",
            "messages": [{"id": i, "datetime": MOMENTS[i], "sender": "s", "content": "c"}
                         for i in cluster],  # fmt: skip
        }
        for cluster in clusters
    ]


@pytest.fixture
def gold(tmp_path: Path) -> dict[str, Any]:
    frozen = freeze_gold(
        _threads(TAGGED), [Window.from_dict(WINDOW)], tmp_path / "gold.json", tmp_path, "test"
    )
    return add_context(
        frozen, _threads(TAGGED), [_threads(OLD)], tagged_source="tagged", shown_sources=["old"]
    )


def test_context_part_is_additive_and_labels_follow_the_tagged_threads(
    gold: dict[str, Any], tmp_path: Path
) -> None:
    frozen = load_gold(tmp_path / "gold.json")
    (window,) = gold["windows"]
    assert window["message_ids"] == frozen["windows"][0]["message_ids"] == ["a1", "b1", "a2"]
    assert gold["messages"] == frozen["messages"]
    assert window["context_message_ids"] == ["q1", "p1", "b2"]
    assert gold["context_messages"] == {"q1": "q1", "p1": "a1", "b2": "b1"}
    assert gold["metadata"]["context"]["message_count"] == 3
    path = tmp_path / "with_context.json"
    path.write_text(json.dumps(gold), encoding="utf-8")
    assert load_gold(path) == gold
    old = _threads(OLD)
    assert evaluate(old, gold) == evaluate(old, frozen)


def test_window_plus_context_scores_the_shown_messages(gold: dict[str, Any]) -> None:
    perfect = evaluate(_threads(TAGGED), gold, with_context=True)["micro"]
    assert perfect["messages"] == 6 and perfect["bcubed_f1"] == 1.0
    old = evaluate(_threads(OLD), gold, with_context=True)["micro"]
    assert old["bcubed_recall"] == 1.0 and old["bcubed_precision"] < 0.5
    with pytest.raises(ValueError, match="no context part"):
        evaluate(_threads(OLD), {**gold, "windows": [{**WINDOW, "message_ids": ["a1"]}]},
                 with_context=True)  # fmt: skip


def test_add_context_rejects_a_tagged_file_that_is_not_the_frozen_source(
    gold: dict[str, Any],
) -> None:
    with pytest.raises(ValueError, match="two clusters"):
        add_context(gold, _threads([["a1", "b1", "a2", "b2"], ["p1"], ["q1"]]), [],
                    tagged_source="x", shown_sources=[])  # fmt: skip


def test_load_gold_rejects_an_inconsistent_context(gold: dict[str, Any], tmp_path: Path) -> None:
    broken = {**gold, "context_messages": {"q1": "q1"}}
    path = tmp_path / "broken.json"
    path.write_text(json.dumps(broken), encoding="utf-8")
    with pytest.raises(ValueError, match="inconsistent context part"):
        load_gold(path)


def test_a_thread_whose_neighbour_was_never_shown_is_cut_by_an_edge(gold: dict[str, Any]) -> None:
    assert edge_cut_labels(gold, CHAT_ORDER) == [{"b1"}]
    trimmed = without_labels(gold, [{"b1"}])
    assert trimmed["windows"][0]["message_ids"] == ["a1", "a2"]
    assert trimmed["windows"][0]["context_message_ids"] == ["q1", "p1"]
    with pytest.raises(ValueError, match="no context part"):
        edge_cut_labels({k: v for k, v in gold.items() if k != "context_messages"}, CHAT_ORDER)


def test_disagreements_are_named_and_flagged_when_an_edge_is_involved(
    gold: dict[str, Any],
) -> None:
    found = disagreements(_threads(OLD), gold, [{"b1"}], with_context=False)
    assert found == [
        {"window": WINDOW["start"], "kind": "merge", "edge": True, "message_ids": ["b1"]}
    ]
    split = _threads([["a1"], ["a2"], ["b1"], ["p1"], ["q1"], ["b2"], ["u1"], ["z1"]])
    found = disagreements(split, gold, [set()], with_context=True)
    assert [(item["kind"], item["edge"]) for item in found] == [("split", False)] * 3
    assert sorted(i for item in found for i in item["message_ids"]) == ["a1", "a2", "b1"]


def test_pooled_f1_equals_the_micro_average(gold: dict[str, Any]) -> None:
    result = evaluate(_threads(OLD), gold, with_context=True)
    assert pooled_f1(result["windows"]) == pytest.approx(result["micro"]["bcubed_f1"])


def test_cli_adds_the_context_and_scores_both(
    gold: dict[str, Any], tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    tagged = tmp_path / "tagged.json"
    tagged.write_text(json.dumps({"threads": _threads(TAGGED)}), encoding="utf-8")
    old = tmp_path / "old.json"
    old.write_text(json.dumps({"threads": _threads(OLD)}), encoding="utf-8")
    gold_path = tmp_path / "gold.json"
    caplog.set_level(logging.INFO)
    try:
        main(["--add-context", "--gold", str(gold_path), "--tagged", str(tagged),
              "--shown", str(old)])  # fmt: skip
        written = load_gold(gold_path)
        assert written["context_messages"] == gold["context_messages"]
        main(["--context", "--gold", str(gold_path), "--pred", str(old)])
        assert "window + context:" in caplog.text
        with pytest.raises(SystemExit):
            main(["--add-context", "--freeze-gold"])
        with pytest.raises(SystemExit):
            main(["--tagged", str(tagged)])
    finally:
        handler = logging_setup._file_handler
        if handler is not None:
            logging.getLogger().removeHandler(handler)
            handler.close()
            logging_setup._file_handler = None
            logging_setup._current_step = None
