from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Callable, Sequence, TypeVar

import numpy as np

from preprocessing.models import Message
from threads_split.embedding import cosine_similarity
from threads_split.models import ScoredCandidate, Thread, ThreadConfig
from threads_split.tfidf import TfidfCorpus, TokenizedMessages, tfidf_cosine_similarity

DEFAULT_CORPUS_PATH = Path("data/tfidf_corpus.json")
DEFAULT_TOKENS_PATH = Path("data/tfidf_tokens.json")

T = TypeVar("T")


def load_tfidf_resources(
    corpus_path: Path | str = DEFAULT_CORPUS_PATH,
    tokens_path: Path | str = DEFAULT_TOKENS_PATH,
) -> tuple[TfidfCorpus, TokenizedMessages]:
    corpus_file = Path(corpus_path)
    tokens_file = Path(tokens_path)
    if not corpus_file.exists() or not tokens_file.exists():
        from threads_split.prepare_tfidf import run as prepare_tfidf

        prepare_tfidf(
            output_path=corpus_file,
            tokens_output_path=tokens_file,
        )
    return TfidfCorpus.load(corpus_file), TokenizedMessages.load(tokens_file)


def social_score(message: Message, thread: Thread) -> float:
    if message.sender == thread.last_sender:
        return 1.0
    if message.sender in thread.participants:
        return 0.75
    return 0.0


class ThreadScorer:
    def __init__(
        self,
        config: ThreadConfig,
        messages: Sequence[Message],
        message_embeddings: Sequence[np.ndarray],
        tfidf_corpus: TfidfCorpus | None = None,
        tokenized_messages: TokenizedMessages | None = None,
        corpus_path: Path | str = DEFAULT_CORPUS_PATH,
        tokens_path: Path | str = DEFAULT_TOKENS_PATH,
    ):
        self.config = config
        self.messages = messages
        self.message_embeddings = message_embeddings
        if tfidf_corpus is None or tokenized_messages is None:
            tfidf_corpus, tokenized_messages = load_tfidf_resources(corpus_path, tokens_path)
        self.tfidf_corpus = tfidf_corpus
        self.tokenized_messages = tokenized_messages

    def semantic_similarity(
        self,
        message_index: int,
        message_embedding: np.ndarray,
        thread: Thread,
    ) -> float:
        embedding_score = self._position_decayed_similarity(
            message_index,
            thread,
            message_embedding,
            lambda index: self.message_embeddings[index],
            thread.recent_embeddings_mean,
            cosine_similarity,
        )
        message_tokens = self.tokenized_messages.tokens_for(message_index)
        recent_ids_for_mean = thread.message_ids[-self.config.recent_embeddings_window :]
        recent_tokens = [
            token
            for thread_message_index in recent_ids_for_mean
            for token in self.tokenized_messages.tokens_for(thread_message_index)
        ]
        tfidf_score = self._position_decayed_similarity(
            message_index,
            thread,
            message_tokens,
            lambda index: self.tokenized_messages.tokens_for(index),
            recent_tokens,
            lambda left, right: tfidf_cosine_similarity(left, right, self.tfidf_corpus),
        )
        w_tfidf = self.config.w_tfidf
        w_embedding = 1.0 - w_tfidf
        return w_embedding * embedding_score + w_tfidf * tfidf_score

    def _position_decayed_similarity(
        self,
        message_index: int,
        thread: Thread,
        message_repr: T,
        thread_repr_for_index: Callable[[int], T],
        recent_aggregate_repr: T,
        similarity: Callable[[T, T], float],
    ) -> float:
        recent_ids = thread.message_ids[-self.config.recent_messages_for_semantic :]
        best_score = 0.0

        for thread_message_index in recent_ids:
            cosine = similarity(message_repr, thread_repr_for_index(thread_message_index))
            delta_n = abs(message_index - thread_message_index)
            position_factor = self.config.position_decay_gamma ** delta_n
            best_score = max(best_score, cosine * position_factor)

        aggregate_cosine = similarity(message_repr, recent_aggregate_repr)
        return max(best_score, aggregate_cosine)

    def attach_score(
        self,
        message: Message,
        message_index: int,
        message_embedding: np.ndarray,
        thread: Thread,
    ) -> ScoredCandidate:
        semantic = self.semantic_similarity(message_index, message_embedding, thread)
        temporal = self.time_proximity(message.datetime, thread.last_time)
        social = social_score(message, thread)
        total = (
            self.config.w_semantic * semantic
            + self.config.w_time * temporal
            + self.config.w_social * social
        )
        return ScoredCandidate(
            thread=thread,
            attach_score=total,
            semantic_score=semantic,
            time_score=temporal,
            social_score=social,
        )

    def time_proximity(self, message_time: datetime, thread_last_time: datetime) -> float:
        gap_minutes = max(0.0, (message_time - thread_last_time).total_seconds() / 60.0)
        return float(np.exp(-gap_minutes / self.config.tau_minutes))

    def new_thread_score(
        self,
        message: Message,
        previous_message_time: datetime | None,
        candidates: Sequence[ScoredCandidate],
    ) -> float:
        gap_component = self.normalized_gap_from_previous(message.datetime, previous_message_time)
        max_candidate = max(candidates.attach_score for candidates in candidates) if candidates else 0.0
        low_similarity_component = 1.0 - max_candidate
        return self.config.b1_gap * gap_component + self.config.b2_low_similarity * low_similarity_component

    def normalized_gap_from_previous(self, current_time: datetime, previous_time: datetime | None) -> float:
        if previous_time is None:
            return 1.0
        gap_minutes = max(0.0, (current_time - previous_time).total_seconds() / 60.0)
        return min(1.0, gap_minutes / self.config.gap_normalize_minutes)

    def decide_assignment(
        self,
        candidates: Sequence[ScoredCandidate],
        new_thread_score_value: float,
    ) -> Thread | None:
        if not candidates:
            return None

        ranked = sorted(candidates, key=lambda c: c.attach_score, reverse=True)
        best = ranked[0]
        second_best_score = ranked[1].attach_score if len(ranked) > 1 else 0.0

        if (
            best.attach_score >= self.config.attach_threshold
            and (best.attach_score - second_best_score) >= self.config.margin
            and best.attach_score > new_thread_score_value
        ):
            return best.thread
        return None
