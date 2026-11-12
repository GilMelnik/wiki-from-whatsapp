"""What step 3 sends: the thoroughness note above a long conversation (fresh: all its
messages, update: the new ones), the rest of the prompt as before, numbered claims in both
schemas. Synthetic data only, no model.
"""

from __future__ import annotations

import pytest

from step_3_extract.claims import ThreadClaims
from step_3_extract.prompt import (
    EXTRACT_CONFIG,
    EXTRACT_SYSTEM,
    FRESH_SCHEMA,
    THOROUGH_NOTE,
    THOROUGH_UPDATE_NOTE,
    UPDATE_SCHEMA,
)

# The note as task 52 tested it on paid calls (variant G): it must not drift.
TESTED_NOTE = (
    "This conversation is long: {count} messages. Work through it from the first message to "
    "the last and extract every claim it holds. There is no limit on the number of claims: it "
    "grows with the length of the conversation, so do not stop after the main points and do "
    "not summarise. A detail that one member mentions once (a price, a provider, a duration, "
    "a document, a tip, an experience) is a claim of its own when another member could act on "
    "it. What to skip is unchanged."
)


def _thread(size: int) -> dict:
    """``size`` messages with content plus one empty message (never shown, never counted)."""
    messages = [{"id": f"W{i}", "datetime": f"2024-01-01T10:{i:02d}:00", "sender": f"p{i % 3}",
                 "content": f"synthetic text {i}"} for i in range(size)]
    messages.insert(1, {"id": "EMPTY", "datetime": "2024-01-01T10:00:30", "sender": "p1",
                        "content": " "})
    return {"thread_id": "t-W0", "messages": messages}


def _existing() -> list[dict]:
    return [{"claim_id": "t-W0-old", "claim_text": "an existing claim"}]


def test_the_note_keeps_its_tested_wording_and_update_changes_only_its_first_sentence() -> None:
    assert THOROUGH_NOTE == TESTED_NOTE
    fresh_first, rest = THOROUGH_NOTE.split(". ", 1)
    update_first, update_rest = THOROUGH_UPDATE_NOTE.split(". ", 1)
    assert update_rest == rest and fresh_first != update_first
    assert update_first == "The new part of this conversation is long: {count} new messages"
    assert "long:" not in EXTRACT_SYSTEM and "Work through it" not in EXTRACT_SYSTEM


@pytest.mark.parametrize("size", [30, 31])
def test_fresh_the_note_is_above_more_than_long_messages(size: int) -> None:
    assert int(EXTRACT_CONFIG["long_messages"]) == 30
    job = ThreadClaims(_thread(size))
    prompt = job.prompt([])
    today = f"Conversation:\n{job.rendered}"  # the prompt as step 3 sent it before the note
    assert len(job.refs) == size  # the empty message is not counted
    if size == 30:
        assert prompt == today
    else:
        assert prompt == f"{THOROUGH_NOTE.format(count=31)}\n\n{today}"
        assert prompt.startswith("This conversation is long: 31 messages. Work through")


@pytest.mark.parametrize(("size", "new", "noted"), [(60, 30, False), (60, 31, True),
                                                     (31, 1, False)])
def test_update_the_note_counts_only_the_new_messages(size: int, new: int, noted: bool) -> None:
    thread = _thread(size)
    new_ids = frozenset(m["id"] for m in thread["messages"][-new:]) | {"EMPTY"}
    job = ThreadClaims(thread, new_ids)
    prompt = job.prompt(_existing())
    today = f"Existing claims:\nC1: an existing claim\n\nConversation:\n{job.rendered}"
    if noted:
        assert prompt == f"{THOROUGH_UPDATE_NOTE.format(count=new)}\n\n{today}"
        assert prompt.startswith(f"The new part of this conversation is long: {new} new messages.")
    else:
        assert prompt == today  # 31 messages in all, 1 new: no note in update mode


def test_both_schemas_ask_for_n_first_and_the_prompt_explains_it() -> None:
    for schema in (FRESH_SCHEMA, UPDATE_SCHEMA):
        item = schema["properties"]["claims"]["items"]
        assert list(item["properties"])[0] == "n" and item["required"][0] == "n"
        assert item["properties"]["n"] == {"type": "integer"}
    assert "- n: the claim's number in your answer: 1 for the first claim you write" in (
        EXTRACT_SYSTEM)
    assert "- counters: the n of each claim of your answer that this claim disputes" in (
        EXTRACT_SYSTEM)
    assert "the disputed claim's n in its counters" in EXTRACT_SYSTEM
    assert "by their number in your list" not in EXTRACT_SYSTEM
    assert "existing" in UPDATE_SCHEMA["properties"] and "claim" in (
        UPDATE_SCHEMA["properties"]["existing"]["items"]["properties"])  # still C-numbers
