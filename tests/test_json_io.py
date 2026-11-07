from __future__ import annotations

import json
from pathlib import Path

import pytest

from utils.json_io import write_json_file


def test_round_trip_creates_parents_and_leaves_no_temp(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "dir" / "x.json"
    payload = {"a": "שלום", "n": [1, 2]}

    write_json_file(payload, target)

    text = target.read_text(encoding="utf-8")
    assert "שלום" in text
    assert text == json.dumps(payload, ensure_ascii=False, indent=2)
    assert json.loads(text) == payload
    assert [p.name for p in target.parent.iterdir()] == ["x.json"]


def test_failed_overwrite_keeps_previous_content(tmp_path: Path) -> None:
    target = tmp_path / "x.json"
    write_json_file({"old": 1}, target)

    with pytest.raises(TypeError):
        write_json_file({"bad": object()}, target)

    assert json.loads(target.read_text(encoding="utf-8")) == {"old": 1}
    assert [p.name for p in tmp_path.iterdir()] == ["x.json"]
