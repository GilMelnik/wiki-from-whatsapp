"""Gold windows, the frozen gold file, the metrics and the window suggester (synthetic data)."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from step_1_threads_split import evaluate as evaluate_module
from step_1_threads_split.evaluate import evaluate, main
from step_1_threads_split.gold import Window, WindowSet, WindowSuggester, freeze_gold, load_gold
from utils import logging_setup
from utils.config import section

W1 = {"start": "2025-01-01T10:00", "end": "2025-01-01T12:00"}
W2 = {"start": "2025-01-02T10:00", "end": "2025-01-02T12:00"}
WINDOWS = [Window.from_dict(W1), Window.from_dict(W2)]
SCORES = [
    "pairwise_precision", "pairwise_recall", "pairwise_f1",
    "bcubed_precision", "bcubed_recall", "bcubed_f1",
]  # fmt: skip

# a* and b* are two interleaved conversations in W1, c* and d1 sit in W2, o* are outside.
MOMENTS = {
    "o1": "2025-01-01T09:50:00",
    "a1": "2025-01-01T10:00:00",  # exactly at the window start: inside
    "b1": "2025-01-01T10:01:00",
    "a2": "2025-01-01T10:02:00",
    "b2": "2025-01-01T10:03:00",
    "a3": "2025-01-01T10:30:00",
    "b3": "2025-01-01T11:00:00",
    "a4": "2025-01-01T11:59:00",
    "o2": "2025-01-01T12:00:00",  # exactly at the window end: outside
    "c1": "2025-01-02T10:10:00",
    "c2": "2025-01-02T10:11:00",
    "d1": "2025-01-02T10:12:00",
    "c3": "2025-01-02T10:13:00",
}
TAGGED = [["o1", "a1", "a2", "a3", "a4"], ["b1", "b2", "b3", "o2"], ["c1", "c2", "c3"], ["d1"]]
W1_IDS = ["a1", "b1", "a2", "b2", "a3", "b3", "a4"]
W2_IDS = ["c1", "c2", "d1", "c3"]
ONE_THREAD = [list(MOMENTS)]
ALL_ALONE = [[wa_id] for wa_id in MOMENTS]


def _threads(clusters: list[list[str]], shape: str = "v1") -> list[dict[str, Any]]:
    """Threads in the old shape (positional ids) or the C1 shape (WhatsApp ids)."""
    positions = {wa_id: position for position, wa_id in enumerate(MOMENTS)}
    threads = []
    for number, cluster in enumerate(clusters, start=1):
        messages = [
            {"id": wa_id, "datetime": MOMENTS[wa_id], "sender": "s", "content": "c"}
            for wa_id in cluster
        ]
        if shape == "v1":
            thread_id = f"thread-{number:04d}"
            message_ids: list[Any] = [positions[wa_id] for wa_id in cluster]
        else:
            thread_id = f"t-{cluster[0]}"
            message_ids = list(cluster)
        threads.append(
            {
                "thread_id": thread_id,
                "start_time": messages[0]["datetime"],
                "last_time": messages[-1]["datetime"],
                "message_ids": message_ids,
                "messages": messages,
            }
        )
    return threads


@pytest.fixture
def gold(tmp_path: Path) -> dict[str, Any]:
    return freeze_gold(
        _threads(TAGGED), WINDOWS, tmp_path / "gold.json", tmp_path / "backups", "synthetic"
    )


# -------------------------------------------------------------------- windows
def test_window_is_half_open_and_rejects_bad_input() -> None:
    window = Window.from_dict(W1)
    assert window.contains(datetime(2025, 1, 1, 10, 0))
    assert not window.contains(datetime(2025, 1, 1, 12, 0))
    assert window.to_dict() == W1
    with pytest.raises(ValueError, match="bad gold window"):
        Window.from_dict({"start": "2025-01-01T10:00"})
    with pytest.raises(ValueError, match="bad gold window"):
        Window.from_dict({"start": "yesterday", "end": "today"})
    with pytest.raises(ValueError, match="must end after it starts"):
        Window.from_dict({"start": W1["end"], "end": W1["start"]})


def test_window_set_rejects_overlap_and_finds_the_window() -> None:
    window_set = WindowSet(reversed(WINDOWS))
    assert window_set.windows == WINDOWS
    assert window_set.index_of(datetime(2025, 1, 2, 11, 0)) == 1
    assert window_set.index_of(datetime(2025, 1, 1, 12, 0)) is None
    overlapping = Window.from_dict({"start": "2025-01-01T11:00", "end": "2025-01-01T13:00"})
    with pytest.raises(ValueError, match="overlap"):
        WindowSet([*WINDOWS, overlapping])


# -------------------------------------------------------------------- metrics
def test_perfect_prediction_scores_one(gold: dict[str, Any]) -> None:
    result = evaluate(_threads(TAGGED), gold)
    for row in [*result["windows"], result["micro"]]:
        assert [row[score] for score in SCORES] == [1.0] * len(SCORES)
        assert row["pred_clusters"] == row["gold_clusters"]
        assert row["missing_in_pred"] == row["unlabelled_in_pred"] == 0
    assert [row["messages"] for row in result["windows"]] == [7, 4]
    assert [row["gold_clusters"] for row in result["windows"]] == [2, 2]
    assert result["micro"]["messages"] == 11
    assert result["micro"]["gold_clusters"] == 4


def test_everything_in_one_thread_has_full_recall_and_low_precision(gold: dict[str, Any]) -> None:
    result = evaluate(_threads(ONE_THREAD), gold)
    first, second = result["windows"]
    assert first["pairwise_recall"] == second["pairwise_recall"] == 1.0
    assert first["pairwise_precision"] == pytest.approx(9 / 21)
    assert second["pairwise_precision"] == pytest.approx(3 / 6)
    assert first["bcubed_precision"] == pytest.approx(25 / 49)
    assert first["bcubed_recall"] == 1.0
    assert [row["pred_clusters"] for row in result["windows"]] == [1, 1]
    micro = result["micro"]
    assert micro["pairwise_precision"] == pytest.approx(12 / 27)
    assert micro["pairwise_recall"] == 1.0
    assert micro["pairwise_f1"] == pytest.approx(8 / 13)
    assert micro["bcubed_precision"] == pytest.approx((25 / 7 + 10 / 4) / 11)
    assert micro["bcubed_recall"] == 1.0


def test_every_message_alone_has_full_precision_and_no_pairwise_recall(
    gold: dict[str, Any],
) -> None:
    result = evaluate(_threads(ALL_ALONE), gold)
    for row in [*result["windows"], result["micro"]]:
        assert row["pairwise_precision"] == 1.0
        assert row["pairwise_recall"] == 0.0
        assert row["pairwise_f1"] == 0.0
        assert row["bcubed_precision"] == 1.0
    assert result["windows"][0]["bcubed_recall"] == pytest.approx(2 / 7)
    assert result["micro"]["bcubed_recall"] == pytest.approx(4 / 11)
    assert result["micro"]["pred_clusters"] == 11


def test_messages_outside_windows_are_ignored(gold: dict[str, Any]) -> None:
    moved = [["a1", "a2", "a3", "a4"], ["o1", "b1", "b2", "b3"], ["o2", "c1", "c2", "c3"], ["d1"]]
    assert evaluate(_threads(moved), gold) == evaluate(_threads(TAGGED), gold)


def test_old_and_c1_shaped_predictions_score_the_same(gold: dict[str, Any]) -> None:
    mixed = [["o1", "a1", "b1", "a2"], ["b2", "a3", "b3"], ["a4", "o2", "c1"], ["c2", "d1", "c3"]]
    old_shape = evaluate(_threads(mixed, shape="v1"), gold)
    c1_shape = evaluate(list(reversed(_threads(mixed, shape="c1"))), gold)
    assert old_shape == c1_shape
    assert 0.0 < old_shape["micro"]["pairwise_f1"] < 1.0


def test_gold_message_missing_from_prediction_is_its_own_thread(gold: dict[str, Any]) -> None:
    without_a4 = [["o1", "a1", "a2", "a3"], ["b1", "b2", "b3", "o2"], ["c1", "c2", "c3"], ["d1"]]
    first = evaluate(_threads(without_a4), gold)["windows"][0]
    assert first["missing_in_pred"] == 1
    assert first["messages"] == 7
    assert first["pred_clusters"] == 3
    assert first["pairwise_precision"] == 1.0
    assert first["pairwise_recall"] == pytest.approx(6 / 9)


def test_unlabelled_message_inside_a_window_is_counted_and_ignored(gold: dict[str, Any]) -> None:
    threads = _threads(TAGGED)
    threads[0]["messages"].append(
        {"id": "new", "datetime": "2025-01-01T10:45:00", "sender": "s", "content": "c"}
    )
    result = evaluate(threads, gold)
    assert result["windows"][0]["unlabelled_in_pred"] == 1
    assert result["windows"][0]["messages"] == 7
    assert result["micro"]["pairwise_f1"] == 1.0


def test_message_in_two_predicted_threads_is_rejected(gold: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="more than one predicted thread"):
        evaluate(_threads([*TAGGED, ["a1"]]), gold)


# ------------------------------------------------------------------ gold file
def test_freeze_then_evaluate_round_trip(tmp_path: Path) -> None:
    gold_path = tmp_path / "step" / "gold.json"
    gold = freeze_gold(_threads(TAGGED), WINDOWS, gold_path, tmp_path / "backups", "synthetic")
    text = gold_path.read_text(encoding="utf-8")
    assert load_gold(gold_path) == gold == json.loads(text)
    assert text.startswith('{\n  "windows"')
    assert "thread-" not in text
    assert list(gold["messages"]) == [*W1_IDS, *W2_IDS]
    assert gold["messages"]["a4"] == "a1"
    assert gold["messages"]["b3"] == "b1"
    assert gold["messages"]["d1"] == "d1"
    assert [window["message_ids"] for window in gold["windows"]] == [W1_IDS, W2_IDS]
    metadata = gold["metadata"]
    assert (metadata["source"], metadata["message_count"], metadata["cluster_count"]) == (
        "synthetic",
        11,
        4,
    )
    rescored = evaluate(_threads(TAGGED, shape="c1"), load_gold(gold_path))
    assert rescored["micro"]["bcubed_f1"] == 1.0
    assert not (tmp_path / "backups").exists()


def test_freeze_refuses_to_overwrite_without_force(tmp_path: Path) -> None:
    gold_path = tmp_path / "gold.json"
    backups = tmp_path / "backups"
    freeze_gold(_threads(TAGGED), WINDOWS, gold_path, backups, "first")
    before = gold_path.read_text(encoding="utf-8")
    with pytest.raises(FileExistsError, match="--add"):
        freeze_gold(_threads(ALL_ALONE), WINDOWS, gold_path, backups, "second")
    assert gold_path.read_text(encoding="utf-8") == before

    rebuilt = freeze_gold(_threads(ALL_ALONE), WINDOWS, gold_path, backups, "second", force=True)
    assert rebuilt["metadata"]["cluster_count"] == 11
    assert [path.read_text(encoding="utf-8") for path in backups.iterdir()] == [before]


def test_add_merges_new_windows_and_keeps_frozen_ones(tmp_path: Path) -> None:
    gold_path = tmp_path / "gold.json"
    backups = tmp_path / "backups"
    first = freeze_gold(_threads(TAGGED), WINDOWS[:1], gold_path, backups, "first")
    assert list(first["messages"]) == W1_IDS

    # Later the tagged file no longer holds the W1 tagging; W1 must stay as frozen.
    merged = freeze_gold(_threads(ALL_ALONE), WINDOWS, gold_path, backups, "second", add=True)
    assert merged["windows"][0] == first["windows"][0]
    assert {wa_id: merged["messages"][wa_id] for wa_id in first["messages"]} == first["messages"]
    assert [merged["messages"][wa_id] for wa_id in W2_IDS] == W2_IDS
    assert merged["metadata"]["message_count"] == 11
    assert merged["metadata"]["cluster_count"] == 6
    assert merged["metadata"]["source"] == "second"
    assert load_gold(gold_path) == merged
    assert len(list(backups.iterdir())) == 1

    unchanged = freeze_gold(_threads(ONE_THREAD), WINDOWS, gold_path, backups, "third", add=True)
    assert unchanged == merged
    assert len(list(backups.iterdir())) == 1


def test_add_rejects_a_window_overlapping_a_frozen_one(tmp_path: Path) -> None:
    gold_path = tmp_path / "gold.json"
    freeze_gold(_threads(TAGGED), WINDOWS[:1], gold_path, tmp_path / "backups", "first")
    shifted = Window.from_dict({"start": "2025-01-01T11:00", "end": "2025-01-01T13:00"})
    with pytest.raises(ValueError, match="overlap"):
        freeze_gold(_threads(TAGGED), [shifted], gold_path, tmp_path / "backups", "x", add=True)


def test_freeze_rejects_unusable_input(tmp_path: Path) -> None:
    gold_path = tmp_path / "gold.json"
    backups = tmp_path / "backups"
    with pytest.raises(ValueError, match="no gold windows"):
        freeze_gold(_threads(TAGGED), [], gold_path, backups, "x")
    empty = Window.from_dict({"start": "2025-03-01T10:00", "end": "2025-03-01T12:00"})
    with pytest.raises(ValueError, match="contains no messages"):
        freeze_gold(_threads(TAGGED), [empty], gold_path, backups, "x")
    threads = _threads(TAGGED)
    threads[0]["messages"][1]["id"] = None
    with pytest.raises(ValueError, match="no WhatsApp id"):
        freeze_gold(threads, WINDOWS, gold_path, backups, "x")
    with pytest.raises(ValueError, match="more than once"):
        freeze_gold(_threads([*TAGGED, ["a1"]]), WINDOWS, gold_path, backups, "x")
    assert not gold_path.exists()


def test_load_gold_validates_the_file(tmp_path: Path, gold: dict[str, Any]) -> None:
    with pytest.raises(FileNotFoundError, match="--freeze-gold"):
        load_gold(tmp_path / "missing.json")
    del gold["messages"]["a1"]
    broken = tmp_path / "broken.json"
    broken.write_text(json.dumps(gold), encoding="utf-8")
    with pytest.raises(ValueError, match="inconsistent"):
        load_gold(broken)


# ------------------------------------------------------------------ suggester
def _busy_days(day_count: int, mixed_days: set[int]) -> list[dict[str, Any]]:
    """One three-hour burst every 30 days; on mixed days two threads talk at once."""
    threads = []
    for day in range(day_count):
        burst_start = datetime(2024, 1, 5, 20, 0) + timedelta(days=30 * day)
        moments = [(burst_start + timedelta(minutes=10 * step)).isoformat() for step in range(18)]
        parts = [moments[0::2], moments[1::2]] if day in mixed_days else [moments]
        for part_number, part in enumerate(parts):
            threads.append(
                {
                    "thread_id": f"thread-{day}-{part_number}",
                    "messages": [
                        {"id": f"m-{day}-{part_number}-{index}", "datetime": moment}
                        for index, moment in enumerate(part)
                    ],
                }
            )
    return threads


def _suggester(threads: list[dict[str, Any]], exclude: tuple[Window, ...] = ()) -> WindowSuggester:
    return WindowSuggester(
        threads, hours=2, min_messages=5, max_messages=50, min_threads=2, exclude=exclude
    )


def test_suggester_is_deterministic_for_a_seed() -> None:
    threads = _busy_days(12, mixed_days=set(range(12)))
    assert _suggester(threads).suggest(4, seed=7) == _suggester(threads).suggest(4, seed=7)
    outcomes = {
        tuple(window["start"] for window in _suggester(threads).suggest(4, seed=seed))
        for seed in range(10)
    }
    assert len(outcomes) > 1


def test_suggested_windows_do_not_overlap_and_respect_the_bounds() -> None:
    threads = _busy_days(3, mixed_days={0, 1, 2})
    for seed in range(5):
        suggestions = _suggester(threads).suggest(5, seed=seed)
        spans = [Window.from_dict(suggestion) for suggestion in suggestions]
        assert len(spans) >= 3
        assert WindowSet(spans).windows == spans
        for suggestion, span in zip(suggestions, spans):
            assert span.end - span.start == timedelta(hours=2)
            assert span.start.minute == 0
            assert 5 <= suggestion["message_count"] <= 50


def test_suggester_spreads_over_the_timeline_and_prefers_several_threads() -> None:
    mixed_days = {1, 2, 5, 6, 9, 10}
    threads = _busy_days(12, mixed_days)
    for seed in range(5):
        suggestions = _suggester(threads).suggest(4, seed=seed)
        days = [int(s["threads"][0]["thread_id"].split("-")[1]) for s in suggestions]
        assert [day // 3 for day in days] == [0, 1, 2, 3]
        assert set(days) <= mixed_days
        assert all(suggestion["thread_count"] == 2 for suggestion in suggestions)


def test_suggestion_lists_the_overlapping_threads() -> None:
    suggestion = _suggester(_busy_days(1, mixed_days={0})).suggest(1, seed=0)[0]
    assert suggestion["thread_count"] == len(suggestion["threads"]) == 2
    in_window = [thread["messages_in_window"] for thread in suggestion["threads"]]
    assert suggestion["message_count"] == sum(in_window)
    assert suggestion["threads"][0] == {
        "thread_id": "thread-0-0",
        "start_time": "2024-01-05T20:00:00",
        "messages_in_window": in_window[0],
        "num_messages": 9,
    }


def test_suggester_skips_windows_that_are_already_taken() -> None:
    threads = _busy_days(2, mixed_days={0, 1})
    taken = Window(datetime(2024, 1, 5, 0, 0), datetime(2024, 1, 6, 0, 0))
    suggestions = _suggester(threads, exclude=(taken,)).suggest(4, seed=0)
    assert suggestions
    assert all(suggestion["start"].startswith("2024-02-04") for suggestion in suggestions)
    assert _suggester([]).suggest(3, seed=0) == []
    with pytest.raises(ValueError, match="at least one"):
        _suggester(threads).suggest(0, seed=0)


# ------------------------------------------------------------------------ CLI
@pytest.fixture
def cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point the CLI's files and its step log at ``tmp_path``; returns the data folder."""
    data = tmp_path / "data"
    monkeypatch.setattr(logging_setup, "DATA_DIR", data)
    monkeypatch.setattr(logging_setup, "_current_step", None)
    monkeypatch.setattr(logging_setup, "_file_handler", None)
    monkeypatch.setattr(evaluate_module, "ORIGINAL_THREADS_PATH", data / "threads.json")
    monkeypatch.setattr(evaluate_module, "EDITED_THREADS_PATH", data / "threads_edited.json")
    monkeypatch.setattr(evaluate_module, "BACKUPS_DIR", data / "backups")
    monkeypatch.setitem(evaluate_module.THREADS_CONFIG, "gold_windows", [W1, W2])
    monkeypatch.setitem(
        evaluate_module.THREADS_CONFIG,
        "gold_suggest",
        {
            "windows": 2,
            "hours": 2,
            "seed": 0,
            "min_messages": 5,
            "max_messages": 50,
            "min_threads": 2,
        },
    )
    data.mkdir()
    yield data
    handler = logging_setup._file_handler
    if handler is not None:
        logging.getLogger().removeHandler(handler)
        handler.close()


def _write_threads(path: Path, threads: list[dict[str, Any]]) -> Path:
    path.write_text(json.dumps({"threads": threads, "metadata": {}}), encoding="utf-8")
    return path


def test_cli_freezes_then_scores(cli: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    gold_path = cli / "gold.json"
    w1_lumped = [["o1", "a1", "a2", "a3", "a4", "b1", "b2", "b3", "o2"], ["c1", "c2", "c3"], ["d1"]]
    original = _write_threads(cli / "threads.json", _threads(w1_lumped))
    _write_threads(cli / "threads_edited.json", _threads(TAGGED))

    main(["--freeze-gold", "--gold", str(gold_path)])
    assert load_gold(gold_path)["metadata"]["message_count"] == 11
    assert f"| {W1['start']} → {W1['end']} |" in caplog.text
    assert "| **total** |  | 11 | 4 |" in caplog.text
    untouched = [record.message for record in caplog.records if "really tagged" in record.message]
    assert len(untouched) == 1
    assert W2["start"] in untouched[0]

    with pytest.raises(FileExistsError):
        main(["--freeze-gold", "--gold", str(gold_path)])

    caplog.clear()
    main(["--pred", str(original), "--gold", str(gold_path)])
    assert "| window | messages | pred threads | gold threads | pair P |" in caplog.text
    assert f"| {W1['start']} → {W1['end']} | 7 | 1 | 2 | 0.429 | 1.000 | 0.600 |" in caplog.text
    assert "| **micro** | 11 | 3 | 4 |" in caplog.text
    log_file = cli / "step_1_threads_split" / "step_1_threads_split.log"
    assert "| **micro** |" in log_file.read_text(encoding="utf-8")


def test_cli_add_warns_only_about_new_untouched_windows(
    cli: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    gold_path = cli / "gold.json"
    _write_threads(cli / "threads.json", _threads(TAGGED))
    _write_threads(cli / "threads_edited.json", _threads(TAGGED))

    monkeypatch.setitem(evaluate_module.THREADS_CONFIG, "gold_windows", [W1])
    main(["--freeze-gold", "--gold", str(gold_path)])
    caplog.clear()
    monkeypatch.setitem(evaluate_module.THREADS_CONFIG, "gold_windows", [W1, W2])
    main(["--freeze-gold", "--add", "--gold", str(gold_path)])

    untouched = [record.message for record in caplog.records if "really tagged" in record.message]
    assert len(untouched) == 1
    assert W2["start"] in untouched[0]
    assert [window["start"] for window in load_gold(gold_path)["windows"]] == [
        W1["start"],
        W2["start"],
    ]
    assert len(list((cli / "backups").iterdir())) == 1


def test_cli_suggests_windows(cli: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    pred = _write_threads(cli / "pred.json", _busy_days(4, mixed_days={0, 1, 2, 3}))
    main(["--suggest-windows", "--pred", str(pred), "--gold", str(cli / "none.json")])
    assert "2 proposed windows" in caplog.text
    assert "### Window 2:" in caplog.text
    assert "| thread id | thread starts | messages in window | messages in thread |" in caplog.text
    assert '"gold_windows": [' in caplog.text


def test_cli_rejects_flag_mixups(cli: Path) -> None:
    mixups = (
        ["--add"],
        ["--freeze-gold", "--add", "--force"],
        ["--freeze-gold", "--suggest-windows"],
    )
    for argv in mixups:
        with pytest.raises(SystemExit):
            main(argv)
    with pytest.raises(FileNotFoundError, match="thread tagger"):
        main(["--freeze-gold", "--gold", str(cli / "gold.json")])


def test_config_has_the_suggester_defaults() -> None:
    threads_config = section("threads")
    assert isinstance(threads_config["gold_windows"], list)
    assert set(threads_config["gold_suggest"]) == {
        "windows", "hours", "seed", "min_messages", "max_messages", "min_threads",
    }  # fmt: skip
