"""Step 4b entity resolution: clustering precision, transliteration, resolver."""

from __future__ import annotations

import json
from pathlib import Path

from step_4b_entities.cluster import (
    cluster_entities,
    ensure_entity_distance_matrix,
    _entity_distance_matrix_metadata,
    _entity_distance_matrix_need_rebuild,
)
from step_4b_entities.collect import collect_entities, _claim_contacts
from step_4b_entities.constants import DISTANCE_METHOD
from step_4b_entities.normalize import normalize_name, transliteration_skeleton
from step_4b_entities.pair_index import EntityPairIndex
from step_5_aggregate.resolver import EntityResolver, load_entity_resolver, apply_entity_resolution


def _claim(claim_id: str, entities: list[str], topics: list[str] | None = None) -> dict:
    return {
        "claim_id": claim_id,
        "thread_id": claim_id.split("-")[0],
        "claim_text": " / ".join(entities),
        "topic_tags": topics or ["overview"],
        "entities": entities,
    }


def _cluster_of(entities: list[dict], name: str) -> str:
    for entity in entities:
        if any(m["name"] == name for m in entity["members"]):
            return entity["entity_id"]
    raise AssertionError(f"{name} not found in any cluster")


def _cluster(
    claims: list[dict], tmp_path: Path, *, seed_path: Path | None = None
) -> list[dict]:
    source = tmp_path / "claims.json"
    source.write_text(json.dumps({"claims": claims}), encoding="utf-8")
    return cluster_entities(
        collect_entities(claims),
        source,
        matrix_path=tmp_path / "dist.npy",
        meta_path=tmp_path / "dist.json",
        # Isolate from the shipped data/entities_seed.json unless a test opts in.
        seed_path=seed_path or (tmp_path / "no_seed.json"),
    )


def _entity_with(entities: list[dict], name: str) -> dict:
    for entity in entities:
        if any(m["name"] == name for m in entity["members"]):
            return entity
    raise AssertionError(f"{name} not found in any cluster")


def test_clustering_merges_variants_keeps_distinct_separate(tmp_path: Path):
    claims = [
        _claim("t1-c0", ["עמית פלס"]),
        _claim("t1-c1", ["Amit Peles"]),
        _claim("t2-c0", ["אל על"]),
        _claim("t2-c1", ["אלעל"]),
        _claim("t3-c0", ["קופת חולים"]),
        _claim("t3-c1", ["קופות החולים"]),
        _claim("t4-c0", ["תל אביב"]),
        _claim("t4-c1", ["ירושלים"]),
        _claim("t5-c0", ["מכבי"]),
        _claim("t5-c1", ["כללית"]),
    ]
    entities = _cluster(claims, tmp_path)

    # Cross-script transliteration and same-script spelling variants merge.
    assert _cluster_of(entities, "עמית פלס") == _cluster_of(entities, "Amit Peles")
    assert _cluster_of(entities, "אל על") == _cluster_of(entities, "אלעל")
    assert _cluster_of(entities, "קופת חולים") == _cluster_of(entities, "קופות החולים")

    # Distinct same-type entities must NOT merge (the embedding failure mode).
    assert _cluster_of(entities, "תל אביב") != _cluster_of(entities, "ירושלים")
    assert _cluster_of(entities, "מכבי") != _cluster_of(entities, "כללית")


def test_prefix_and_cooccurrence_merge(tmp_path: Path):
    # "עו"ד הראל" normalizes to "הראל", a whole-word prefix of "הראל ברק", and the
    # two co-occur on one claim -> a must-link alias (not two entities).
    claims = [
        _claim("t1-c0", ['עו"ד הראל', "הראל ברק"], ["legal-lawyers"]),
        _claim("t1-c1", ["הראל ברק"], ["legal-lawyers"]),
    ]
    entities = _cluster(claims, tmp_path)
    assert _cluster_of(entities, 'עו"ד הראל') == _cluster_of(entities, "הראל ברק")
    ent = _entity_with(entities, "הראל ברק")
    assert ent["canonical_name"] == "הראל ברק"
    assert "prefix" in ent["merge_signals"]
    assert "co_occur" in ent["merge_signals"]


def test_topic_guard_holds_near_identical_short_names_apart(tmp_path: Path):
    # Same normalized short name, disjoint topics -> held apart and flagged for
    # review instead of silently merged.
    claims = [
        {
            "claim_id": "t1-c0",
            "thread_id": "t1",
            "claim_text": "lawyer note",
            "topic_tags": ["legal-lawyers"],
            "entities": ["הראל"],
        },
        {
            "claim_id": "t2-c0",
            "thread_id": "t2",
            "claim_text": "cost note",
            "topic_tags": ["money-costs"],
            "entities": ["הראל׳"],
        },
    ]
    entities = _cluster(claims, tmp_path)
    assert _cluster_of(entities, "הראל") != _cluster_of(entities, "הראל׳")
    ent = _entity_with(entities, "הראל")
    assert ent["status"] == "ambiguous"
    assert ent["conflict_with"]


def test_confident_contact_merges_distinct_spellings(tmp_path: Path):
    # Each name appears alone with the same email -> confident contact must-link.
    claims = [
        {
            "claim_id": "t1-c0",
            "thread_id": "t1",
            "claim_text": "Foo Clinic",
            "topic_tags": ["clinics"],
            "entities": ["Foo Clinic"],
            "_redactions": [{"type": "email", "value": "info@fooclinic.com"}],
        },
        {
            "claim_id": "t2-c0",
            "thread_id": "t2",
            "claim_text": "Foo Klinik",
            "topic_tags": ["clinics"],
            "entities": ["Foo Klinik"],
            "_redactions": [{"type": "email", "value": "info@fooclinic.com"}],
        },
    ]
    entities = _cluster(claims, tmp_path)
    assert _cluster_of(entities, "Foo Clinic") == _cluster_of(entities, "Foo Klinik")
    ent = _entity_with(entities, "Foo Clinic")
    assert "confident_contact" in ent["merge_signals"]


def test_multi_entity_contact_is_uncertain_and_no_merge(tmp_path: Path):
    # One email, two entities in the claim: unattributable -> uncertain, no merge.
    claims = [
        {
            "claim_id": "t1-c0",
            "thread_id": "t1",
            "claim_text": "Alpha Beta",
            "topic_tags": ["overview"],
            "entities": ["Alpha", "Beta"],
            "_redactions": [{"type": "email", "value": "shared@x.com"}],
        }
    ]
    entities = _cluster(claims, tmp_path)
    assert _cluster_of(entities, "Alpha") != _cluster_of(entities, "Beta")
    alpha = _entity_with(entities, "Alpha")
    assert alpha["contacts"]["email"] == []
    assert "shared@x.com" in alpha["contacts_uncertain"]["email"]


def test_resolver_topic_disambiguation():
    registry = [
        {
            "entity_id": "e0",
            "canonical_name": "הראל ברק",
            "aliases": ["הראל"],
            "members": [{"name": "הראל", "claim_ids": None}],
            "contacts": {},
            "topics": ["legal-lawyers"],
        },
        {
            "entity_id": "e1",
            "canonical_name": "הראל ביטוח",
            "aliases": ["הראל ביטוח"],
            "members": [
                {"name": "הראל", "claim_ids": None},
                {"name": "הראל ביטוח", "claim_ids": None},
            ],
            "contacts": {},
            "topics": ["insurance-newborn"],
        },
    ]
    resolver = EntityResolver(registry)
    assert resolver.canonical("הראל", topic_tags=["legal-lawyers"]) == "הראל ברק"
    assert resolver.canonical("הראל", topic_tags=["insurance-newborn"]) == "הראל ביטוח"


def test_normalize_strips_titles_and_phones():

    assert normalize_name('עו"ד הראל') == normalize_name("הראל")
    assert normalize_name("Dr. Cohen") == normalize_name("cohen")
    assert normalize_name("+1 (555) 010-2030") == "15550102030"


def test_seed_must_links_aliases(tmp_path: Path):
    seed_path = tmp_path / "seed.json"
    seed_path.write_text(
        json.dumps(
            {
                "entities": [
                    {
                        "id": "acme",
                        "canonical": "Acme Agency",
                        "aliases": ["Acme Agency", "Acme", "אקמי"],
                        "topics": ["choosing-agency"],
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
            "topic_tags": ["choosing-agency"],
            "entities": ["Acme Agency"],
        },
        {
            "claim_id": "t2-c0",
            "thread_id": "t2",
            "claim_text": "b",
            "topic_tags": ["choosing-agency"],
            "entities": ["אקמי"],
        },
    ]
    entities = _cluster(claims, tmp_path, seed_path=seed_path)
    ent = _entity_with(entities, "Acme Agency")
    assert _cluster_of(entities, "אקמי") == ent["entity_id"]
    assert ent["canonical_name"] == "Acme Agency"
    assert "seed" in ent["merge_signals"]


def test_transliteration_skeleton_aligns_across_scripts():
    assert transliteration_skeleton("עמית פלס") == transliteration_skeleton("Amit Peles")
    # David Shield: skeletons are close but not required to be identical.
    assert transliteration_skeleton("דלתא") == transliteration_skeleton("Delta")


def test_contacts_preserved_from_claims(tmp_path: Path):
    claim = _claim("t1-c0", ["David Shield"])
    claim["_redactions"] = [{"type": "email", "value": "x@davidshield.com"}]
    claim["claim_text"] = "ביטוח David Shield דרך davidshield.com"
    entities = _cluster([claim], tmp_path)
    contacts = entities[0]["contacts"]
    assert "x@davidshield.com" in contacts["email"]
    assert "davidshield.com" in contacts["website"]


def test_contacts_from_original_when_edited_lacks_redactions(tmp_path: Path):
    """PII review drops ``_redactions`` from edited claims; original still has them."""
    original = _claim("t1-c0", ["David Shield"])
    original["_redactions"] = [{"type": "email", "value": "x@davidshield.com"}]
    original["claim_text"] = 'ביטוח "David Shield" בדוא"ל [הוסר]'
    edited = _claim("t1-c0", ["David Shield"])
    edited["claim_text"] = (
        'ניתן לפנות ל-David Shield בדוא"ל joindavidshield@davidshield.com'
    )
    edited["_pii_review"] = "restored"
    original_by_id = {original["claim_id"]: original}

    emails, _phones, sites = _claim_contacts(edited, original)
    assert "x@davidshield.com" in emails
    assert "joindavidshield@davidshield.com" in emails
    assert "davidshield.com" in sites

    source = tmp_path / "claims.json"
    source.write_text(json.dumps({"claims": [edited]}), encoding="utf-8")
    entities = cluster_entities(
        collect_entities([edited], original_by_id=original_by_id),
        source,
    )
    contacts = entities[0]["contacts"]
    assert "joindavidshield@davidshield.com" in contacts["email"]


def test_contacts_from_claim_text_without_redactions():
    claim = _claim("t1-c0", ["David Shield"])
    claim["claim_text"] = (
        'ניתן לפנות ל-David Shield בדוא"ל joindavidshield@davidshield.com'
    )
    emails, phones, sites = _claim_contacts(claim)
    assert emails == ["joindavidshield@davidshield.com"]
    assert "davidshield.com" in sites


def test_collect_entities_includes_text_mentions():
    claims = [
        {
            "claim_id": "t1-c0",
            "thread_id": "t1",
            "claim_text": "לטיפת חלב יש תור",
            "topic_tags": ["bringing-baby-home"],
            "entities": ["טיפת חלב"],
        },
        {
            "claim_id": "t2-c0",
            "thread_id": "t2",
            "claim_text": "בטיפת חלב מקבלים חיסון",
            "topic_tags": ["israel"],
            "entities": ["כללית"],
        },
    ]
    entities = collect_entities(claims)
    tipat = next(e for e in entities if e["name"] == "טיפת חלב")
    assert tipat["count"] == 2
    assert set(tipat["claim_ids"]) == {"t1-c0", "t2-c0"}
    assert "israel" in tipat["topics"]


def test_short_hotline_phones_detected():
    from step_4_extract.scrub import find_phones

    text = 'מוקד משרד הבריאות (5400*), גם *5400'
    assert "(5400*)" in find_phones(text)
    assert "*5400" in find_phones(text)


def test_resolver_maps_name_and_claim_overrides():
    registry_entities = [
        {
            "entity_id": "e0000",
            "canonical_name": "תמוז",
            "aliases": ["תמוז", "Tammuz"],
            "members": [
                {"name": "תמוז", "claim_ids": None},
                {"name": "Tammuz", "claim_ids": None},
            ],
            "contacts": {"email": [], "phone": [], "website": []},
            "topics": ["tamuz"],
        },
        {
            "entity_id": "e0001",
            "canonical_name": "מיכל קרן דוד",
            "aliases": ["מיכל"],
            "members": [{"name": "מיכל", "claim_ids": ["t9-c0"]}],
            "contacts": {"email": [], "phone": [], "website": []},
            "topics": ["providers-other"],
        },
    ]
    resolver = EntityResolver(registry_entities)

    assert resolver.canonical("Tammuz") == "תמוז"
    assert resolver.canonical("מיכל", "t9-c0") == "מיכל קרן דוד"  # claim override
    assert resolver.canonical("מיכל", "t1-c0") == "מיכל"  # no override -> identity
    assert resolver.canonical("unknown") == "unknown"

    claim = _claim("t1-c0", ["Tammuz", "תמוז"])
    assert resolver.resolve_claim(claim) == ["תמוז"]  # deduped to canonical
    assert "תמוז" in resolver.registry()


def test_apply_resolution_and_missing_file_fallback(tmp_path: Path):
    # Missing file -> no resolver, claims untouched.
    missing = tmp_path / "nope.json"
    assert load_entity_resolver(missing) is None
    claim = _claim("t1-c0", ["אלעל"])
    apply_entity_resolution([claim], None)
    assert claim["entities"] == ["אלעל"]

    # Present file -> claims rewritten to canonical names.
    path = tmp_path / "entities.json"
    path.write_text(
        json.dumps(
            {
                "entities": [
                    {
                        "entity_id": "e0000",
                        "canonical_name": "אל על",
                        "aliases": ["אל על", "אלעל"],
                        "members": [
                            {"name": "אל על", "claim_ids": None},
                            {"name": "אלעל", "claim_ids": None},
                        ],
                        "contacts": {},
                        "topics": [],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    resolver = load_entity_resolver(path)
    assert resolver is not None
    claim2 = _claim("t1-c0", ["אלעל"])
    apply_entity_resolution([claim2], resolver)
    assert claim2["entities"] == ["אל על"]


def test_entity_distance_matrix_cache(tmp_path: Path):
    import numpy as np

    claims = [_claim("t1-c0", ["אל על"]), _claim("t1-c1", ["אלעל"])]
    source = tmp_path / "claims.json"
    source.write_text(json.dumps({"claims": claims}), encoding="utf-8")
    entities = collect_entities(claims)
    matrix_path = tmp_path / "entity_dist.npy"
    meta_path = tmp_path / "entity_dist.json"

    first = ensure_entity_distance_matrix(
        entities, source, matrix_path=matrix_path, meta_path=meta_path
    )
    second = ensure_entity_distance_matrix(
        entities, source, matrix_path=matrix_path, meta_path=meta_path
    )
    assert np.array_equal(first, second)

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["metadata"]["distance_method"] == DISTANCE_METHOD

    expected = _entity_distance_matrix_metadata(
        source,
        [e["name"] for e in entities],
        signature=EntityPairIndex(entities, [None] * len(entities)).signal_signature(),
    )
    assert not _entity_distance_matrix_need_rebuild(
        meta_path, matrix_path, expected
    )

    meta["metadata"]["distance_method"] = "old_method"
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    assert _entity_distance_matrix_need_rebuild(meta_path, matrix_path, expected)
