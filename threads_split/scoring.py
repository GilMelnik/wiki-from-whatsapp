from __future__ import annotations

from datetime import datetime
from typing import Sequence

import numpy as np

from preprocessing.models import Message
from threads_split.embedding import cosine_similarity
from threads_split.models import ScoredCandidate, Thread, ThreadConfig


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
    ):
        self.config = config
        self.messages = messages
        self.message_embeddings = message_embeddings

    def semantic_similarity(
        self,
        message_index: int,
        message_embedding: np.ndarray,
        thread: Thread,
    ) -> float:
        recent_ids = thread.message_ids[-self.config.recent_messages_for_semantic :]
        best_score = 0.0

        for thread_message_index in recent_ids:
            thread_embedding = self.message_embeddings[thread_message_index]
            cosine = cosine_similarity(message_embedding, thread_embedding)
            delta_n = abs(message_index - thread_message_index)
            position_factor = self.config.position_decay_gamma ** delta_n
            score = cosine * position_factor
            best_score = max(best_score, score)

        recent_embeddings_cosine = cosine_similarity(message_embedding, thread.recent_embeddings_mean)
        return max(best_score, recent_embeddings_cosine)

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
