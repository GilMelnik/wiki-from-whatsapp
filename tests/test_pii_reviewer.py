"""Tests for pii_reviewer.store."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from step_3_extract.reviewer.store import ClaimStore
from step_3_extract.scrub import REDACTION_MARK, scrub_claims


@pytest.fixture
def claims_file(tmp_path: Path) -> Path:
    claims = [
        {"claim_id": "t1-c0", "thread_id": "t1", "claim_text": "צור קשר ב-050-1234567."},
        {"claim_id": "t1-c1", "thread_id": "t1", "claim_text": "עורך דין מומלץ."},
        {
            "claim_id": "t2-c0",
            "thread_id": "t2",
            "claim_text": "כתובת foo@bar.com לפרטים.",
        },
    ]
    scrub_claims(claims)
    path = tmp_path / "claims.json"
    path.write_text(
        json.dumps({"claims": claims, "metadata": {}}, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def test_list_pending_claims(claims_file: Path) -> None:
    store = ClaimStore(claims_path=claims_file)
    store.load()
    items, total = store.list_enriched(filter_kind="pending")
    assert total == 2
    assert {i["claim_id"] for i in items} == {"t1-c0", "t2-c0"}
    assert items[0]["original_text"] != items[0]["claim_text"]


def test_accept_redaction(claims_file: Path) -> None:
    store = ClaimStore(claims_path=claims_file)
    store.load()
    result = store.review("t1-c0", "accept")
    assert result["review_status"] == "accepted"
    assert REDACTION_MARK in result["claim_text"]
    claim = store.get_claim("t1-c0")
    assert claim is not None
    assert "_redactions" not in claim
    assert claim["_pii_review"] == "accepted"


def test_restore_redaction(claims_file: Path) -> None:
    store = ClaimStore(claims_path=claims_file)
    store.load()
    result = store.review("t2-c0", "restore")
    assert result["review_status"] == "restored"
    assert "foo@bar.com" in result["claim_text"]
    assert REDACTION_MARK not in result["claim_text"]
    claim = store.get_claim("t2-c0")
    assert claim is not None
    assert claim["_pii_review"] == "restored"


def test_store_loads_c3_claims(tmp_path: Path) -> None:
    claim = {
        "claim_id": "t-W1-0a1b2c3d",
        "thread_id": "t-W1",
        "claim_text": "צור קשר ב-050-1234567.",
        "entities": ["Alpha"],
        "stance": "factual",
        "supporting_message_ids": ["W1"],
        "opposing_message_ids": [],
        "first_date": "2024-01-05",
        "last_date": "2024-01-05",
        "supporter_count": 3,
        "opposer_count": 1,
        "reaction_summary": [],
    }
    scrub_claims([claim])
    path = tmp_path / "claims.json"
    path.write_text(json.dumps({"claims": [claim], "metadata": {}}), encoding="utf-8")
    store = ClaimStore(claims_path=path)
    store.load()
    items, total = store.list_enriched(filter_kind="pending", sort="date")
    assert total == 1
    assert items[0]["date"] == "2024-01-05"
    assert items[0]["support_count"] == 3 and items[0]["opposer_count"] == 1
    assert store.review(claim["claim_id"], "accept")["review_status"] == "accepted"
