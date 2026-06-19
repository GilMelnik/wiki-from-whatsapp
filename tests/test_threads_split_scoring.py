from __future__ import annotations

import unittest
from datetime import datetime, timedelta

import numpy as np

from step_0_preprocessing.models import Message
from step_1_threads_split.models import Thread, ThreadConfig
from step_1_threads_split.scoring import ThreadScorer
from step_1_threads_split.tf_idf.tfidf import TokenizedMessages, TfidfCorpus


def _message(
    index: int,
    *,
    minutes: int = 0,
    sender: str = "alice",
    content: str = "hello",
    quote: dict[str, str] | None = None,
) -> Message:
    return Message(
        date_time=datetime(2024, 1, 1, 12, 0) + timedelta(minutes=minutes),
        sender=sender,
        content=content,
        message_id=f"m{index}",
        quote=quote,
    )


def _thread_with_messages(
    messages: list[Message],
    message_indices: list[int],
    embeddings: list[np.ndarray],
    *,
    window: int = 20,
) -> Thread:
    thread = Thread.create(message_indices[0], messages[message_indices[0]], embeddings[0], window)
    for idx in message_indices[1:]:
        thread.add_message(idx, messages[idx], embeddings[idx])
    return thread


def _scorer(messages: list[Message], embeddings: list[np.ndarray]) -> ThreadScorer:
    tokenized = TokenizedMessages([["hello"] for _ in messages])
    return ThreadScorer(
        ThreadConfig(),
        messages,
        embeddings,
        tfidf_corpus=TfidfCorpus(document_count=1, terms={}, default_idf=1.0),
        tokenized_messages=tokenized,
    )


class SplitBoundaryTests(unittest.TestCase):
    def test_prefers_large_time_gap(self) -> None:
        messages = []
        embeddings = []
        for i in range(30):
            messages.append(_message(i, minutes=i, content=f"topic-a {i}"))
            embeddings.append(np.array([1.0, 0.0], dtype=float))
        for i in range(30, 55):
            messages.append(_message(i, minutes=1000 + i, content=f"topic-b {i}"))
            embeddings.append(np.array([0.0, 1.0], dtype=float))

        thread = _thread_with_messages(messages, list(range(55)), embeddings)
        scorer = _scorer(messages, embeddings)

        split_pos = scorer.find_best_split_point(thread)
        self.assertEqual(split_pos, 29)

    def test_avoids_split_inside_quote_chain(self) -> None:
        messages = [_message(i, minutes=i, content=f"filler {i}") for i in range(48)]
        embeddings = [np.array([1.0, 0.0], dtype=float) for _ in range(48)]
        messages.extend(
            [
                _message(
                    48,
                    minutes=49,
                    sender="bob",
                    content="reply one",
                    quote={"sender": "alice", "text": "filler 47"},
                ),
                _message(
                    49,
                    minutes=50,
                    sender="alice",
                    content="reply two",
                    quote={"sender": "bob", "text": "reply one"},
                ),
                _message(50, minutes=1000, content="new topic"),
            ]
        )
        for i in range(51, 61):
            messages.append(_message(i, minutes=1000 + i, content=f"new topic {i}"))
        embeddings.extend(
            [
                np.array([1.0, 0.0], dtype=float),
                np.array([1.0, 0.0], dtype=float),
                np.array([0.0, 1.0], dtype=float),
            ]
        )
        embeddings.extend([np.array([0.0, 1.0], dtype=float) for _ in range(51, 61)])

        thread = _thread_with_messages(messages, list(range(61)), embeddings)
        scorer = _scorer(messages, embeddings)

        split_pos = scorer.find_best_split_point(thread)
        self.assertNotIn(split_pos, {46, 47, 48})
        self.assertEqual(split_pos, 49)
        self.assertTrue(scorer.boundary_splits_quote_component(thread, 48))
        self.assertEqual(scorer.score_split_boundary(thread, 48), float("-inf"))

    def test_never_splits_transitive_quote_chain(self) -> None:
        messages = [
            _message(0, minutes=0, content="root"),
            _message(
                1,
                minutes=1,
                content="reply",
                quote={"sender": "alice", "text": "root"},
            ),
        ]
        messages.extend(_message(i, minutes=100 + i, content=f"filler {i}") for i in range(2, 54))
        messages.append(
            _message(
                54,
                minutes=2000,
                content="late reply",
                quote={"sender": "alice", "text": "reply"},
            )
        )
        embeddings = [np.array([1.0, 0.0], dtype=float) for _ in range(55)]
        embeddings[54] = np.array([0.0, 1.0], dtype=float)

        thread = _thread_with_messages(messages, list(range(55)), embeddings)
        scorer = _scorer(messages, embeddings)

        self.assertIsNone(scorer.find_best_split_point(thread))

    def test_keeps_long_thread_when_no_boundary_passes_threshold(self) -> None:
        messages = [_message(i, minutes=i, content=f"ongoing {i}") for i in range(55)]
        embeddings = [np.array([1.0, 0.0], dtype=float) for _ in range(55)]
        thread = _thread_with_messages(messages, list(range(55)), embeddings)
        scorer = _scorer(messages, embeddings)

        self.assertIsNone(scorer.find_best_split_point(thread))

    def test_thread_split_keeps_both_parts(self) -> None:
        messages = [_message(i, minutes=i, content=f"msg {i}") for i in range(55)]
        embeddings = [np.array([1.0, 0.0], dtype=float) for _ in range(55)]
        thread = _thread_with_messages(messages, list(range(55)), embeddings)

        tail = thread.split_after(29, messages, embeddings)

        self.assertEqual(len(thread.message_ids), 30)
        self.assertEqual(len(tail.message_ids), 25)
        self.assertEqual(thread.message_ids[-1], 29)
        self.assertEqual(tail.message_ids[0], 30)


if __name__ == "__main__":
    unittest.main()
