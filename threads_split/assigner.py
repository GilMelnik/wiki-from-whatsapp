from __future__ import annotations

from datetime import timedelta
from typing import Sequence

import numpy as np

from preprocessing.models import Message
from threads_split.models import Thread, ThreadConfig
from threads_split.scoring import attach_score, decide_assignment, new_thread_score


class ThreadAssigner:
    def __init__(self, config: ThreadConfig | None = None):
        self.config = config or ThreadConfig()
        self.open_threads: list[Thread] = []
        self.closed_threads: list[Thread] = []

    def process_messages(
        self,
        messages: Sequence[Message],
        message_embeddings: Sequence[np.ndarray],
    ) -> list[Thread]:
        Thread.reset_counter()
        self.open_threads = []
        self.closed_threads = []

        previous_message_time = None
        for process_index, message in enumerate(messages):
            source_index = getattr(message, "source_index", process_index)
            self._close_stale_threads(message.datetime)
            self._assign_message(
                source_index,
                process_index,
                message,
                messages,
                message_embeddings,
                previous_message_time,
            )
            previous_message_time = message.datetime

        self._flush_open_threads()
        all_threads = self.closed_threads + self.open_threads
        all_threads.sort(key=lambda t: t.start_time)
        return all_threads

    def _assign_message(
        self,
        source_index: int,
        process_index: int,
        message: Message,
        messages: Sequence[Message],
        message_embeddings: Sequence[np.ndarray],
        previous_message_time,
    ) -> None:
        candidates = [
            attach_score(
                message,
                process_index,
                message_embeddings[process_index],
                thread,
                messages,
                message_embeddings,
                self.config,
            )
            for thread in self.open_threads
        ]
        new_score = new_thread_score(
            message,
            process_index,
            message_embeddings[process_index],
            self.open_threads,
            messages,
            message_embeddings,
            previous_message_time,
            self.config,
        )
        chosen_thread = decide_assignment(candidates, new_score, self.config)

        if chosen_thread is None:
            thread = Thread.create(
                source_index,
                message,
                message_embeddings[process_index],
                self.config,
                process_index=process_index,
            )
            self.open_threads.append(thread)
        else:
            chosen_thread.add_message(
                source_index,
                message,
                message_embeddings[process_index],
                self.config,
                process_index=process_index,
            )

        self._enforce_open_thread_cap()

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
