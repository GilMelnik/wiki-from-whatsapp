from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path
from typing import Any, Sequence

IDF_FORMULA = "log((1 + N) / (1 + df)) + 1"


def compute_idf(document_count: int, document_frequency: int) -> float:
    return math.log((1 + document_count) / (1 + document_frequency)) + 1


class TfidfCorpus:
    def __init__(
        self,
        document_count: int,
        terms: dict[str, dict[str, float | int]],
        default_idf: float,
        metadata: dict[str, Any] | None = None,
    ):
        self.document_count = document_count
        self.terms = terms
        self.default_idf = default_idf
        self.metadata = metadata or {}

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> TfidfCorpus:
        metadata = payload["metadata"]
        terms = payload["terms"]
        default_idf = metadata.get(
            "default_idf",
            compute_idf(metadata["document_count"], 0),
        )
        return cls(
            document_count=metadata["document_count"],
            terms=terms,
            default_idf=default_idf,
            metadata=metadata,
        )

    @classmethod
    def load(cls, path: Path | str) -> TfidfCorpus:
        with Path(path).open(encoding="utf-8") as f:
            return cls.from_dict(json.load(f))

    def idf(self, term: str) -> float:
        term_stats = self.terms.get(term)
        if term_stats is None:
            return self.default_idf
        return float(term_stats["idf"])

    def message_weights(self, tokens: Sequence[str]) -> dict[str, float]:
        if not tokens:
            return {}

        counts = Counter(tokens)
        total_tokens = len(tokens)
        return {
            term: (count / total_tokens) * self.idf(term)
            for term, count in counts.items()
        }


class TokenizedMessages:
    def __init__(
        self,
        tokenized_messages: list[list[str]],
        metadata: dict[str, Any] | None = None,
    ):
        self.tokenized_messages = tokenized_messages
        self.metadata = metadata or {}

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> TokenizedMessages:
        return cls(
            tokenized_messages=payload["tokenized_messages"],
            metadata=payload.get("metadata", {}),
        )

    @classmethod
    def load(cls, path: Path | str) -> TokenizedMessages:
        with Path(path).open(encoding="utf-8") as f:
            return cls.from_dict(json.load(f))

    def tokens_for(self, message_index: int) -> list[str]:
        return self.tokenized_messages[message_index]

    def tokens_for_indices(self, message_indices: Sequence[int]) -> list[list[str]]:
        return [self.tokenized_messages[index] for index in message_indices]


def sparse_cosine_similarity(
    weights_a: dict[str, float],
    weights_b: dict[str, float],
) -> float:
    if not weights_a or not weights_b:
        return 0.0

    shared_terms = set(weights_a) & set(weights_b)
    dot = sum(weights_a[term] * weights_b[term] for term in shared_terms)
    norm_a = math.sqrt(sum(value * value for value in weights_a.values()))
    norm_b = math.sqrt(sum(value * value for value in weights_b.values()))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return float(dot / (norm_a * norm_b))


def tfidf_cosine_similarity(
    tokens_a: Sequence[str],
    tokens_b: Sequence[str],
    corpus: TfidfCorpus,
) -> float:
    weights_a = corpus.message_weights(tokens_a)
    weights_b = corpus.message_weights(tokens_b)
    return sparse_cosine_similarity(weights_a, weights_b)


def tfidf_cosine_similarity_by_index(
    message_index_a: int,
    message_index_b: int,
    corpus: TfidfCorpus,
    tokenized_messages: TokenizedMessages,
) -> float:
    return tfidf_cosine_similarity(
        tokenized_messages.tokens_for(message_index_a),
        tokenized_messages.tokens_for(message_index_b),
        corpus,
    )
