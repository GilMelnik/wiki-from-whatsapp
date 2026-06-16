from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Sequence

import numpy as np

if TYPE_CHECKING:
    from preprocessing.models import Message


@dataclass
class ThreadConfig:
    w_semantic: float = 0.60
    w_time: float = 0.20
    w_social: float = 0.20
    w_tfidf: float = 0.45
    attach_threshold: float = 0.45
    margin: float = 0.02
    tau_minutes: float = 180.0
    max_open_threads: int = 5
    close_after_hours: float = 12.0
    recent_messages_for_semantic: int = 20
    position_decay_gamma: float = 0.95
    b1_gap: float = 0.4
    b2_low_similarity: float = 0.6
    gap_normalize_minutes: float = 360.0
    long_thread_message_limit: int = 50
    long_thread_min_part_size: int = 10
    short_gap_exempt_minutes: float = 5.0
    monologue_gap_minutes: float = 10.0
    monologue_semantic_floor: float = 0.15
    closed_thread_lookback_hours: float = 168.0
    closed_thread_reopen_semantic_threshold: float = 0.45
    split_time_weight: float = 0.5
    split_semantic_weight: float = 0.5
    split_short_gap_penalty: float = 0.5
    split_boundary_threshold: float = 0.4
    embedding_model: str = "intfloat/multilingual-e5-large"
    recent_embeddings_window: int = 20
    assignment_debug_path: Path | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "w_semantic": self.w_semantic,
            "w_time": self.w_time,
            "w_social": self.w_social,
            "w_tfidf": self.w_tfidf,
            "attach_threshold": self.attach_threshold,
            "margin": self.margin,
            "tau_minutes": self.tau_minutes,
            "max_open_threads": self.max_open_threads,
            "close_after_hours": self.close_after_hours,
            "recent_messages_for_semantic": self.recent_messages_for_semantic,
            "position_decay_gamma": self.position_decay_gamma,
            "b1_gap": self.b1_gap,
            "b2_low_similarity": self.b2_low_similarity,
            "gap_normalize_minutes": self.gap_normalize_minutes,
            "long_thread_message_limit": self.long_thread_message_limit,
            "long_thread_min_part_size": self.long_thread_min_part_size,
            "short_gap_exempt_minutes": self.short_gap_exempt_minutes,
            "monologue_gap_minutes": self.monologue_gap_minutes,
            "monologue_semantic_floor": self.monologue_semantic_floor,
            "closed_thread_lookback_hours": self.closed_thread_lookback_hours,
            "closed_thread_reopen_semantic_threshold": self.closed_thread_reopen_semantic_threshold,
            "split_time_weight": self.split_time_weight,
            "split_semantic_weight": self.split_semantic_weight,
            "split_short_gap_penalty": self.split_short_gap_penalty,
            "split_boundary_threshold": self.split_boundary_threshold,
            "embedding_model": self.embedding_model,
            "recent_embeddings_window": self.recent_embeddings_window,
            "assignment_debug_path": (
                str(self.assignment_debug_path) if self.assignment_debug_path else None
            ),
        }


class Thread:
    _counter = 0

    def __init__(
        self,
        thread_id: str,
        start_time: datetime,
        last_time: datetime,
        participants: set[str],
        message_ids: list[int],
        last_sender: str,
        num_messages: int,
        num_unique_senders: int,
        recent_embeddings_mean: np.ndarray,
        recent_embeddings_window: int,
        is_open: bool = True,
        recent_embeddings: list[np.ndarray] | None = None,
    ):
        self.thread_id = thread_id
        self.start_time = start_time
        self.last_time = last_time
        self.participants = participants
        self.message_ids = message_ids
        self.last_sender = last_sender
        self.num_messages = num_messages
        self.num_unique_senders = num_unique_senders
        self.recent_embeddings_mean = recent_embeddings_mean
        self.recent_embeddings_window = recent_embeddings_window
        self.is_open = is_open
        self.recent_embeddings = recent_embeddings or []

    @classmethod
    def create(
        cls,
        message_index: int,
        message: Message,
        embedding: np.ndarray,
        recent_embeddings_window: int,
    ) -> Thread:
        cls._counter += 1
        thread_id = f"thread-{cls._counter:04d}"
        participants = {message.sender}
        return cls(
            thread_id=thread_id,
            start_time=message.datetime,
            last_time=message.datetime,
            participants=participants,
            message_ids=[message_index],
            last_sender=message.sender,
            num_messages=1,
            num_unique_senders=1,
            recent_embeddings_mean=embedding.copy(),
            recent_embeddings_window=recent_embeddings_window,
            is_open=True,
            recent_embeddings=[embedding.copy()],
        )

    def add_message(
        self,
        message_index: int,
        message: Message,
        embedding: np.ndarray,
    ) -> None:
        self.message_ids.append(message_index)
        self.last_time = message.datetime
        self.last_sender = message.sender
        self.num_messages += 1
        if message.sender not in self.participants:
            self.participants.add(message.sender)
            self.num_unique_senders += 1

        self.recent_embeddings.append(embedding.copy())
        if len(self.recent_embeddings) > self.recent_embeddings_window:
            self.recent_embeddings = self.recent_embeddings[-self.recent_embeddings_window :]

        self.recent_embeddings_mean = np.mean(self.recent_embeddings, axis=0)

    def add_participants(self, senders: set[str]) -> None:
        for sender in senders:
            if sender not in self.participants:
                self.participants.add(sender)
                self.num_unique_senders += 1

    def add_reaction_participants(self, reactions: list[dict]) -> None:
        reaction_senders: set[str] = set()
        for reaction in reactions:
            for sender in reaction.get("senders", []):
                reaction_senders.add(sender)
        self.add_participants(reaction_senders)

    def rebuild_metadata(
        self,
        messages: Sequence[Message],
        embeddings: Sequence[np.ndarray],
    ) -> None:
        self.num_messages = len(self.message_ids)
        first = messages[self.message_ids[0]]
        last = messages[self.message_ids[-1]]
        self.start_time = first.datetime
        self.last_time = last.datetime
        self.last_sender = last.sender
        participants: set[str] = set()
        for message_index in self.message_ids:
            participants.add(messages[message_index].sender)
        self.participants = participants
        self.num_unique_senders = len(participants)

        self.recent_embeddings = [
            embeddings[message_index].copy()
            for message_index in self.message_ids[-self.recent_embeddings_window :]
        ]
        self.recent_embeddings_mean = np.mean(self.recent_embeddings, axis=0)

    @classmethod
    def from_message_indices(
        cls,
        message_indices: list[int],
        messages: Sequence[Message],
        embeddings: Sequence[np.ndarray],
        recent_embeddings_window: int,
    ) -> Thread:
        cls._counter += 1
        thread_id = f"thread-{cls._counter:04d}"
        thread = cls(
            thread_id=thread_id,
            start_time=messages[message_indices[0]].datetime,
            last_time=messages[message_indices[-1]].datetime,
            participants=set(),
            message_ids=message_indices.copy(),
            last_sender=messages[message_indices[-1]].sender,
            num_messages=len(message_indices),
            num_unique_senders=0,
            recent_embeddings_mean=embeddings[message_indices[0]].copy(),
            recent_embeddings_window=recent_embeddings_window,
            is_open=True,
            recent_embeddings=[],
        )
        thread.rebuild_metadata(messages, embeddings)
        return thread

    def split_after(
        self,
        split_after_pos: int,
        messages: Sequence[Message],
        embeddings: Sequence[np.ndarray],
    ) -> Thread:
        tail_ids = self.message_ids[split_after_pos + 1 :]
        self.message_ids = self.message_ids[: split_after_pos + 1]
        self.rebuild_metadata(messages, embeddings)
        tail = Thread.from_message_indices(
            tail_ids,
            messages,
            embeddings,
            self.recent_embeddings_window,
        )
        tail.is_open = self.is_open
        return tail

    def to_dict(self, messages: Sequence[Message]) -> dict[str, Any]:
        thread_messages = []
        for message_index in self.message_ids:
            message = messages[message_index]
            thread_messages.append(message.to_dict())
        return {
            "thread_id": self.thread_id,
            "start_time": self.start_time.isoformat(),
            "last_time": self.last_time.isoformat(),
            "participants": sorted(self.participants),
            "message_ids": self.message_ids,
            "num_messages": self.num_messages,
            "num_unique_senders": self.num_unique_senders,
            "last_sender": self.last_sender,
            "messages": thread_messages,
        }


@dataclass
class ScoredCandidate:
    thread: Thread
    attach_score: float
    semantic_score: float
    time_score: float
    social_score: float
