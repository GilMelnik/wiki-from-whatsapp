"""Rendering a thread for the extract model and joining quotes / reactions from the export.

Synthetic data only.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from utils.threads_io import load_export_index, render_thread_for_llm, with_export

ALICE = "972500000001@s.whatsapp.net"
BOB = "972500000002@s.whatsapp.net"
CAROL = "972500000003@s.whatsapp.net"


def _thread() -> dict:
    return {
        "thread_id": "t-W1",
        "messages": [
            {"id": "W1", "datetime": "2024-01-05T10:00:00", "sender": ALICE, "content": "first"},
            {"id": "W2", "datetime": "2024-01-06T11:00:00", "sender": BOB, "content": "second"},
            {"id": "W3", "datetime": "2024-01-07T12:00:00", "sender": ALICE, "content": ""},
            {"id": "W4", "datetime": "2024-02-01T09:00:00", "sender": CAROL, "content": "third"},
        ],
    }


def _export_index(tmp_path: Path) -> dict:
    export = [
        {
            "id": "W2",
            "sender": {"user_name": BOB, "phone": ""},
            "quote": {"sender": {"user_name": ALICE, "phone": ""}, "text": "first"},
            "reactions": [
                {"emoji": "👍", "count": 2, "senders": [{"user_name": ALICE}, {"user_name": CAROL}]}
            ],
        },
        {
            "id": "W4",
            "quote": {"sender": {"user_name": "972500000009@s.whatsapp.net"}, "text": "x" * 500},
            "reactions": [],
        },
    ]
    path = tmp_path / "export.json"
    path.write_text(json.dumps(export), encoding="utf-8")
    return load_export_index(path)


def test_numbering_aliases_dates_and_refs(tmp_path: Path) -> None:
    text, refs = render_thread_for_llm(with_export(_thread(), _export_index(tmp_path)))
    lines = text.splitlines()
    assert refs == ["W1", "W2", "W4"]  # the empty message is not shown or numbered
    assert lines[0] == "[1] 2024-01-05 משתתף 1: first"
    assert lines[1].startswith("[2] 2024-01-06 משתתף 2 ↩[1]: second")
    assert lines[2].startswith("[3] 2024-02-01 משתתף 3 ")


def test_quote_and_reactions_shown_without_identities(tmp_path: Path) -> None:
    text, _ = render_thread_for_llm(with_export(_thread(), _export_index(tmp_path)))
    lines = text.splitlines()
    assert lines[1].endswith("[תגובות: 👍×2]")
    # A quote of a message outside the thread: aliased sender, shortened text.
    assert "↩«משתתף 4: " in lines[2]
    assert "x" * 161 not in lines[2] and "…»" in lines[2]
    for identity in ("9725000000", "whatsapp.net"):
        assert identity not in text


def test_new_messages_are_marked(tmp_path: Path) -> None:
    thread = with_export(_thread(), _export_index(tmp_path))
    text, _ = render_thread_for_llm(thread, new_ids=frozenset({"W4"}))
    lines = text.splitlines()
    assert "(חדשה)" in lines[2]
    assert "(חדשה)" not in lines[0] + lines[1]


def test_inline_quote_and_reactions_win_over_the_export(tmp_path: Path) -> None:
    thread = _thread()
    thread["messages"][1]["reactions"] = [{"emoji": "❤️", "senders": [CAROL]}]
    thread["messages"][1]["quote"] = None
    messages = with_export(thread, _export_index(tmp_path))["messages"]
    assert messages[1]["reactions"] == [{"emoji": "❤️", "senders": [CAROL]}]
    assert messages[1]["quote"] is None
    assert messages[0]["reactions"] == [] and messages[0]["quote"] is None


def test_export_reaction_senders_become_identity_strings(tmp_path: Path) -> None:
    messages = with_export(_thread(), _export_index(tmp_path))["messages"]
    assert messages[1]["reactions"] == [{"emoji": "👍", "senders": sorted([ALICE, CAROL])}]
    assert messages[1]["quote"] == {"sender": ALICE, "text": "first"}


def test_missing_export_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="Chat export not found"):
        load_export_index(tmp_path / "nope.json")
