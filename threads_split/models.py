from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Sequence

import numpy as np

if TYPE_CHECKING:
    from preprocessing.models import Message


@dataclass
class ThreadConfig:
    w_semantic: float = 0.55
    w_time: float = 0.25
    w_social: float = 0.20
    attach_threshold: float = 0.45
    margin: float = 0.08
    tau_minutes: float = 90.0
    max_open_threads: int = 5
    close_after_hours: float = 24.0
    recent_messages_for_semantic: int = 5
    position_decay_gamma: float = 0.95
    b1_gap: float = 0.4
    b2_low_similarity: float = 0.6
    gap_normalize_minutes: float = 360.0
    embedding_model: str = "intfloat/multilingual-e5-large"
    recent_embeddings_window: int = 5

    def to_dict(self) -> dict[str, Any]:
        return {
            "w_semantic": self.w_semantic,
            "w_time": self.w_time,
            "w_social": self.w_social,
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
            "embedding_model": self.embedding_model,
            "recent_embeddings_window": self.recent_embeddings_window,
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

    def to_dict(self, messages: Sequence[Message]) -> dict[str, Any]:
        thread_messages = []
        message_refs = []
        for message_index in self.message_ids:
            message = messages[message_index]
            thread_messages.append(message.to_dict())
            message_refs.append({"message_index": message_index})
        return {
            "thread_id": self.thread_id,
            "start_time": self.start_time.isoformat(),
            "last_time": self.last_time.isoformat(),
            "participants": sorted(self.participants),
            "message_ids": self.message_ids,
            "message_refs": message_refs,
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
