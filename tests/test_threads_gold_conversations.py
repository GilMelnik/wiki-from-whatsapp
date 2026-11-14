"""The owner's conversations in the gold file and the measure's owner part (synthetic data).

Step 1's split: A = a1 a2 a3, B = b1 b2, C = c1, D = d1 d2. The owner cut a3 out of A and
joined it to B; C and D were left as they were. So the edited split is A' = a1 a2,
B' = a3 b1 b2, C, D, and the changed conversations are A' and B'.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest

from step_1_threads_split.gold import Window, freeze_gold, load_gold
from step_1_threads_split.gold_conversations import (
    add_conversations,
    add_conversations_to_file,
    changed_conversations,
    conversation_counts,
    main,
    pooled_bcubed,
)
from step_1_threads_split.measure_owner import (
    OwnerConversations,
    changed_outside,
    owner_section,
    reviewed_unchanged,
    true_order,
)
from utils import logging_setup

MOMENTS = {
    "a1": "2025-01-01T10:00:00",
    "a2": "2025-01-01T10:05:00",
    "b1": "2025-01-01T11:00:00",
    "a3": "2025-01-01T11:00:00",
    "b2": "2025-01-01T11:10:00",
    "c1": "2025-01-02T09:00:00",
    "d1": "2025-01-03T09:00:00",
    "d2": "2025-01-03T09:01:00",
}
ORIGINAL = [["a1", "a2", "a3"], ["b1", "b2"], ["c1"], ["d1", "d2"]]
EDITED = [["a1", "a2"], ["a3", "b1", "b2"], ["c1"], ["d1", "d2"]]


def threads(clusters: list[list[str]]) -> list[dict[str, Any]]:
    return [
        {
            "thread_id": f"t-{cluster[0]}",
            "message_ids": cluster,
            "messages": [{"id": i, "datetime": MOMENTS[i], "sender": "s", "content": "c"}
                         for i in cluster],  # fmt: skip
        }
        for cluster in clusters
    ]


@pytest.fixture
def gold_path(tmp_path: Path) -> Path:
    path = tmp_path / "gold.json"
    window = Window.from_dict({"start": "2025-01-03T09:00", "end": "2025-01-03T10:00"})
    freeze_gold(threads(EDITED), [window], path, tmp_path / "backups", "test")
    return path


def _write(path: Path, clusters: list[list[str]]) -> Path:
    path.write_text(json.dumps({"threads": threads(clusters)}), encoding="utf-8")
    return path


def test_the_changed_conversations_are_the_edited_threads_step_1_does_not_have() -> None:
    found = changed_conversations(threads(ORIGINAL), threads(EDITED))
    assert [c["message_ids"] for c in found] == [["a1", "a2"], ["a3", "b1", "b2"]]
    assert found[1]["start"] == MOMENTS["a3"]


def test_adding_keeps_what_is_there_and_does_not_add_twice(gold_path: Path) -> None:
    gold = load_gold(gold_path)
    found = changed_conversations(threads(ORIGINAL), threads(EDITED))
    once, added = add_conversations(gold, found[:1], original_source="o", edited_source="e")
    twice, added_again = add_conversations(once, found, original_source="o", edited_source="e")
    assert (added, added_again) == (1, 1)
    assert twice["conversations"][0] == once["conversations"][0]
    assert [c["message_ids"] for c in twice["conversations"]] == [["a1", "a2"], ["a3", "b1", "b2"]]
    assert twice["metadata"]["conversations"]["count"] == 2
    assert twice["windows"] == gold["windows"] and twice["messages"] == gold["messages"]


def test_a_re_edited_gold_conversation_stops_the_command(gold_path: Path) -> None:
    gold, _ = add_conversations(
        load_gold(gold_path),
        changed_conversations(threads(ORIGINAL), threads(EDITED)),
        original_source="o",
        edited_source="e",
    )
    regrouped = changed_conversations(threads(ORIGINAL), threads([["a1"], ["a2", "a3", "b1", "b2"]]))
    with pytest.raises(ValueError, match="re-edited"):
        add_conversations(gold, regrouped, original_source="o", edited_source="e")


def test_the_file_is_backed_up_its_windows_stay_and_freeze_add_keeps_the_part(
    gold_path: Path, tmp_path: Path
) -> None:
    before = gold_path.read_text(encoding="utf-8")
    original = _write(tmp_path / "original.json", ORIGINAL)
    edited = _write(tmp_path / "edited.json", EDITED)
    gold, added = add_conversations_to_file(gold_path, original, edited)
    assert added == 2 and load_gold(gold_path)["conversations"] == gold["conversations"]
    windows_and_labels = before[: before.index('"metadata"')]
    assert gold_path.read_text(encoding="utf-8").startswith(windows_and_labels)
    assert add_conversations_to_file(gold_path, original, edited)[1] == 0

    window = Window.from_dict({"start": "2025-01-02T09:00", "end": "2025-01-02T10:00"})
    refrozen = freeze_gold(threads(EDITED), [window], gold_path, tmp_path / "backups", "test", add=True)
    assert refrozen["conversations"] == gold["conversations"]
    assert len(refrozen["windows"]) == 2


def test_load_gold_rejects_a_message_in_two_conversations(gold_path: Path) -> None:
    gold = load_gold(gold_path)
    gold["conversations"] = [{"message_ids": ["a1"]}, {"message_ids": ["a1", "a2"]}]
    gold_path.write_text(json.dumps(gold), encoding="utf-8")
    with pytest.raises(ValueError, match="conversations part"):
        load_gold(gold_path)


def test_messages_outside_a_complete_conversation_count_against_precision() -> None:
    conversations = changed_conversations(threads(ORIGINAL), threads(EDITED))
    perfect = pooled_bcubed(conversation_counts(threads(EDITED), conversations))
    assert perfect["bcubed_f1"] == 1.0 and perfect["messages"] == 5
    # Step 1's split: a1 a2 sit with a3 (precision 2/3 each), a3 sits with them (1/3), b1 b2
    # lack a3 (recall 2/3 each), a3 lacks b1 b2 (recall 1/3).
    scored = pooled_bcubed(conversation_counts(threads(ORIGINAL), conversations))
    assert scored["bcubed_precision"] == pytest.approx((2 / 3 + 2 / 3 + 1 / 3 + 1 + 1) / 5)
    assert scored["bcubed_recall"] == pytest.approx((1 + 1 + 1 / 3 + 2 / 3 + 2 / 3) / 5)
    missing = conversation_counts([], conversations[:1])
    assert missing == [(2.0, 1.0, 2)]


def test_the_weaker_set_is_reviewed_and_unchanged() -> None:
    classified = [
        {"thread_id": "t-a1", "is_knowledge_bearing": False},
        {"thread_id": "t-c1", "is_knowledge_bearing": False},
        {"thread_id": "t-d1", "is_knowledge_bearing": True},
    ]
    weaker = reviewed_unchanged(threads(ORIGINAL), threads(EDITED), classified)
    assert [c["message_ids"] for c in weaker] == [["c1"]]


@pytest.fixture
def times_file(tmp_path: Path) -> Path:
    ranks = {"a1": 0, "a2": 1, "a3": 2, "b1": 3}
    path = tmp_path / "times.json"
    path.write_text(json.dumps({"messages": {k: {"timestamp_ms": 0, "rank": v} for k, v in ranks.items()}}))
    return path


def test_the_true_order_puts_a_minute_in_database_order(times_file: Path) -> None:
    export = sorted((m for t in threads(ORIGINAL) for m in t["messages"]), key=lambda m: m["datetime"])
    export.sort(key=lambda m: (m["datetime"], m["id"] != "b1"))  # the export put b1 first
    ordered, covered = true_order(export, times_file)
    assert [m["id"] for m in ordered] == ["a1", "a2", "a3", "b1", "b2", "c1", "d1", "d2"]
    assert covered == {"a1", "a2", "a3", "b1"}


def test_changed_outside_counts_only_untagged_reference_conversations() -> None:
    reference = threads(ORIGINAL)
    assert changed_outside(reference, threads(EDITED), tagged={"a1"}) == 1  # B changed
    assert changed_outside(reference, threads(EDITED), tagged={"a1", "b1"}) == 0
    merged = threads([["a1", "a2", "a3"], ["b1", "b2"], ["c1", "d1", "d2"]])
    assert changed_outside(reference, merged, tagged=set()) == 2


def test_each_fold_is_scored_with_the_row_best_on_the_other_folds() -> None:
    corrected = changed_conversations(threads(ORIGINAL), threads(EDITED))
    weaker = reviewed_unchanged(
        threads(ORIGINAL), threads(EDITED), [{"thread_id": "t-c1", "is_knowledge_bearing": False}]
    )
    owner = OwnerConversations(corrected, weaker)
    assert len(owner.folds) == 3  # three conversations, config asks for more folds
    grid = [owner.counts(threads(ORIGINAL)), owner.counts(threads(EDITED))]
    held = owner.held_out(grid)
    assert [fold["row"] for fold in held["folds"]] == [1, 1, 1]
    assert held["all"]["bcubed_f1"] == 1.0
    assert owner.covered({"a1", "a2", "c1"}).corrected == corrected[:1]


def test_the_owner_section_names_the_best_row_and_the_held_out_scores() -> None:
    owner = OwnerConversations(changed_conversations(threads(ORIGINAL), threads(EDITED)), [])

    def scores(clusters: list[list[str]]) -> dict[str, Any]:
        micro = {"micro": {"bcubed_f1": 0.5}}
        return {"window": micro, "context": micro, "owner": owner.counts(threads(clusters)),
                "changed_outside": 0}  # fmt: skip

    grid = [({"quiet_minutes": 10}, scores(ORIGINAL)), ({"quiet_minutes": 20}, scores(EDITED))]
    parts, data = owner_section(owner, grid, [("configured", scores(ORIGINAL))])
    assert data["rows"][1]["split"] == 'best on all: {"quiet_minutes": 20}'
    assert data["rows"][1]["corrected B3 F1"] == "1.000"
    assert [f["params"] for f in data["held_out"]["folds"]] == [{"quiet_minutes": 20}] * 2
    assert "held-out B3 F1 over all folds: corrected 1.000" in "\n".join(parts)


def test_cli_adds_the_conversations(
    gold_path: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    original = _write(tmp_path / "original.json", ORIGINAL)
    edited = _write(tmp_path / "edited.json", EDITED)
    caplog.set_level(logging.INFO)
    try:
        main(["--gold", str(gold_path), "--original", str(original), "--edited", str(edited)])
        assert len(load_gold(gold_path)["conversations"]) == 2
        assert "gold conversations" in caplog.text
    finally:
        handler = logging_setup._file_handler
        if handler is not None:
            logging.getLogger().removeHandler(handler)
            handler.close()
            logging_setup._file_handler = None
            logging_setup._current_step = None
