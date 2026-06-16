from __future__ import annotations

from datetime import timedelta
from typing import Sequence

import numpy as np

from preprocessing.models import Message
from threads_split.models import Thread, ThreadConfig
from threads_split.scoring import ThreadScorer


class ThreadAssigner:
    def __init__(
        self,
        messages: Sequence[Message],
        message_embeddings: Sequence[np.ndarray],
        config: ThreadConfig | None = None,
    ):
        self.config = config or ThreadConfig()
        self.open_threads: list[Thread] = []
        self.closed_threads: list[Thread] = []
        self.messages: Sequence[Message] = messages
        self.message_embeddings: Sequence[np.ndarray] = message_embeddings
        self.scorer: ThreadScorer = ThreadScorer(
            self.config, self.messages, self.message_embeddings
        )
        self.message_thread: dict[int, Thread] = {}
        self.quote_index: dict[tuple[str, str], list[int]] = {}

    def process_messages(self) -> list[Thread]:
        for message_index in range(len(self.messages)):
            message = self.messages[message_index]
            previous_message_time = (
                self.messages[message_index - 1].datetime if message_index > 0 else None
            )
            self._close_stale_threads(message.datetime)
            self._assign_message(message_index, message, previous_message_time)

        self._flush_open_threads()
        all_threads = self.closed_threads.copy()
        all_threads.sort(key=lambda t: t.start_time)
        return all_threads

    def _assign_message(
        self,
        message_index: int,
        message: Message,
        previous_message_time,
    ) -> None:
        message_embedding = self.message_embeddings[message_index]
        quoted_idx = self._resolve_quote_index(message_index, message)

        if quoted_idx is not None:
            thread = self.message_thread[quoted_idx]
            self._reopen_thread(thread)
            thread.add_message(message_index, message, message_embedding)
            thread.add_reaction_participants(message.reactions)
            self.message_thread[message_index] = thread
            self._update_quote_index(message_index, message)
            thread = self._split_thread_if_too_long(thread)
            self.message_thread[message_index] = thread
            return

        candidates = [
            self.scorer.attach_score(message, message_index, message_embedding, thread)
            for thread in self.open_threads
        ]
        new_score = self.scorer.new_thread_score(message, previous_message_time, candidates)
        chosen_thread = self.scorer.decide_assignment(candidates, new_score)

        if chosen_thread is None:
            thread = Thread.create(
                message_index,
                message,
                message_embedding,
                self.config.recent_embeddings_window,
            )
            self.open_threads.append(thread)
        else:
            thread = chosen_thread
            thread.add_message(message_index, message, message_embedding)

        thread.add_reaction_participants(message.reactions)
        self.message_thread[message_index] = thread
        self._update_quote_index(message_index, message)
        thread = self._split_thread_if_too_long(thread)
        self.message_thread[message_index] = thread
        self._enforce_open_thread_cap()

    def _split_thread_if_too_long(self, thread: Thread) -> Thread:
        current = thread
        while current.num_messages > self.config.long_thread_message_limit:
            split_pos = self.scorer.find_best_split_point(current)
            if split_pos is None:
                break

            tail = current.split_after(split_pos, self.messages, self.message_embeddings)
            for message_index in tail.message_ids:
                self.message_thread[message_index] = tail

            if current in self.open_threads:
                self.open_threads.append(tail)
            elif current in self.closed_threads:
                self.closed_threads.append(tail)

            current = tail
        return current

    def _resolve_quote_index(self, message_index: int, message: Message) -> int | None:
        key = message.quote_lookup_key()
        if key is None:
            return None

        candidates = [idx for idx in self.quote_index.get(key, []) if idx < message_index]
        if not candidates:
            return None
        return candidates[-1]

    def _update_quote_index(self, message_index: int, message: Message) -> None:
        key = (message.sender, message.normalized_content())
        self.quote_index.setdefault(key, []).append(message_index)

    def _reopen_thread(self, thread: Thread) -> None:
        if thread in self.open_threads:
            thread.is_open = True
            return

        if thread in self.closed_threads:
            self.closed_threads.remove(thread)

        thread.is_open = True
        if thread not in self.open_threads:
            self.open_threads.append(thread)

    def _close_stale_threads(self, current_time) -> None:
        cutoff = timedelta(hours=self.config.close_after_hours)
        still_open: list[Thread] = []
        for thread in self.open_threads:
            if current_time - thread.last_time >= cutoff:
                thread.is_open = False
                self.closed_threads.append(thread)
            else:
                still_open.append(thread)
        self.open_threads = still_open

    def _enforce_open_thread_cap(self) -> None:
        while len(self.open_threads) > self.config.max_open_threads:
            lru_thread = min(self.open_threads, key=lambda t: t.last_time)
            self.open_threads.remove(lru_thread)
            lru_thread.is_open = False
            self.closed_threads.append(lru_thread)

    def _flush_open_threads(self) -> None:
        for thread in self.open_threads:
            thread.is_open = False
        self.closed_threads.extend(self.open_threads)
        self.open_threads = []
