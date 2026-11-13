"""Model-free helpers for the step-4 entity tests: a fake analyzer, claims and members."""

from __future__ import annotations

from collections.abc import Sequence

from step_4_entities.collect import ClaimCorpus, tagged_names
from step_4_entities.mentions import Analyzer, SimpleAnalyzer, Word, analyze_claims


def collect_entities(
    claims: list[dict],
    *,
    original_by_id: dict | None = None,
    analysis: dict[str, list[Word]] | None = None,
    analyzer: Analyzer | None = None,
) -> list[dict]:
    """One member record per distinct entity string, most claims first (``analysis`` is built
    with ``analyzer``, default the model-free ``SimpleAnalyzer``, when not given). Names are
    taken most-tagged first, ties by name, so the order is stable."""

    if analysis is None:
        analysis = analyze_claims(claims, analyzer or SimpleAnalyzer())
    corpus = ClaimCorpus(claims, analysis, original_by_id)
    tagged = {name for claim in corpus.claims_by_id.values() for name in tagged_names(claim)}
    names = sorted(tagged, key=lambda n: (-len(corpus.tagged_claims(n)), n))
    members = [corpus.member(name, corpus.claims_for(name)) for name in names]
    members.sort(key=lambda m: m["count"], reverse=True)
    return members


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

    def analyze_batch(self, texts: Sequence[str]) -> list[list[Word]]:
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


def analyzed_words(text: str, analyzer: Analyzer | None = None) -> list[Word]:
    analyzer = analyzer or SimpleAnalyzer()
    return analyze_claims([{"claim_id": "c", "claim_text": text}], analyzer)["c"]


def entity_claim(claim_id: str, entities: list[str], text: str | None = None) -> dict:
    return {
        "claim_id": claim_id,
        "thread_id": claim_id.split("-")[0],
        "claim_text": " / ".join(entities) if text is None else text,
        "entities": entities,
    }
