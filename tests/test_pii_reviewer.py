"""Tests for pii_reviewer.store."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pii_reviewer.store import ClaimStore
from wiki_build.scrub import REDACTION_MARK, scrub_claims


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
