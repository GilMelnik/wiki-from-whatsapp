"""Tests for entity reviewer store mutations."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from step_4b_entities.reviewer.store import EntityStore


@pytest.fixture
def store_files(tmp_path: Path) -> tuple[Path, Path]:
    claims = {
        "claims": [
            {
                "claim_id": "t1-c0",
                "thread_id": "t1",
                "claim_text": "contact joindavidshield@davidshield.com",
                "topic_tags": ["overview"],
                "entities": ["David Shield"],
            }
        ]
    }
    entities = {
        "entities": [
            {
                "entity_id": "e0000",
                "canonical_name": "David Shield",
                "status": "suggested",
                "aliases": ["David Shield", "DavidShield"],
                "members": [
                    {
                        "name": "David Shield",
                        "claim_ids": None,
                        "count": 1,
                        "sample_claim_ids": ["t1-c0"],
                        "topics": ["overview"],
                        "contacts": {
                            "email": ["joindavidshield@davidshield.com"],
                            "phone": [],
                            "website": ["davidshield.com"],
                        },
                    },
                    {
                        "name": "DavidShield",
                        "claim_ids": None,
                        "count": 1,
                        "sample_claim_ids": [],
                        "topics": [],
                        "contacts": {"email": [], "phone": [], "website": []},
                    },
                ],
                "contacts": {
                    "email": ["joindavidshield@davidshield.com"],
                    "phone": [],
                    "website": ["davidshield.com"],
                },
                "topics": ["overview"],
                "score": 1.0,
            },
            {
                "entity_id": "e0001",
                "canonical_name": "ORM",
                "status": "accepted",
                "aliases": ["ORM"],
                "members": [
                    {
                        "name": "ORM",
                        "claim_ids": None,
                        "count": 2,
                        "sample_claim_ids": [],
                        "topics": [],
                        "contacts": {"email": [], "phone": [], "website": []},
                    }
                ],
                "contacts": {"email": [], "phone": [], "website": []},
                "topics": [],
                "score": 1.0,
            },
        ],
        "metadata": {},
    }
    claims_path = tmp_path / "claims.json"
    entities_path = tmp_path / "entities.json"
    claims_path.write_text(json.dumps(claims), encoding="utf-8")
    entities_path.write_text(json.dumps(entities), encoding="utf-8")
    return entities_path, claims_path


def test_rename_set_contacts_delete(store_files: tuple[Path, Path]) -> None:
    entities_path, claims_path = store_files
    store = EntityStore(entities_path=entities_path, claims_path=claims_path)
    store.load()

    renamed = store.rename_entity("e0000", "David Shield Insurance")
    assert renamed["canonical_name"] == "David Shield Insurance"

    updated = store.set_contacts(
        "e0000",
        {
            "email": ["support@davidshield.com", "support@davidshield.com"],
            "phone": ["+1-555-0100"],
            "website": ["davidshield.com", "https://davidshield.com"],
        },
    )
    assert updated["contacts"]["email"] == ["support@davidshield.com"]
    assert updated["contacts_manual"] is True

    # Recompute from member move must not wipe manual contacts.
    store.move_member("e0000", "DavidShield", target_entity_id="e0001")
    kept = store.get_entity("e0000")["entity"]
    assert kept["contacts"]["email"] == ["support@davidshield.com"]

    deleted = store.delete_entity("e0001")
    assert deleted["deleted_id"] == "e0001"
    assert deleted["next_id"] == "e0000"
    assert store.stats()["entity_count"] == 1


def test_copy_claims_keeps_source(store_files: tuple[Path, Path]) -> None:
    entities_path, claims_path = store_files
    claims = json.loads(claims_path.read_text(encoding="utf-8"))
    claims["claims"].append(
        {
            "claim_id": "t2-c0",
            "thread_id": "t2",
            "claim_text": "David Shield contact",
            "topic_tags": ["overview"],
            "entities": ["David Shield"],
        }
    )
    claims_path.write_text(json.dumps(claims), encoding="utf-8")

    store = EntityStore(entities_path=entities_path, claims_path=claims_path)
    store.load()
    store.copy_claims("e0000", "David Shield", ["t1-c0"], target_entity_id="e0001")

    source = store.get_entity("e0000")["entity"]
    target = store.get_entity("e0001")["entity"]
    source_ids = {
        c["claim_id"]
        for m in source["members"]
        for c in m["sample_claims"]
    }
    target_ids = {
        c["claim_id"]
        for m in target["members"]
        for c in m["sample_claims"]
    }
    assert "t1-c0" in source_ids
    assert "t1-c0" in target_ids


def test_exclude_claims(store_files: tuple[Path, Path]) -> None:
    entities_path, claims_path = store_files
    claims = json.loads(claims_path.read_text(encoding="utf-8"))
    claims["claims"].extend(
        [
            {
                "claim_id": "t2-c0",
                "thread_id": "t2",
                "claim_text": "David Shield and ORM",
                "topic_tags": ["overview"],
                "entities": ["David Shield", "ORM"],
            },
            {
                "claim_id": "t3-c0",
                "thread_id": "t3",
                "claim_text": "David Shield unrelated mention",
                "topic_tags": ["overview"],
                "entities": ["David Shield"],
            },
        ]
    )
    claims_path.write_text(json.dumps(claims), encoding="utf-8")

    store = EntityStore(entities_path=entities_path, claims_path=claims_path)
    store.load()
    store.exclude_claims("e0000", "David Shield", ["t2-c0", "t3-c0"])

    detail = store.get_entity("e0000")["entity"]
    member = next(m for m in detail["members"] if m["name"] == "David Shield")
    claim_ids = {c["claim_id"] for c in member["sample_claims"]}
    assert "t2-c0" not in claim_ids
    assert "t3-c0" not in claim_ids
    excluded = member.get("excluded_claim_ids") or []
    assert "t2-c0" in excluded
    assert "t3-c0" in excluded


def test_claim_highlights_other_entities(store_files: tuple[Path, Path]) -> None:
    entities_path, claims_path = store_files
    claims = json.loads(claims_path.read_text(encoding="utf-8"))
    claims["claims"] = [
        {
            "claim_id": "t1-c0",
            "thread_id": "t1",
            "claim_text": "David Shield works with ORM",
            "topic_tags": ["overview"],
            "entities": ["David Shield", "ORM"],
        }
    ]
    claims_path.write_text(json.dumps(claims), encoding="utf-8")

    store = EntityStore(entities_path=entities_path, claims_path=claims_path)
    store.load()
    detail = store.get_entity("e0000")["entity"]
    sample = detail["members"][0]["sample_claims"][0]
    kinds = {h["kind"] for h in sample["highlights"]}
    assert "self" in kinds
    assert "other" in kinds
    other = next(h for h in sample["highlights"] if h["kind"] == "other")
    assert other["entity_id"] == "e0001"


def _uncertain_store_files(tmp_path: Path) -> tuple[Path, Path]:
    claims = {
        "claims": [
            {
                "claim_id": "t1-c0",
                "thread_id": "t1",
                "claim_text": "David Shield and ORM, mail maybe@shared.com",
                "topic_tags": ["overview"],
                "entities": ["David Shield", "ORM"],
            }
        ]
    }
    entities = {
        "entities": [
            {
                "entity_id": "e0000",
                "canonical_name": "David Shield",
                "status": "suggested",
                "aliases": ["David Shield"],
                "members": [
                    {
                        "name": "David Shield",
                        "claim_ids": None,
                        "count": 1,
                        "sample_claim_ids": ["t1-c0"],
                        "topics": ["overview"],
                        "contacts": {"email": [], "phone": [], "website": []},
                        "contacts_uncertain": {
                            "email": ["maybe@shared.com"],
                            "phone": [],
                            "website": [],
                        },
                    }
                ],
                "contacts": {"email": [], "phone": [], "website": []},
                "contacts_uncertain": {
                    "email": ["maybe@shared.com"],
                    "phone": [],
                    "website": [],
                },
                "topics": ["overview"],
                "score": 1.0,
            }
        ],
        "metadata": {},
    }
    claims_path = tmp_path / "claims.json"
    entities_path = tmp_path / "entities.json"
    claims_path.write_text(json.dumps(claims), encoding="utf-8")
    entities_path.write_text(json.dumps(entities), encoding="utf-8")
    return entities_path, claims_path


def test_accept_uncertain_contact_with_edit(tmp_path: Path) -> None:
    entities_path, claims_path = _uncertain_store_files(tmp_path)
    store = EntityStore(entities_path=entities_path, claims_path=claims_path)
    store.load()

    updated = store.resolve_uncertain_contact(
        "e0000",
        kind="email",
        value="maybe@shared.com",
        action="accept",
        new_value="confirmed@davidshield.com",
    )
    assert updated["contacts"]["email"] == ["confirmed@davidshield.com"]
    assert updated["contacts_uncertain"]["email"] == []
    assert updated["contacts_manual"] is True


def test_reject_uncertain_contact_does_not_resurrect(tmp_path: Path) -> None:
    entities_path, claims_path = _uncertain_store_files(tmp_path)
    store = EntityStore(entities_path=entities_path, claims_path=claims_path)
    store.load()

    rejected = store.resolve_uncertain_contact(
        "e0000", kind="email", value="maybe@shared.com", action="reject"
    )
    assert rejected["contacts_uncertain"]["email"] == []
    assert rejected["contacts"]["email"] == []

    # A later recompute (rename triggers no recompute; set_canonical does) must not
    # bring the rejected value back from the member bucket.
    store.set_canonical("e0000", "David Shield")
    again = store.get_entity("e0000")["entity"]
    assert again["contacts_uncertain"]["email"] == []


def test_resolve_uncertain_contact_validates(tmp_path: Path) -> None:
    entities_path, claims_path = _uncertain_store_files(tmp_path)
    store = EntityStore(entities_path=entities_path, claims_path=claims_path)
    store.load()

    with pytest.raises(ValueError):
        store.resolve_uncertain_contact(
            "e0000", kind="email", value="absent@x.com", action="accept"
        )
    with pytest.raises(ValueError):
        store.resolve_uncertain_contact(
            "e0000", kind="email", value="maybe@shared.com", action="bogus"
        )


def test_related_entities_tagged_not_in_text(store_files: tuple[Path, Path]) -> None:
    entities_path, claims_path = store_files
    claims = json.loads(claims_path.read_text(encoding="utf-8"))
    claims["claims"] = [
        {
            "claim_id": "t1-c0",
            "thread_id": "t1",
            "claim_text": 'מומלץ לשלב בין ביטוח "גרייט מורנינג" לבין ביטוח "דיוויד שילד".',
            "topic_tags": ["overview"],
            "entities": ["Great Morning", "David Shield"],
        }
    ]
    claims_path.write_text(json.dumps(claims), encoding="utf-8")

    store = EntityStore(entities_path=entities_path, claims_path=claims_path)
    store.load()
    detail = store.get_entity("e0000")["entity"]
    sample = detail["members"][0]["sample_claims"][0]
    related = sample.get("related_entities") or []
    assert any(r["name"] == "Great Morning" for r in related)
    assert all(r.get("tagged_only") for r in related)
