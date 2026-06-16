from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Sequence, TypeVar

import numpy as np

from preprocessing.models import Message
from threads_split.embedding.embedding import cosine_similarity
from threads_split.models import ScoredCandidate, Thread, ThreadConfig
from threads_split.tf_idf.tfidf import TfidfCorpus, TokenizedMessages, tfidf_cosine_similarity

DEFAULT_CORPUS_PATH = Path("data/tfidf_corpus.json")
DEFAULT_TOKENS_PATH = Path("data/tfidf_tokens.json")

T = TypeVar("T")


def _source_matches(metadata: dict, input_path: Path | str) -> bool:
    stored = metadata.get("source")
    if stored is None:
        return False
    return Path(stored).resolve() == Path(input_path).resolve()


def load_tfidf_resources(
    input_path: Path | str | None = None,
    corpus_path: Path | str | None = None,
    tokens_path: Path | str | None = None,
) -> tuple[TfidfCorpus, TokenizedMessages]:
    corpus_file = Path(corpus_path or DEFAULT_CORPUS_PATH)
    tokens_file = Path(tokens_path or DEFAULT_TOKENS_PATH)

    needs_build = not corpus_file.exists() or not tokens_file.exists()
    if not needs_build and input_path is not None:
        corpus = TfidfCorpus.load(corpus_file)
        tokens = TokenizedMessages.load(tokens_file)
        if not _source_matches(corpus.metadata, input_path) or not _source_matches(
            tokens.metadata, input_path
        ):
            needs_build = True
        else:
            return corpus, tokens

    if not needs_build:
        return TfidfCorpus.load(corpus_file), TokenizedMessages.load(tokens_file)

    from threads_split.tf_idf.prepare_tfidf import run as prepare_tfidf

    prepare_kwargs: dict = {
        "output_path": corpus_file,
        "tokens_output_path": tokens_file,
    }
    if input_path is not None:
        prepare_kwargs["input_path"] = input_path
    prepare_tfidf(**prepare_kwargs)
    return TfidfCorpus.load(corpus_file), TokenizedMessages.load(tokens_file)


def social_score(
    message: Message,
    thread: Thread,
    *,
    previous_message_index: int | None = None,
    previous_message_in_thread: bool = False,
) -> float:
    participants = thread.participants
    sender = message.sender
    union_size = len(participants | {sender})
    jaccard = len(participants & {sender}) / union_size if union_size else 0.0

    continuity = 0.0
    if sender == thread.last_sender:
        continuity = 1.0
    elif sender in participants:
        continuity = 0.75

    score = 0.6 * continuity + 0.4 * jaccard

    if previous_message_in_thread and previous_message_index is not None:
        score = min(1.0, score + 0.25)

    return score


class ThreadScorer:
    def __init__(
        self,
        config: ThreadConfig,
        messages: Sequence[Message],
        message_embeddings: Sequence[np.ndarray],
        query_embeddings: Sequence[np.ndarray] | None = None,
        tfidf_corpus: TfidfCorpus | None = None,
        tokenized_messages: TokenizedMessages | None = None,
        input_path: Path | str | None = None,
        corpus_path: Path | str = DEFAULT_CORPUS_PATH,
        tokens_path: Path | str = DEFAULT_TOKENS_PATH,
    ):
        self.config = config
        self.messages = messages
        self.message_embeddings = message_embeddings
        self.query_embeddings = (
            query_embeddings if query_embeddings is not None else message_embeddings
        )
        if tfidf_corpus is None or tokenized_messages is None:
            tfidf_corpus, tokenized_messages = load_tfidf_resources(
                input_path=input_path,
                corpus_path=corpus_path,
                tokens_path=tokens_path,
            )
        self.tfidf_corpus = tfidf_corpus
        self.tokenized_messages = tokenized_messages

    def query_embedding_for(self, message_index: int) -> np.ndarray:
        return self.query_embeddings[message_index]

    def embedding_semantic_similarity(
        self,
        message_index: int,
        thread: Thread,
    ) -> float:
        query_embedding = self.query_embedding_for(message_index)
        return self._position_decayed_similarity(
            message_index,
            thread,
            query_embedding,
            lambda index: self.message_embeddings[index],
            thread.recent_embeddings_mean,
            cosine_similarity,
        )

    def semantic_similarity(
        self,
        message_index: int,
        message_embedding: np.ndarray,
        thread: Thread,
    ) -> float:
        embedding_score = self.embedding_semantic_similarity(message_index, thread)
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
        *,
        previous_message_index: int | None = None,
    ) -> ScoredCandidate:
        semantic = self.semantic_similarity(message_index, message_embedding, thread)
        temporal = self.time_proximity(message.datetime, thread.last_time)
        previous_in_thread = (
            previous_message_index is not None
            and previous_message_index in thread.message_ids
        )
        social = social_score(
            message,
            thread,
            previous_message_index=previous_message_index,
            previous_message_in_thread=previous_in_thread,
        )
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

    def gap_minutes(self, later: datetime, earlier: datetime) -> float:
        return max(0.0, (later - earlier).total_seconds() / 60.0)

    def has_short_gap(self, message_time: datetime, reference_time: datetime) -> bool:
        return self.gap_minutes(message_time, reference_time) <= self.config.short_gap_exempt_minutes

    def find_quoted_index_in_thread(
        self,
        message_index: int,
        thread_message_ids: Sequence[int],
    ) -> int | None:
        message = self.messages[message_index]
        key = message.quote_lookup_key()
        if key is None:
            return None
        for candidate_index in reversed(thread_message_ids):
            if candidate_index >= message_index:
                continue
            quoted = self.messages[candidate_index]
            if (quoted.sender, quoted.normalized_content()) == key:
                return candidate_index
        return None

    def thread_quote_components(self, thread: Thread) -> dict[int, int]:
        parent = {message_index: message_index for message_index in thread.message_ids}

        def find(message_index: int) -> int:
            while parent[message_index] != message_index:
                parent[message_index] = parent[parent[message_index]]
                message_index = parent[message_index]
            return message_index

        def union(left: int, right: int) -> None:
            left_root = find(left)
            right_root = find(right)
            if left_root != right_root:
                parent[right_root] = left_root

        for message_index in thread.message_ids:
            quoted_index = self.find_quoted_index_in_thread(message_index, thread.message_ids)
            if quoted_index is not None:
                union(message_index, quoted_index)

        return {message_index: find(message_index) for message_index in thread.message_ids}

    def boundary_splits_quote_component(
        self,
        thread: Thread,
        split_after_pos: int,
        quote_components: dict[int, int] | None = None,
    ) -> bool:
        if quote_components is None:
            quote_components = self.thread_quote_components(thread)

        head_ids = set(thread.message_ids[: split_after_pos + 1])
        head_roots = {quote_components[message_index] for message_index in head_ids}
        for message_index in thread.message_ids[split_after_pos + 1 :]:
            if quote_components[message_index] in head_roots:
                return True
        return False

    def score_split_boundary(
        self,
        thread: Thread,
        split_after_pos: int,
        quote_components: dict[int, int] | None = None,
    ) -> float:
        message_ids = thread.message_ids
        if split_after_pos < 0 or split_after_pos >= len(message_ids) - 1:
            return float("-inf")

        if self.boundary_splits_quote_component(thread, split_after_pos, quote_components):
            return float("-inf")

        idx_before = message_ids[split_after_pos]
        idx_after = message_ids[split_after_pos + 1]
        msg_before = self.messages[idx_before]
        msg_after = self.messages[idx_after]

        gap = self.gap_minutes(msg_after.datetime, msg_before.datetime)
        time_score = min(1.0, gap / self.config.gap_normalize_minutes)
        semantic_gap = 1.0 - cosine_similarity(
            self.message_embeddings[idx_before],
            self.message_embeddings[idx_after],
        )
        score = (
            self.config.split_time_weight * time_score
            + self.config.split_semantic_weight * semantic_gap
        )

        if self.has_short_gap(msg_after.datetime, msg_before.datetime):
            score -= self.config.split_short_gap_penalty
        return score

    def find_best_split_point(self, thread: Thread) -> int | None:
        message_ids = thread.message_ids
        if len(message_ids) <= self.config.long_thread_message_limit:
            return None

        min_part = self.config.long_thread_min_part_size
        if len(message_ids) < 2 * min_part:
            return None

        quote_components = self.thread_quote_components(thread)
        best_pos: int | None = None
        best_score = float("-inf")
        for pos in range(min_part - 1, len(message_ids) - min_part):
            score = self.score_split_boundary(thread, pos, quote_components)
            if score > best_score:
                best_score = score
                best_pos = pos

        if best_pos is None or best_score < self.config.split_boundary_threshold:
            return None
        return best_pos

    def new_thread_score(
        self,
        message: Message,
        previous_message_time: datetime | None,
        candidates: Sequence[ScoredCandidate],
    ) -> float:
        gap_component = self.normalized_gap_from_previous(message.datetime, previous_message_time)
        max_semantic = max(c.semantic_score for c in candidates) if candidates else 0.0
        low_similarity_component = 1.0 - max_semantic
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

    def find_closed_thread_match(
        self,
        message_index: int,
        message_time: datetime,
        closed_threads: Sequence[Thread],
    ) -> Thread | None:
        lookback = timedelta(hours=self.config.closed_thread_lookback_hours)
        eligible = [
            thread
            for thread in closed_threads
            if message_time - thread.last_time <= lookback
        ]
        if not eligible:
            return None

        best_thread: Thread | None = None
        best_semantic = 0.0
        for thread in eligible:
            semantic = self.embedding_semantic_similarity(message_index, thread)
            if semantic > best_semantic:
                best_semantic = semantic
                best_thread = thread

        if (
            best_thread is not None
            and best_semantic >= self.config.closed_thread_reopen_semantic_threshold
        ):
            return best_thread
        return None
