"""Model answer -> C3 claim records: number mapping, ids, dedupe, grounding, validation.

Synthetic data only.
"""

from __future__ import annotations

import pytest

from step_3_extract.claims import (
    DERIVED_KEYS,
    OutputError,
    ThreadClaims,
    claim_id_for,
    input_fingerprint,
    message_fingerprints,
)

C3_KEYS = [
    "claim_id",
    "thread_id",
    "claim_text",
    "entities",
    "stance",
    "supporting_message_ids",
    "opposing_message_ids",
    "first_date",
    "last_date",
    "supporter_count",
    "opposer_count",
    "reaction_summary",
]


def _thread() -> dict:
    return {
        "thread_id": "t-A",
        "messages": [
            {"id": "A", "datetime": "2024-01-05T10:00:00", "sender": "p1", "content": "one",
             "quote": None, "reactions": [{"emoji": "👍", "senders": ["p3"]}]},
            {"id": "B", "datetime": "2024-01-06T10:00:00", "sender": "p2", "content": "two",
             "quote": None, "reactions": []},
            {"id": "C", "datetime": "2024-02-01T10:00:00", "sender": "p3", "content": "three",
             "quote": None, "reactions": []},
        ],
    }


def _claim(text: str, supporting: list[int], opposing: list[int] = ()) -> dict:
    return {"text": text, "entities": ["Alpha", " ", "Alpha"], "stance": "factual",
            "supporting": supporting, "opposing": list(opposing)}


def test_numbers_map_to_whatsapp_ids_and_record_shape() -> None:
    job = ThreadClaims(_thread())
    records = job.absorb({"claims": [_claim(" fact ", [1, 3, 99], [2, 0])]}, [])
    assert len(records) == 1
    claim, audit = job.finalize(records[0])
    assert list(claim) == C3_KEYS
    assert claim["claim_id"] == claim_id_for("t-A", "fact")
    assert claim["claim_text"] == "fact"
    assert claim["entities"] == ["Alpha"]
    assert claim["supporting_message_ids"] == ["A", "C"]  # 99 is out of range
    assert claim["opposing_message_ids"] == ["B"]
    assert (claim["first_date"], claim["last_date"]) == ("2024-01-05", "2024-02-01")
    # p3 wrote C and reacted to A: one supporter. p2 opposes.
    assert (claim["supporter_count"], claim["opposer_count"]) == (2, 1)
    assert list(audit) == ["claim_id", "thread_id", "supporters", "opposers"]
    assert {e["person"] for e in audit["supporters"]} == {"p1", "p3"}


def test_claim_id_is_the_short_sha1_of_the_text() -> None:
    claim_id = claim_id_for("t-A", "fact")
    assert claim_id.startswith("t-A-") and len(claim_id) == len("t-A-") + 8
    assert claim_id == claim_id_for("t-A", "fact")  # stable across runs
    assert claim_id != claim_id_for("t-A", "fact!")


def test_a_message_in_both_lists_counts_as_supporting() -> None:
    job = ThreadClaims(_thread())
    claim, _ = job.finalize(job.absorb({"claims": [_claim("x", [1, 2], [2])]}, [])[0])
    assert claim["supporting_message_ids"] == ["A", "B"]
    assert claim["opposing_message_ids"] == []


def test_identical_texts_collapse_into_one_claim() -> None:
    job = ThreadClaims(_thread())
    records = job.absorb({"claims": [_claim("same", [1]), _claim("same", [3], [2])]}, [])
    assert len(records) == 1
    assert records[0]["supporting_message_ids"] == ["A", "C"]
    assert records[0]["opposing_message_ids"] == ["B"]


def test_claims_without_supporting_evidence_are_dropped() -> None:
    job = ThreadClaims(_thread())
    assert job.absorb({"claims": [_claim("x", [42]), _claim("", [1])]}, []) == []
    assert job.ungrounded == 2


def test_update_keeps_existing_text_and_adds_only_new_evidence() -> None:
    job = ThreadClaims(_thread(), new_ids=frozenset({"C"}))
    existing = [{"claim_id": "t-A-old", "thread_id": "t-A", "claim_text": "edited by hand",
                 "entities": [], "stance": "neutral", "supporting_message_ids": ["A"],
                 "opposing_message_ids": []}]
    answer = {
        "claims": [_claim("new fact", [3]), _claim("not grounded in new messages", [1])],
        "existing": [{"claim": 1, "supporting": [2, 3], "opposing": []},
                     {"claim": 7, "supporting": [3], "opposing": []}],
    }
    records = job.absorb(answer, existing)
    assert [r["claim_id"] for r in records] == ["t-A-old", claim_id_for("t-A", "new fact")]
    assert records[0]["claim_text"] == "edited by hand"
    assert records[0]["supporting_message_ids"] == ["A", "C"]  # B is old: not added
    assert job.ungrounded == 1
    assert existing[0]["supporting_message_ids"] == ["A"]  # input not mutated


def test_finalize_drops_evidence_that_left_the_thread_and_keeps_manual_fields() -> None:
    job = ThreadClaims(_thread())
    record = {"claim_id": "k", "thread_id": "t-A", "claim_text": "t", "entities": [],
              "stance": "neutral", "supporting_message_ids": ["GONE", "B"],
              "opposing_message_ids": ["GONE2"], "supporter_count": 9, "_redactions": [1]}
    claim, _ = job.finalize(record)
    assert claim["supporting_message_ids"] == ["B"]
    assert claim["opposing_message_ids"] == []
    assert claim["supporter_count"] == 1
    assert list(claim)[-1] == "_redactions"
    record["supporting_message_ids"] = ["GONE"]
    assert job.finalize(record) is None


@pytest.mark.parametrize(
    ("answer", "update"),
    [
        ([], False),
        ({"claims": [{"text": "x"}]}, False),
        ({"claims": [_claim("x", [1]) | {"stance": "maybe"}]}, False),
        ({"claims": [_claim("x", ["1"])]}, False),
        ({"claims": []}, True),
        ({"claims": [], "existing": [{"claim": True, "supporting": [], "opposing": []}]}, True),
    ],
)
def test_malformed_answers_raise(answer: object, update: bool) -> None:
    existing = [{"claim_id": "k", "claim_text": "t"}] if update else []
    with pytest.raises(OutputError):
        ThreadClaims(_thread()).absorb(answer, existing)


def test_fingerprints_follow_content_and_reactions() -> None:
    thread = _thread()
    base_input, base_messages = input_fingerprint(thread), message_fingerprints(thread)
    thread["messages"][0]["reactions"] = []
    assert input_fingerprint(thread) != base_input
    assert message_fingerprints(thread) == base_messages  # reactions are not content
    thread["messages"][1]["content"] = "two, edited"
    assert message_fingerprints(thread)["B"] != base_messages["B"]
    assert set(DERIVED_KEYS) < set(C3_KEYS)


def test_a_claim_that_never_had_evidence_is_kept_and_dated_by_its_thread() -> None:
    record = {"claim_id": "k", "thread_id": "t-A", "claim_text": "t", "entities": [],
              "stance": "neutral", "supporting_message_ids": [], "opposing_message_ids": []}
    claim, audit = ThreadClaims(_thread()).finalize(record)
    assert (claim["first_date"], claim["last_date"]) == ("2024-01-05", "2024-02-01")
    assert claim["supporter_count"] == 0 and audit["supporters"] == []
