"""Step 4 entity resolution: clustering precision, transliteration, guard, resolver."""

from __future__ import annotations

import json
from collections.abc import Sequence
from itertools import count
from pathlib import Path

from step_4_entities.cluster import EntityClusterer
from step_4_entities.collect import ClaimCorpus, collect_entities
from step_4_entities.mentions import (
    MentionIndex,
    SimpleAnalyzer,
    analyze_claims,
    build_or_load_analysis,
    find_mentions,
    mentions_name,
)
from step_4_entities.normalize import normalize_name, transliteration_skeleton
from step_4_entities.records import claim_contacts
from step_5_aggregate.resolver import EntityResolver, load_entity_resolver, apply_entity_resolution


class FakeAnalyzer:
    """Deterministic, model-free analyzer for tests.

    Whitespace tokens with caller-supplied per-token ``core`` (mimicking dictabert
    prefix segmentation) and ``pos`` overrides. Lets us exercise proclitic stripping
    and the POS gate without loading the model.
    """

    model_name = "fake"

    def __init__(
        self,
        cores: dict[str, str] | None = None,
        pos: dict[str, str] | None = None,
        ner: set[str] | None = None,
    ) -> None:
        self._cores = cores or {}
        self._pos = pos or {}
        self._ner = ner or set()

    def analyze_batch(self, texts):
        return [
            [
                {
                    "token": tok,
                    "core": self._cores.get(tok, tok),
                    "prefixes": [],
                    "pos": self._pos.get(tok),
                    "lemma": None,
                    "ner": "ORG" if tok in self._ner else None,
                }
                for tok in (text or "").split()
            ]
            for text in texts
        ]


def _words(text: str, analyzer=None):
    analyzer = analyzer or SimpleAnalyzer()
    return analyze_claims([{"claim_id": "c", "claim_text": text}], analyzer)["c"]


def _claim(claim_id: str, entities: list[str], text: str | None = None) -> dict:
    return {
        "claim_id": claim_id,
        "thread_id": claim_id.split("-")[0],
        "claim_text": " / ".join(entities) if text is None else text,
        "entities": entities,
    }


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
        _claim("t1-c0", ['עו"ד הראל', "הראל ברק"]),
        _claim("t1-c1", ["הראל ברק"]),
    ]
    entities = _cluster(claims, tmp_path)
    assert _cluster_of(entities, 'עו"ד הראל') == _cluster_of(entities, "הראל ברק")
    ent = _entity_with(entities, "הראל ברק")
    assert ent["canonical_name"] == "הראל ברק"
    assert "prefix" in ent["merge_signals"]
    assert "co_occur" in ent["merge_signals"]


def test_context_guard_holds_look_alike_short_names_apart(tmp_path: Path):
    # Same short name up to punctuation, but the claims around them share no word:
    # held apart and flagged for review instead of silently merged.
    claims = [
        _claim("t1-c0", ["Gaya"], "Gaya lawyer handled the court papers"),
        _claim("t2-c0", ["Gayaa"], "Gayaa kindergarten opens at seven"),
    ]
    entities = _cluster(claims, tmp_path)
    assert _cluster_of(entities, "Gaya") != _cluster_of(entities, "Gayaa")
    ent = _entity_with(entities, "Gaya")
    assert ent["status"] == "ambiguous"
    assert ent["conflict_with"] == ["Gayaa"]


def test_context_guard_lets_look_alikes_with_shared_context_merge(tmp_path: Path):
    claims = [
        _claim("t1-c0", ["Gaya"], "Gaya agency charges a fee for the surrogacy process"),
        _claim("t2-c0", ["Gayaa"], "Gayaa agency fee for the surrogacy process is high"),
    ]
    entities = _cluster(claims, tmp_path)
    assert _cluster_of(entities, "Gaya") == _cluster_of(entities, "Gayaa")
    assert _entity_with(entities, "Gaya")["status"] == "suggested"


def test_context_guard_ignores_long_names(tmp_path: Path):
    # Long names are distinctive enough: string similarity alone decides.
    claims = [
        _claim("t1-c0", ["Sunrise Fertility Center"], "Sunrise Fertility Center court papers"),
        _claim("t2-c0", ["Sunrise Fertility Centre"], "Sunrise Fertility Centre kindergarten"),
    ]
    entities = _cluster(claims, tmp_path)
    assert _cluster_of(entities, "Sunrise Fertility Center") == _cluster_of(
        entities, "Sunrise Fertility Centre"
    )


def test_confident_contact_merges_distinct_spellings(tmp_path: Path):
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


def test_multi_entity_contact_is_uncertain_and_no_merge(tmp_path: Path):
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

    emails, _phones, sites = claim_contacts(edited, original)
    assert "x@davidshield.com" in emails
    assert "joindavidshield@davidshield.com" in emails
    assert "davidshield.com" in sites

    entities = _cluster([edited], tmp_path, original_by_id=original_by_id)
    contacts = entities[0]["contacts"]
    assert "x@davidshield.com" in contacts["email"]
    assert "joindavidshield@davidshield.com" in contacts["email"]


def test_contacts_from_claim_text_without_redactions():
    claim = _claim("t1-c0", ["David Shield"])
    claim["claim_text"] = (
        'ניתן לפנות ל-David Shield בדוא"ל joindavidshield@davidshield.com'
    )
    emails, phones, sites = claim_contacts(claim)
    assert emails == ["joindavidshield@davidshield.com"]
    assert "davidshield.com" in sites


def test_collect_entities_includes_text_mentions():
    claims = [
        {
            "claim_id": "t1-c0",
            "thread_id": "t1",
            "claim_text": "לטיפת חלב יש תור",
            "entities": ["טיפת חלב"],
        },
        {
            "claim_id": "t2-c0",
            "thread_id": "t2",
            "claim_text": "בטיפת חלב מקבלים חיסון",
            "entities": ["כללית"],
        },
    ]
    # dictabert segments the proclitic (לטיפת -> ל+טיפת); emulate that offline.
    analyzer = FakeAnalyzer(cores={"לטיפת": "טיפת", "בטיפת": "טיפת"})
    entities = collect_entities(claims, analyzer=analyzer)
    tipat = next(e for e in entities if e["name"] == "טיפת חלב")
    assert tipat["count"] == 2
    assert set(tipat["claim_ids"]) == {"t1-c0", "t2-c0"}
    assert "topics" not in tipat


def test_word_level_matching_rejects_inner_substrings():
    # The reported bug: a short name must not match inside a longer word.
    assert not mentions_name(_words("כללית מרפאה"), "כלל")
    assert not mentions_name(_words("באופן כללי מאוד"), "כלל")
    assert not mentions_name(_words("שנתיים חלפו מאז"), "נתי")
    # ...but the whole word, and multi-word phrases, still match.
    assert mentions_name(_words("כלל חברת ביטוח"), "כלל")
    assert mentions_name(_words("שירותי טיפת חלב בעיר"), "טיפת חלב")


def test_proclitic_prefix_matches_via_segmentation():
    # With segmentation, an attached Hebrew proclitic still matches, and the span
    # covers only the entity word (not the proclitic).
    analyzer = FakeAnalyzer(cores={"בטיפת": "טיפת"})
    words = _words("בטיפת חלב מחר", analyzer)
    spans = find_mentions(words, "טיפת חלב")
    assert spans
    start, end = spans[0]
    assert "בטיפת חלב מחר"[start:end] == "טיפת חלב"


def test_single_word_requires_name_like_evidence():
    # The reported "בדרך כלל" case: a single common Hebrew word matches only with
    # name-like evidence (PROPN or NER), not as a bare common noun / function word.
    idiom = FakeAnalyzer(pos={"כלל": "NOUN"})
    assert not mentions_name(_words("בדרך כלל זה בסדר", idiom), "כלל")
    adverb = FakeAnalyzer(pos={"כלל": "ADV"})
    assert not mentions_name(_words("זה לא כלל ברור", adverb), "כלל")
    # Proper-noun or NER context is accepted (the insurance company "כלל").
    propn = FakeAnalyzer(pos={"כלל": "PROPN"})
    assert mentions_name(_words("חברת כלל גדולה", propn), "כלל")
    # NER rescues an entity the model tags as ADJ (e.g. "כללית").
    ner = FakeAnalyzer(
        cores={"בכללית": "כללית"}, pos={"בכללית": "ADJ"}, ner={"בכללית"}
    )
    assert mentions_name(_words("רשומה בכללית היום", ner), "כללית")


def test_multi_word_match_is_not_gated_by_name_likeness():
    # Distinctive multi-word phrases (NOUN NOUN, no NER) stay matchable.
    plain = FakeAnalyzer(pos={"טיפת": "NOUN", "חלב": "NOUN"})
    assert mentions_name(_words("שירותי טיפת חלב בעיר", plain), "טיפת חלב")


def test_short_hotline_phones_detected():
    from step_3_extract.scrub import find_phones

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


class CountingAnalyzer(SimpleAnalyzer):
    """SimpleAnalyzer that records every text it was asked to analyze."""

    def __init__(self) -> None:
        self.seen: list[str] = []

    def analyze_batch(self, texts: Sequence[str]) -> list[list[dict]]:
        self.seen.extend(texts)
        return super().analyze_batch(texts)


def test_analysis_cache_is_per_claim(tmp_path: Path):
    cache = tmp_path / "analysis.json"
    claims = [_claim("t1-c0", ["Alpha"], "Alpha one"), _claim("t2-c0", ["Beta"], "Beta two")]
    first = CountingAnalyzer()
    build_or_load_analysis(claims, first, cache_path=cache)
    assert first.seen == ["Alpha one", "Beta two"]

    # Unchanged claims are never re-analyzed; a new or edited text is.
    claims[1]["claim_text"] = "Beta three"
    claims.append(_claim("t3-c0", ["Gamma"], "Gamma four"))
    second = CountingAnalyzer()
    analysis = build_or_load_analysis(claims, second, cache_path=cache)
    assert sorted(second.seen) == ["Beta three", "Gamma four"]
    assert set(analysis) == {"t1-c0", "t2-c0", "t3-c0"}

    third = CountingAnalyzer()
    build_or_load_analysis(claims, third, cache_path=cache)
    assert third.seen == []


def test_mention_index_matches_a_full_scan():
    analyzer = FakeAnalyzer(cores={"לטיפת": "טיפת", "בכללית": "כללית"})
    claims = [
        _claim("t1-c0", ["טיפת חלב"], "לטיפת חלב יש תור"),
        _claim("t2-c0", ["כללית"], "רשומה בכללית ובטיפת חלב"),
        _claim("t3-c0", [], "כללית מרפאה טיפת"),
    ]
    analysis = analyze_claims(claims, analyzer)
    index = MentionIndex(analysis)
    for name in ("טיפת חלב", "כללית", "טיפת", "חלב", "nothing"):
        scan = {cid for cid, words in analysis.items() if mentions_name(words, name)}
        assert index.claims_mentioning(name) == scan


def test_claim_corpus_names_and_claims():
    claims = [
        _claim("t1-c0", ["Alpha", "Beta"], "Alpha and Beta"),
        _claim("t2-c0", ["Alpha"], "Alpha again"),
        _claim("t3-c0", [], "Beta only in the text"),
    ]
    corpus = ClaimCorpus(claims, analyze_claims(claims, SimpleAnalyzer()))
    assert corpus.names() == ["Alpha", "Beta"]
    assert corpus.claims_for("Beta") == {"t1-c0", "t3-c0"}
    assert corpus.claims_for("Beta", within={"t3-c0"}) == {"t3-c0"}
