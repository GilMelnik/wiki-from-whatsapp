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
    lexical_blend_weight: float = 0.15
    b1_gap: float = 0.4
    b2_low_similarity: float = 0.6
    gap_normalize_minutes: float = 360.0
    embedding_model: str = "intfloat/multilingual-e5-large"
    topic_embedding_window: int = 5

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
            "lexical_blend_weight": self.lexical_blend_weight,
            "b1_gap": self.b1_gap,
            "b2_low_similarity": self.b2_low_similarity,
            "gap_normalize_minutes": self.gap_normalize_minutes,
            "embedding_model": self.embedding_model,
            "topic_embedding_window": self.topic_embedding_window,
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
        topic_embedding: np.ndarray,
        summary_embedding: np.ndarray,
        is_open: bool = True,
        recent_embeddings: list[np.ndarray] | None = None,
        process_indices: list[int] | None = None,
    ):
        self.thread_id = thread_id
        self.start_time = start_time
        self.last_time = last_time
        self.participants = participants
        self.message_ids = message_ids
        self.process_indices = process_indices or []
        self.last_sender = last_sender
        self.num_messages = num_messages
        self.num_unique_senders = num_unique_senders
        self.topic_embedding = topic_embedding
        self.summary_embedding = summary_embedding
        self.is_open = is_open
        self.recent_embeddings = recent_embeddings or []

    @classmethod
    def create(
        cls,
        stream_index: int,
        message: Message,
        embedding: np.ndarray,
        config: ThreadConfig,
    ) -> Thread:
        cls._counter += 1
        thread_id = f"thread-{cls._counter:04d}"
        participants = {message.sender}
        return cls(
            thread_id=thread_id,
            start_time=message.datetime,
            last_time=message.datetime,
            participants=participants,
            message_ids=[stream_index],
            last_sender=message.sender,
            num_messages=1,
            num_unique_senders=1,
            topic_embedding=embedding.copy(),
            summary_embedding=embedding.copy(),
            is_open=True,
            recent_embeddings=[embedding.copy()],
            process_indices=[stream_index],
        )

    @classmethod
    def reset_counter(cls) -> None:
        cls._counter = 0

    def add_message(
        self,
        stream_index: int,
        message: Message,
        embedding: np.ndarray,
        config: ThreadConfig,
    ) -> None:
        self.message_ids.append(stream_index)
        self.process_indices.append(stream_index)
        self.last_time = message.datetime
        self.last_sender = message.sender
        self.num_messages += 1
        if message.sender not in self.participants:
            self.participants.add(message.sender)
            self.num_unique_senders += 1

        self.recent_embeddings.append(embedding.copy())
        window = config.topic_embedding_window
        if len(self.recent_embeddings) > window:
            self.recent_embeddings = self.recent_embeddings[-window:]

        self.topic_embedding = np.mean(self.recent_embeddings, axis=0)
        self.summary_embedding = self.topic_embedding.copy()

    def to_dict(self, messages: Sequence[Message]) -> dict[str, Any]:
        thread_messages = []
        message_refs = []
        for stream_index in self.message_ids:
            message = messages[stream_index]
            msg_dict = message.to_dict()
            source_file = getattr(message, "source_file", None)
            source_index = getattr(message, "source_index", stream_index)
            if source_file is not None:
                msg_dict["source_file"] = str(source_file)
                msg_dict["source_index"] = source_index
            thread_messages.append(msg_dict)
            message_refs.append(
                {
                    "source_file": str(source_file) if source_file is not None else None,
                    "source_index": source_index,
                    "stream_index": stream_index,
                }
            )
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
