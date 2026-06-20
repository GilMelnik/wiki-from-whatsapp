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
