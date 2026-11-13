"""Step 4 mentions: word-level matching, the analysis cache, the mention index, the corpus."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from step_4_entities.collect import ClaimCorpus
from step_4_entities.mentions import (
    MentionIndex,
    SimpleAnalyzer,
    analyze_claims,
    build_or_load_analysis,
    find_mentions,
)
from tests.entities_fixtures import FakeAnalyzer, analyzed_words, collect_entities, entity_claim


def test_collect_entities_includes_text_mentions() -> None:
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


def test_word_level_matching_rejects_inner_substrings() -> None:
    # The reported bug: a short name must not match inside a longer word.
    assert not find_mentions(analyzed_words("כללית מרפאה"), "כלל")
    assert not find_mentions(analyzed_words("באופן כללי מאוד"), "כלל")
    assert not find_mentions(analyzed_words("שנתיים חלפו מאז"), "נתי")
    # ...but the whole word, and multi-word phrases, still match.
    assert find_mentions(analyzed_words("כלל חברת ביטוח"), "כלל")
    assert find_mentions(analyzed_words("שירותי טיפת חלב בעיר"), "טיפת חלב")


def test_proclitic_prefix_matches_via_segmentation() -> None:
    # With segmentation, an attached Hebrew proclitic still matches, and the span
    # covers only the entity word (not the proclitic).
    analyzer = FakeAnalyzer(cores={"בטיפת": "טיפת"})
    words = analyzed_words("בטיפת חלב מחר", analyzer)
    spans = find_mentions(words, "טיפת חלב")
    assert spans
    start, end = spans[0]
    assert "בטיפת חלב מחר"[start:end] == "טיפת חלב"


def test_single_word_requires_name_like_evidence() -> None:
    # The reported "בדרך כלל" case: a single common Hebrew word matches only with
    # name-like evidence (PROPN or NER), not as a bare common noun / function word.
    idiom = FakeAnalyzer(pos={"כלל": "NOUN"})
    assert not find_mentions(analyzed_words("בדרך כלל זה בסדר", idiom), "כלל")
    adverb = FakeAnalyzer(pos={"כלל": "ADV"})
    assert not find_mentions(analyzed_words("זה לא כלל ברור", adverb), "כלל")
    # Proper-noun or NER context is accepted (the insurance company "כלל").
    propn = FakeAnalyzer(pos={"כלל": "PROPN"})
    assert find_mentions(analyzed_words("חברת כלל גדולה", propn), "כלל")
    # NER rescues an entity the model tags as ADJ (e.g. "כללית").
    ner = FakeAnalyzer(
        cores={"בכללית": "כללית"}, pos={"בכללית": "ADJ"}, ner={"בכללית"}
    )
    assert find_mentions(analyzed_words("רשומה בכללית היום", ner), "כללית")


def test_multi_word_match_is_not_gated_by_name_likeness() -> None:
    # Distinctive multi-word phrases (NOUN NOUN, no NER) stay matchable.
    plain = FakeAnalyzer(pos={"טיפת": "NOUN", "חלב": "NOUN"})
    assert find_mentions(analyzed_words("שירותי טיפת חלב בעיר", plain), "טיפת חלב")


def test_short_hotline_phones_detected() -> None:
    from step_3_extract.scrub import find_phones

    text = 'מוקד משרד הבריאות (5400*), גם *5400'
    assert "(5400*)" in find_phones(text)
    assert "*5400" in find_phones(text)


class CountingAnalyzer(SimpleAnalyzer):
    """SimpleAnalyzer that records every text it was asked to analyze."""

    def __init__(self) -> None:
        self.seen: list[str] = []

    def analyze_batch(self, texts: Sequence[str]) -> list[list[dict]]:
        self.seen.extend(texts)
        return super().analyze_batch(texts)


def test_analysis_cache_is_per_claim(tmp_path: Path) -> None:
    cache = tmp_path / "analysis.json"
    claims = [entity_claim("t1-c0", ["Alpha"], "Alpha one"), entity_claim("t2-c0", ["Beta"], "Beta two")]
    first = CountingAnalyzer()
    build_or_load_analysis(claims, first, cache_path=cache)
    assert first.seen == ["Alpha one", "Beta two"]

    # Unchanged claims are never re-analyzed; a new or edited text is.
    claims[1]["claim_text"] = "Beta three"
    claims.append(entity_claim("t3-c0", ["Gamma"], "Gamma four"))
    second = CountingAnalyzer()
    analysis = build_or_load_analysis(claims, second, cache_path=cache)
    assert sorted(second.seen) == ["Beta three", "Gamma four"]
    assert set(analysis) == {"t1-c0", "t2-c0", "t3-c0"}

    third = CountingAnalyzer()
    build_or_load_analysis(claims, third, cache_path=cache)
    assert third.seen == []


def test_mention_index_matches_a_full_scan() -> None:
    analyzer = FakeAnalyzer(cores={"לטיפת": "טיפת", "בכללית": "כללית"})
    claims = [
        entity_claim("t1-c0", ["טיפת חלב"], "לטיפת חלב יש תור"),
        entity_claim("t2-c0", ["כללית"], "רשומה בכללית ובטיפת חלב"),
        entity_claim("t3-c0", [], "כללית מרפאה טיפת"),
    ]
    analysis = analyze_claims(claims, analyzer)
    index = MentionIndex(analysis)
    for name in ("טיפת חלב", "כללית", "טיפת", "חלב", "nothing"):
        scan = {cid for cid, words in analysis.items() if find_mentions(words, name)}
        assert index.claims_mentioning(name) == scan


def test_claim_corpus_claims() -> None:
    claims = [
        entity_claim("t1-c0", ["Alpha", "Beta"], "Alpha and Beta"),
        entity_claim("t2-c0", ["Alpha"], "Alpha again"),
        entity_claim("t3-c0", [], "Beta only in the text"),
    ]
    corpus = ClaimCorpus(claims, analyze_claims(claims, SimpleAnalyzer()))
    assert corpus.tagged_claims("Alpha") == {"t1-c0", "t2-c0"}
    assert corpus.claims_for("Beta") == {"t1-c0", "t3-c0"}
    assert corpus.claims_for("Beta", within={"t3-c0"}) == {"t3-c0"}
