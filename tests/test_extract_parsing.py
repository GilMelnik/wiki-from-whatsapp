"""Robust parsing of the extract model's (schema-unenforced) output shapes.

The model returns claims under many ad-hoc shapes: bare lists, alternate keys
(``claim``/``text``), packed ``claims``/``insights`` lists, and ``[m..]``-style
string ids. ``_claims_from_result`` must salvage claim text, tags and message
ids from any of these without crashing.
"""

from __future__ import annotations

from step_3_extract.run import (
    EXTRACT_SCHEMA,
    _claims_from_result,
    _schema_json_example,
)


def _fixture(n: int = 3) -> tuple[dict, list[dict]]:
    line_meta = [
        {
            "sender": f"s{i}",
            "month": "2024-01",
            "label": "x",
            "message_index": i,
            "reactions": [],
        }
        for i in range(n)
    ]
    thread = {
        "thread_id": "t",
        "message_ids": [100 + i for i in range(n)],
        "messages": [{"sender": f"s{i}", "reactions": []} for i in range(n)],
    }
    return thread, line_meta


def test_bare_list_with_text_and_topic() -> None:
    thread, line_meta = _fixture()
    result = [{"topic": "usa", "text": "עלות הליך", "entities": ["X"]}]
    claims = _claims_from_result(result, thread, line_meta)
    assert len(claims) == 1
    assert claims[0]["claim_text"] == "עלות הליך"
    assert "usa" in claims[0]["topic_tags"]
    assert claims[0]["entities"] == ["X"]


def test_source_message_m_prefix_becomes_int() -> None:
    thread, line_meta = _fixture()
    result = [{"claim": "c", "source_message": "m2", "topic": "t"}]
    claims = _claims_from_result(result, thread, line_meta)
    assert claims[0]["_local_message_ids"] == [2]


def test_source_message_ids_list_and_range_filter() -> None:
    thread, line_meta = _fixture()  # valid ids: 0..2
    result = [{"text": "c", "source_message_ids": ["m0", "m104"]}]
    claims = _claims_from_result(result, thread, line_meta)
    assert claims[0]["_local_message_ids"] == [0]  # 104 dropped (out of range)


def test_insights_list_expands_to_many() -> None:
    thread, line_meta = _fixture()
    result = [{"category": "legal", "insights": ["a", "b"], "entities": []}]
    claims = _claims_from_result(result, thread, line_meta)
    assert [c["claim_text"] for c in claims] == ["a", "b"]
    assert all("legal" in c["topic_tags"] for c in claims)


def test_canonical_dict_claims() -> None:
    thread, line_meta = _fixture()
    result = {
        "claims": [
            {
                "claim_text": "z",
                "topic_tags": ["k"],
                "entities": [],
                "stance": "factual",
                "supporting_message_ids": [1],
                "opposing_message_ids": [],
            }
        ]
    }
    claims = _claims_from_result(result, thread, line_meta)
    assert claims[0]["stance"] == "factual"
    assert claims[0]["_local_message_ids"] == [1]


def test_string_item() -> None:
    thread, line_meta = _fixture()
    claims = _claims_from_result(["just text"], thread, line_meta)
    assert claims[0]["claim_text"] == "just text"


def test_non_dict_result_is_safe() -> None:
    thread, line_meta = _fixture()
    assert _claims_from_result(None, thread, line_meta) == []
    assert _claims_from_result(42, thread, line_meta) == []


def test_schema_example_tracks_schema() -> None:
    example = _schema_json_example(EXTRACT_SCHEMA)
    assert '"claims"' in example
    assert '"claim_text"' in example
    assert "positive|negative|neutral|factual" in example
