"""Step 10: what a newer export adds (new / changed / missing) and whether it continues the
pipeline input. Synthetic messages only."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from step_10_update.exports import (
    NotAContinuation,
    check_continuation,
    compare_exports,
    load_export,
    merged_input,
)


def message(msg_id: str, day: int, minute: int = 0, text: str | None = None, sender: str = "A",
            reactions: list | None = None, month: int = 3) -> dict:
    return {"id": msg_id, "date": f"{day:02d}/{month:02d}/2026", "time": f"10:{minute:02d}",
            "sender": {"user_name": sender, "phone": "0"}, "text": text or f"text {msg_id}",
            "quote": None, "reactions": reactions or []}


def chat(count: int) -> list[dict]:
    return [message(f"m{i}", 1, i) for i in range(count)]


def test_new_changed_and_missing_are_told_apart() -> None:
    old = chat(30)
    new = [dict(m) for m in old[2:]] + [message("n1", 9), message("n2", 9, 5)]
    new[0] = {**new[0], "text": "edited"}
    new[1] = {**new[1], "reactions": [{"emoji": "x", "senders": ["B"]}]}
    diff = compare_exports(old, new)
    assert diff.new == ("n1", "n2")
    assert diff.changed == ("m2", "m3")
    assert diff.missing == ("m0", "m1")
    assert diff.counts() == {"new": 2, "changed": 2, "missing": 2}
    check_continuation(diff)  # contains the last messages: a continuation


def test_an_identical_export_has_nothing_new() -> None:
    old = chat(25)
    diff = compare_exports(old, [dict(m) for m in old])
    assert diff.nothing_new and not diff.missing
    check_continuation(diff)


def test_a_later_period_starting_where_the_input_ends_is_a_continuation() -> None:
    old = chat(25)
    later = [message("x1", 2, 0), message("x2", 3, 0)]
    diff = compare_exports(old, later)
    assert not diff.overlap_found and len(diff.missing) == 25
    check_continuation(diff)


@pytest.mark.parametrize("case", ["older", "gap", "other_chat", "lacks_last"])
def test_an_export_that_does_not_continue_the_input_is_refused(case: str) -> None:
    old = chat(25)
    new = {
        "older": old[:10],
        "gap": [message("x1", 28, month=6)],
        "other_chat": [message("m24", 1, 24, sender="Z"), message("z9", 2)],
        "lacks_last": [*old[:3], message("z9", 5)],
    }[case]
    with pytest.raises(NotAContinuation, match="Nothing was changed"):
        check_continuation(compare_exports(old, new))


def test_missing_messages_stay_in_the_input_unless_asked() -> None:
    old = chat(5)
    new = [old[0], old[2], old[3], old[4], message("n1", 9)]
    kept = merged_input(old, new, drop_missing=False)
    assert [m["id"] for m in kept] == ["m0", "m1", "m2", "m3", "m4", "n1"]
    dropped = merged_input(old, new, drop_missing=True)
    assert [m["id"] for m in dropped] == ["m0", "m2", "m3", "m4", "n1"]
    assert merged_input(old, [*old, message("n1", 9)], drop_missing=False)[-1]["id"] == "n1"


def test_load_export_refuses_what_is_not_an_export(tmp_path: Path) -> None:
    path = tmp_path / "export.json"
    path.write_text(json.dumps({"messages": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="non-empty JSON list"):
        load_export(path)
    path.write_text(json.dumps([{"id": "1"}]), encoding="utf-8")
    with pytest.raises(ValueError, match="no id / date / time"):
        load_export(path)
