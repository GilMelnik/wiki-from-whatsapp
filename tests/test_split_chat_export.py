from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from utils.split_chat_export import newest_export, split_export

CUTOFF = datetime(2026, 9, 1)


def _message(msg_id: str, date: str, time: str, **extra: object) -> dict[str, object]:
    return {
        "id": msg_id,
        "date": date,
        "time": time,
        "sender": {"user_name": "A", "phone": "000"},
        "text": f"text {msg_id}",
        "is_outgoing": False,
        "quote": None,
        "reactions": [],
        **extra,
    }


@pytest.fixture
def export_path(tmp_path: Path) -> Path:
    messages = [
        _message("m1", "30/08/2026", "10:00"),
        _message("m2", "31/08/2026", "23:59", quote={"sender": "B", "text": "q"}),
        _message("m3", "01/09/2026", "00:00"),
        _message("m4", "01/09/2026", "08:30", reactions=[{"emoji": "x", "count": 1, "senders": ["B"]}]),
        _message("m5", "02/10/2026", "12:00"),
    ]
    path = tmp_path / "2026_11_07_chat_android.json"
    path.write_text(json.dumps(messages, ensure_ascii=False), encoding="utf-8")
    return path


def test_split_keeps_messages_before_cutoff_in_order(export_path: Path, tmp_path: Path) -> None:
    output = tmp_path / "out" / "chat_android.json"

    assert split_export(export_path, CUTOFF, output) == (2, 3)

    kept = json.loads(output.read_text(encoding="utf-8"))
    assert [m["id"] for m in kept] == ["m1", "m2"]


def test_split_preserves_raw_dicts(tmp_path: Path) -> None:
    original = [
        _message("m1", "01/08/2026", "09:00", quote={"sender": "B", "text": "שלום"},
                 reactions=[{"emoji": "x", "count": 2, "senders": ["B", "C"]}], media_type="image"),
        _message("m2", "05/09/2026", "09:00"),
    ]
    export = tmp_path / "export.json"
    export.write_text(json.dumps(original, ensure_ascii=False), encoding="utf-8")
    output = tmp_path / "chat_android.json"

    split_export(export, CUTOFF, output)

    assert json.loads(output.read_text(encoding="utf-8")) == original[:1]
    assert "שלום" in output.read_text(encoding="utf-8")


def test_split_refuses_to_overwrite_without_force(export_path: Path, tmp_path: Path) -> None:
    output = tmp_path / "chat_android.json"
    output.write_text("[]", encoding="utf-8")

    with pytest.raises(FileExistsError):
        split_export(export_path, CUTOFF, output)
    assert output.read_text(encoding="utf-8") == "[]"

    assert split_export(export_path, CUTOFF, output, force=True) == (2, 3)
    assert len(json.loads(output.read_text(encoding="utf-8"))) == 2


def test_split_rejects_non_list_export(tmp_path: Path) -> None:
    export = tmp_path / "export.json"
    export.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="JSON list"):
        split_export(export, CUTOFF, tmp_path / "out.json")


def test_newest_export_picks_latest_stamp_and_ignores_other_files(tmp_path: Path) -> None:
    for name in ("2026_01_01_chat_android.json", "2026_11_07_chat_android.json", "2026_11_07_new_messages.json"):
        (tmp_path / name).write_text("[]", encoding="utf-8")

    assert newest_export(tmp_path, "chat_android.json").name == "2026_11_07_chat_android.json"
    with pytest.raises(FileNotFoundError):
        newest_export(tmp_path / "missing", "chat_android.json")
