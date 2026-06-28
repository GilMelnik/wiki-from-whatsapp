"""Tests for entity reviewer store mutations."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from step_4_entities.reviewer.store import EntityStore


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


def test_reject_splits_and_trims_to_llm_claims(
    store_files: tuple[Path, Path], tmp_path: Path
) -> None:
    entities_path, claims_path = store_files
    claims = json.loads(claims_path.read_text(encoding="utf-8"))
    # Mentions "David Shield" in text but the LLM tagged it to ORM only; reject
    # must drop this mention-matched claim from the David Shield entity (Req 3).
    claims["claims"].append(
        {
            "claim_id": "t2-c0",
            "thread_id": "t2",
            "claim_text": "David Shield is great",
            "topic_tags": ["overview"],
            "entities": ["ORM"],
        }
    )
    claims_path.write_text(json.dumps(claims), encoding="utf-8")

    store = EntityStore(
        entities_path=entities_path,
        claims_path=claims_path,
        aggregations_path=tmp_path / "agg.json",
    )
    store.load()

    store.reject("e0000")

    by_name = {e["canonical_name"]: e for e in store.entities}
    # The two-member cluster split into one standalone entity per member (Req 2).
    assert "David Shield" in by_name
    assert "DavidShield" in by_name
    assert by_name["David Shield"]["entity_id"] != by_name["DavidShield"]["entity_id"]
    assert by_name["David Shield"]["status"] == "suggested"

    ds = store.get_entity(by_name["David Shield"]["entity_id"])["entity"]
    member = ds["members"][0]
    ids = {c["claim_id"] for c in member["sample_claims"]}
    assert ids == {"t1-c0"}  # only the LLM-tagged claim, not the mention match

    dsj = store.get_entity(by_name["DavidShield"]["entity_id"])["entity"]
    assert dsj["members"][0]["sample_claims"] == []  # no LLM tag for this alias


def test_member_claims_pagination(
    store_files: tuple[Path, Path], tmp_path: Path
) -> None:
    entities_path, claims_path = store_files
    claims = json.loads(claims_path.read_text(encoding="utf-8"))
    for i in range(1, 15):
        claims["claims"].append(
            {
                "claim_id": f"t1-c{i}",
                "thread_id": "t1",
                "claim_text": f"David Shield note {i}",
                "topic_tags": ["overview"],
                "entities": ["David Shield"],
            }
        )
    claims_path.write_text(json.dumps(claims), encoding="utf-8")

    store = EntityStore(
        entities_path=entities_path,
        claims_path=claims_path,
        aggregations_path=tmp_path / "agg.json",
    )
    store.load()

    page = store.member_claims("e0000", "David Shield", offset=0, limit=5)
    assert page["count"] == 15
    assert len(page["claims"]) == 5
    page2 = store.member_claims("e0000", "David Shield", offset=5, limit=5)
    first = {c["claim_id"] for c in page["claims"]}
    second = {c["claim_id"] for c in page2["claims"]}
    assert len(second) == 5
    assert first.isdisjoint(second)


def test_manual_aggregation_hides_members_and_edits(
    store_files: tuple[Path, Path], tmp_path: Path
) -> None:
    entities_path, claims_path = store_files
    claims = json.loads(claims_path.read_text(encoding="utf-8"))
    claims["claims"].extend(
        [
            {
                "claim_id": "t2-c0",
                "thread_id": "t2",
                "claim_text": "David Shield duplicate two",
                "topic_tags": ["overview"],
                "entities": ["David Shield"],
            },
            {
                "claim_id": "t3-c0",
                "thread_id": "t3",
                "claim_text": "David Shield duplicate three",
                "topic_tags": ["overview"],
                "entities": ["David Shield"],
            },
        ]
    )
    claims_path.write_text(json.dumps(claims), encoding="utf-8")

    store = EntityStore(
        entities_path=entities_path,
        claims_path=claims_path,
        aggregations_path=tmp_path / "agg.json",
    )
    store.load()

    with pytest.raises(ValueError):
        store.create_aggregation(["t1-c0"], representative="t1-c0")
    with pytest.raises(ValueError):
        store.create_aggregation(["t1-c0", "t2-c0"], representative="t3-c0")

    group = store.create_aggregation(
        ["t1-c0", "t2-c0", "t3-c0"], representative="t2-c0"
    )

    def member_ids() -> set[str]:
        detail = store.get_entity("e0000")["entity"]
        member = next(m for m in detail["members"] if m["name"] == "David Shield")
        return {c["claim_id"] for c in member["sample_claims"]}, member

    ids, member = member_ids()
    assert "t2-c0" in ids  # representative stays visible
    assert "t1-c0" not in ids and "t3-c0" not in ids  # folded away
    rep = next(c for c in member["sample_claims"] if c["claim_id"] == "t2-c0")
    assert rep["aggregation"]["count"] == 3

    # Re-aggregating a claim already in a group is rejected.
    with pytest.raises(ValueError):
        store.create_aggregation(["t1-c0", "t2-c0"], representative="t1-c0")

    store.decouple_claim(group["id"], "t3-c0")
    ids, _ = member_ids()
    assert "t3-c0" in ids and "t1-c0" not in ids

    store.set_representative(group["id"], "t1-c0")
    ids, _ = member_ids()
    assert "t1-c0" in ids and "t2-c0" not in ids

    # Decoupling down to a single member dissolves the group entirely.
    result = store.decouple_claim(group["id"], "t2-c0")
    assert result.get("dissolved") is True
    ids, _ = member_ids()
    assert {"t1-c0", "t2-c0"} <= ids


def test_delete_claim_removes_everywhere(
    store_files: tuple[Path, Path], tmp_path: Path
) -> None:
    entities_path, claims_path = store_files
    claims = json.loads(claims_path.read_text(encoding="utf-8"))
    claims["claims"].append(
        {
            "claim_id": "t2-c0",
            "thread_id": "t2",
            "claim_text": "David Shield extra claim",
            "topic_tags": ["overview"],
            "entities": ["David Shield"],
        }
    )
    claims_path.write_text(json.dumps(claims), encoding="utf-8")

    store = EntityStore(
        entities_path=entities_path,
        claims_path=claims_path,
        aggregations_path=tmp_path / "agg.json",
    )
    store.load()

    # An aggregation referencing the doomed claim must dissolve on delete.
    store.create_aggregation(["t1-c0", "t2-c0"], representative="t1-c0")

    store.delete_claim("t2-c0")

    detail = store.get_entity("e0000")["entity"]
    member = next(m for m in detail["members"] if m["name"] == "David Shield")
    ids = {c["claim_id"] for c in member["sample_claims"]}
    assert "t2-c0" not in ids
    assert "t1-c0" in ids
    assert store.aggregations() == []

    # The deleted id is persisted on the entities payload for downstream steps.
    persisted = json.loads(entities_path.read_text(encoding="utf-8"))
    assert "t2-c0" in (persisted.get("deleted_claims") or [])

    from step_5_aggregate.resolver import load_deleted_claims

    assert "t2-c0" in load_deleted_claims(entities_path)

    with pytest.raises(KeyError):
        store.delete_claim("does-not-exist")


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
