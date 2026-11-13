"""Step 4 entity resolution: clustering precision, transliteration, guard, contacts."""

from __future__ import annotations

import json
from itertools import count
from pathlib import Path

from step_4_entities.cluster import EntityClusterer
from step_4_entities.mentions import SimpleAnalyzer, analyze_claims
from step_4_entities.normalize import normalize_name, transliteration_skeleton
from step_4_entities.records import claim_contacts
from tests.entities_fixtures import collect_entities, entity_claim


def _cluster_of(entities: list[dict], name: str) -> str:
    for entity in entities:
        if any(m["name"] == name for m in entity["members"]):
            return entity["entity_id"]
    raise AssertionError(f"{name} not found in any cluster")


def _cluster(
    claims: list[dict],
    tmp_path: Path,
    *,
    seed_path: Path | None = None,
    original_by_id: dict | None = None,
) -> list[dict]:
    analysis = analyze_claims(claims, SimpleAnalyzer())
    ids = count()
    return EntityClusterer(
        analysis,
        similarity_threshold=0.80,
        # Isolate from any data/step_4_entities/entities_seed.json unless a test opts in.
        seed_path=seed_path or (tmp_path / "no_seed.json"),
    ).cluster(
        collect_entities(claims, original_by_id=original_by_id, analysis=analysis),
        lambda: f"e{next(ids):04d}",
    )


def _entity_with(entities: list[dict], name: str) -> dict:
    for entity in entities:
        if any(m["name"] == name for m in entity["members"]):
            return entity
    raise AssertionError(f"{name} not found in any cluster")


def test_clustering_merges_variants_keeps_distinct_separate(tmp_path: Path) -> None:
    claims = [
        entity_claim("t1-c0", ["עמית פלס"]),
        entity_claim("t1-c1", ["Amit Peles"]),
        entity_claim("t2-c0", ["אל על"]),
        entity_claim("t2-c1", ["אלעל"]),
        entity_claim("t3-c0", ["קופת חולים"]),
        entity_claim("t3-c1", ["קופות החולים"]),
        entity_claim("t4-c0", ["תל אביב"]),
        entity_claim("t4-c1", ["ירושלים"]),
        entity_claim("t5-c0", ["מכבי"]),
        entity_claim("t5-c1", ["כללית"]),
    ]
    entities = _cluster(claims, tmp_path)

    # Cross-script transliteration and same-script spelling variants merge.
    assert _cluster_of(entities, "עמית פלס") == _cluster_of(entities, "Amit Peles")
    assert _cluster_of(entities, "אל על") == _cluster_of(entities, "אלעל")
    assert _cluster_of(entities, "קופת חולים") == _cluster_of(entities, "קופות החולים")

    # Distinct same-type entities must NOT merge (the embedding failure mode).
    assert _cluster_of(entities, "תל אביב") != _cluster_of(entities, "ירושלים")
    assert _cluster_of(entities, "מכבי") != _cluster_of(entities, "כללית")


def test_prefix_and_cooccurrence_merge(tmp_path: Path) -> None:
    # "עו"ד הראל" normalizes to "הראל", a whole-word prefix of "הראל ברק", and the
    # two co-occur on one claim -> a must-link alias (not two entities).
    claims = [
        entity_claim("t1-c0", ['עו"ד הראל', "הראל ברק"]),
        entity_claim("t1-c1", ["הראל ברק"]),
    ]
    entities = _cluster(claims, tmp_path)
    assert _cluster_of(entities, 'עו"ד הראל') == _cluster_of(entities, "הראל ברק")
    ent = _entity_with(entities, "הראל ברק")
    assert ent["canonical_name"] == "הראל ברק"
    assert "prefix" in ent["merge_signals"]
    assert "co_occur" in ent["merge_signals"]


def test_context_guard_holds_look_alike_short_names_apart(tmp_path: Path) -> None:
    # Same short name up to punctuation, but the claims around them share no word:
    # held apart and flagged for review instead of silently merged.
    claims = [
        entity_claim("t1-c0", ["Gaya"], "Gaya lawyer handled the court papers"),
        entity_claim("t2-c0", ["Gayaa"], "Gayaa kindergarten opens at seven"),
    ]
    entities = _cluster(claims, tmp_path)
    assert _cluster_of(entities, "Gaya") != _cluster_of(entities, "Gayaa")
    ent = _entity_with(entities, "Gaya")
    assert ent["status"] == "ambiguous"
    assert ent["conflict_with"] == ["Gayaa"]


def test_context_guard_lets_look_alikes_with_shared_context_merge(tmp_path: Path) -> None:
    claims = [
        entity_claim("t1-c0", ["Gaya"], "Gaya agency charges a fee for the surrogacy process"),
        entity_claim("t2-c0", ["Gayaa"], "Gayaa agency fee for the surrogacy process is high"),
    ]
    entities = _cluster(claims, tmp_path)
    assert _cluster_of(entities, "Gaya") == _cluster_of(entities, "Gayaa")
    assert _entity_with(entities, "Gaya")["status"] == "suggested"


def test_context_guard_ignores_long_names(tmp_path: Path) -> None:
    # Long names are distinctive enough: string similarity alone decides.
    claims = [
        entity_claim("t1-c0", ["Sunrise Fertility Center"], "Sunrise Fertility Center court papers"),
        entity_claim("t2-c0", ["Sunrise Fertility Centre"], "Sunrise Fertility Centre kindergarten"),
    ]
    entities = _cluster(claims, tmp_path)
    assert _cluster_of(entities, "Sunrise Fertility Center") == _cluster_of(
        entities, "Sunrise Fertility Centre"
    )


def test_confident_contact_merges_distinct_spellings(tmp_path: Path) -> None:
    # Each name appears alone with the same email -> confident contact must-link.
    claims = [
        {
            "claim_id": "t1-c0",
            "thread_id": "t1",
            "claim_text": "Foo Clinic",
            "entities": ["Foo Clinic"],
            "_redactions": [{"type": "email", "value": "info@fooclinic.com"}],
        },
        {
            "claim_id": "t2-c0",
            "thread_id": "t2",
            "claim_text": "Foo Klinik",
            "entities": ["Foo Klinik"],
            "_redactions": [{"type": "email", "value": "info@fooclinic.com"}],
        },
    ]
    entities = _cluster(claims, tmp_path)
    assert _cluster_of(entities, "Foo Clinic") == _cluster_of(entities, "Foo Klinik")
    ent = _entity_with(entities, "Foo Clinic")
    assert "confident_contact" in ent["merge_signals"]


def test_multi_entity_contact_is_uncertain_and_no_merge(tmp_path: Path) -> None:
    # One email, two entities in the claim: unattributable -> uncertain, no merge.
    claims = [
        {
            "claim_id": "t1-c0",
            "thread_id": "t1",
            "claim_text": "Alpha Beta",
            "entities": ["Alpha", "Beta"],
            "_redactions": [{"type": "email", "value": "shared@x.com"}],
        }
    ]
    entities = _cluster(claims, tmp_path)
    assert _cluster_of(entities, "Alpha") != _cluster_of(entities, "Beta")
    alpha = _entity_with(entities, "Alpha")
    assert alpha["contacts"]["email"] == []
    assert "shared@x.com" in alpha["contacts_uncertain"]["email"]


def test_normalize_strips_titles_and_phones() -> None:

    assert normalize_name('עו"ד הראל') == normalize_name("הראל")
    assert normalize_name("Dr. Cohen") == normalize_name("cohen")
    assert normalize_name("+1 (555) 010-2030") == "15550102030"


def test_seed_must_links_aliases(tmp_path: Path) -> None:
    seed_path = tmp_path / "seed.json"
    seed_path.write_text(
        json.dumps(
            {
                "entities": [
                    {
                        "id": "acme",
                        "canonical": "Acme Agency",
                        "aliases": ["Acme Agency", "Acme", "אקמי"],
                        "type": "agency",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    claims = [
        {
            "claim_id": "t1-c0",
            "thread_id": "t1",
            "claim_text": "a",
            "entities": ["Acme Agency"],
        },
        {
            "claim_id": "t2-c0",
            "thread_id": "t2",
            "claim_text": "b",
            "entities": ["אקמי"],
        },
    ]
    entities = _cluster(claims, tmp_path, seed_path=seed_path)
    ent = _entity_with(entities, "Acme Agency")
    assert _cluster_of(entities, "אקמי") == ent["entity_id"]
    assert ent["canonical_name"] == "Acme Agency"
    assert ent["type"] == "agency"
    assert "seed" in ent["merge_signals"]
    assert ent["status"] == "suggested"


def test_seed_status_applies_only_to_a_group_made_of_that_seed(tmp_path: Path) -> None:
    seed_path = tmp_path / "seed.json"
    seed_path.write_text(
        json.dumps(
            {
                "entities": [
                    {
                        "id": "acme",
                        "canonical": "Acme Agency",
                        "aliases": ["Acme Agency", "אקמי"],
                        "status": "accepted",
                    },
                    {
                        "id": "zenith",
                        "canonical": "Zenith Clinic",
                        "aliases": ["Zenith Clinic"],
                        "status": "accepted",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    claims = [
        entity_claim("t1-c0", ["Acme Agency"]),
        entity_claim("t2-c0", ["אקמי"]),
        entity_claim("t3-c0", ["Zenith Clinic"]),
        # Not in the seed, but a spelling variant that clusters with the seeded name.
        entity_claim("t4-c0", ["Zenith Clinics"]),
    ]
    entities = _cluster(claims, tmp_path, seed_path=seed_path)

    assert _entity_with(entities, "Acme Agency")["status"] == "accepted"
    zenith = _entity_with(entities, "Zenith Clinic")
    assert _cluster_of(entities, "Zenith Clinics") == zenith["entity_id"]
    assert zenith["status"] == "suggested"


def test_transliteration_skeleton_aligns_across_scripts() -> None:
    assert transliteration_skeleton("עמית פלס") == transliteration_skeleton("Amit Peles")
    # David Shield: skeletons are close but not required to be identical.
    assert transliteration_skeleton("דלתא") == transliteration_skeleton("Delta")


def test_contacts_preserved_from_claims(tmp_path: Path) -> None:
    claim = entity_claim("t1-c0", ["David Shield"])
    claim["_redactions"] = [{"type": "email", "value": "x@davidshield.com"}]
    claim["claim_text"] = "ביטוח David Shield דרך davidshield.com"
    entities = _cluster([claim], tmp_path)
    contacts = entities[0]["contacts"]
    assert "x@davidshield.com" in contacts["email"]
    assert "davidshield.com" in contacts["website"]


def test_contacts_from_original_when_edited_lacks_redactions(tmp_path: Path) -> None:
    """PII review drops ``_redactions`` from edited claims; original still has them."""
    original = entity_claim("t1-c0", ["David Shield"])
    original["_redactions"] = [{"type": "email", "value": "x@davidshield.com"}]
    original["claim_text"] = 'ביטוח "David Shield" בדוא"ל [הוסר]'
    edited = entity_claim("t1-c0", ["David Shield"])
    edited["claim_text"] = (
        'ניתן לפנות ל-David Shield בדוא"ל joindavidshield@davidshield.com'
    )
    edited["_pii_review"] = "restored"
    original_by_id = {original["claim_id"]: original}

    emails, _phones, sites = claim_contacts(edited, original)
    assert "x@davidshield.com" in emails
    assert "joindavidshield@davidshield.com" in emails
    assert "davidshield.com" in sites

    entities = _cluster([edited], tmp_path, original_by_id=original_by_id)
    contacts = entities[0]["contacts"]
    assert "x@davidshield.com" in contacts["email"]
    assert "joindavidshield@davidshield.com" in contacts["email"]


def test_contacts_from_claim_text_without_redactions() -> None:
    claim = entity_claim("t1-c0", ["David Shield"])
    claim["claim_text"] = (
        'ניתן לפנות ל-David Shield בדוא"ל joindavidshield@davidshield.com'
    )
    emails, phones, sites = claim_contacts(claim)
    assert emails == ["joindavidshield@davidshield.com"]
    assert "davidshield.com" in sites
