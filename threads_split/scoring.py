from __future__ import annotations

from datetime import datetime
from typing import Sequence

import numpy as np

from preprocessing.models import Message
from threads_split.embedding import cosine_similarity
from threads_split.models import ScoredCandidate, Thread, ThreadConfig


def lexical_jaccard(text_a: str, text_b: str) -> float:
    tokens_a = set(text_a.lower().split())
    tokens_b = set(text_b.lower().split())
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = tokens_a & tokens_b
    union = tokens_a | tokens_b
    return len(intersection) / len(union)


def semantic_similarity(
    message: Message,
    message_index: int,
    message_embedding: np.ndarray,
    thread: Thread,
    messages: Sequence[Message],
    message_embeddings: Sequence[np.ndarray],
    config: ThreadConfig,
) -> float:
    recent_ids = thread.process_indices[-config.recent_messages_for_semantic :]
    best_score = 0.0

    for thread_msg_id in recent_ids:
        thread_embedding = message_embeddings[thread_msg_id]
        cosine = cosine_similarity(message_embedding, thread_embedding)
        delta_n = abs(message_index - thread_msg_id)
        position_factor = config.position_decay_gamma ** delta_n
        lexical = lexical_jaccard(message.content, messages[thread_msg_id].content)
        combined = (1.0 - config.lexical_blend_weight) * cosine + config.lexical_blend_weight * lexical
        score = combined * position_factor
        best_score = max(best_score, score)

    summary_cosine = cosine_similarity(message_embedding, thread.summary_embedding)
    summary_lexical = max(
        (lexical_jaccard(message.content, messages[mid].content) for mid in recent_ids),
        default=0.0,
    )
    summary_score = (1.0 - config.lexical_blend_weight) * summary_cosine + config.lexical_blend_weight * summary_lexical
    return max(best_score, summary_score)


def time_proximity(message_time: datetime, thread_last_time: datetime, config: ThreadConfig) -> float:
    gap_minutes = max(0.0, (message_time - thread_last_time).total_seconds() / 60.0)
    return float(np.exp(-gap_minutes / config.tau_minutes))


def social_score(message: Message, thread: Thread) -> float:
    if message.sender == thread.last_sender:
        return 1.0
    if message.sender in thread.participants:
        return 0.75
    return 0.0


def attach_score(
    message: Message,
    message_index: int,
    message_embedding: np.ndarray,
    thread: Thread,
    messages: Sequence[Message],
    message_embeddings: Sequence[np.ndarray],
    config: ThreadConfig,
) -> ScoredCandidate:
    semantic = semantic_similarity(
        message, message_index, message_embedding, thread, messages, message_embeddings, config
    )
    temporal = time_proximity(message.datetime, thread.last_time, config)
    social = social_score(message, thread)
    total = (
        config.w_semantic * semantic
        + config.w_time * temporal
        + config.w_social * social
    )
    return ScoredCandidate(
        thread=thread,
        attach_score=total,
        semantic_score=semantic,
        time_score=temporal,
        social_score=social,
    )


def max_semantic_to_open_threads(
    message: Message,
    message_index: int,
    message_embedding: np.ndarray,
    open_threads: Sequence[Thread],
    messages: Sequence[Message],
    message_embeddings: Sequence[np.ndarray],
    config: ThreadConfig,
) -> float:
    if not open_threads:
        return 0.0
    return max(
        semantic_similarity(
            message, message_index, message_embedding, thread, messages, message_embeddings, config
        )
        for thread in open_threads
    )


def normalized_gap_from_previous(current_time: datetime, previous_time: datetime | None, config: ThreadConfig) -> float:
    if previous_time is None:
        return 1.0
    gap_minutes = max(0.0, (current_time - previous_time).total_seconds() / 60.0)
    return min(1.0, gap_minutes / config.gap_normalize_minutes)


def new_thread_score(
    message: Message,
    message_index: int,
    message_embedding: np.ndarray,
    open_threads: Sequence[Thread],
    messages: Sequence[Message],
    message_embeddings: Sequence[np.ndarray],
    previous_message_time: datetime | None,
    config: ThreadConfig,
) -> float:
    gap_component = normalized_gap_from_previous(message.datetime, previous_message_time, config)
    max_semantic = max_semantic_to_open_threads(
        message, message_index, message_embedding, open_threads, messages, message_embeddings, config
    )
    low_similarity_component = 1.0 - max_semantic
    return config.b1_gap * gap_component + config.b2_low_similarity * low_similarity_component


def decide_assignment(
    candidates: Sequence[ScoredCandidate],
    new_thread_score_value: float,
    config: ThreadConfig,
) -> Thread | None:
    if not candidates:
        return None

    ranked = sorted(candidates, key=lambda c: c.attach_score, reverse=True)
    best = ranked[0]
    second_best_score = ranked[1].attach_score if len(ranked) > 1 else 0.0

    if (
        best.attach_score >= config.attach_threshold
        and (best.attach_score - second_best_score) >= config.margin
        and best.attach_score > new_thread_score_value
    ):
        return best.thread
    return None
