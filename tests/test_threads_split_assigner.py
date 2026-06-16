from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

from preprocessing.models import Message
from threads_split.assigner import ThreadAssigner
from threads_split.models import ScoredCandidate, Thread, ThreadConfig
from threads_split.scoring import ThreadScorer, social_score
from threads_split.tf_idf.tfidf import TokenizedMessages, TfidfCorpus


def _message(
    index: int,
    *,
    minutes: int = 0,
    sender: str = "alice",
    content: str = "hello",
) -> Message:
    return Message(
        date_time=datetime(2024, 1, 1, 12, 0) + timedelta(minutes=minutes),
        sender=sender,
        content=content,
        message_id=f"m{index}",
    )


def _thread_with_messages(
    messages: list[Message],
    message_indices: list[int],
    embeddings: list[np.ndarray],
) -> Thread:
    thread = Thread.create(message_indices[0], messages[message_indices[0]], embeddings[0], 20)
    for idx in message_indices[1:]:
        thread.add_message(idx, messages[idx], embeddings[idx])
    return thread


def _scorer(
    messages: list[Message],
    embeddings: list[np.ndarray],
    config: ThreadConfig | None = None,
    query_embeddings: list[np.ndarray] | None = None,
) -> ThreadScorer:
    config = config or ThreadConfig()
    tokenized = TokenizedMessages([["hello"] for _ in messages])
    return ThreadScorer(
        config,
        messages,
        embeddings,
        query_embeddings=query_embeddings,
        tfidf_corpus=TfidfCorpus(document_count=1, terms={}, default_idf=1.0),
        tokenized_messages=tokenized,
    )


class AssignerLongThreadSplitTests(unittest.TestCase):
    def test_splits_internally_when_thread_exceeds_limit(self) -> None:
        messages = []
        embeddings = []
        for i in range(30):
            messages.append(_message(i, minutes=i, content=f"topic-a {i}"))
            embeddings.append(np.array([1.0, 0.0], dtype=float))
        for i in range(30, 55):
            messages.append(_message(i, minutes=1000 + i, content=f"topic-b {i}"))
            embeddings.append(np.array([0.0, 1.0], dtype=float))

        config = ThreadConfig(long_thread_message_limit=50, long_thread_min_part_size=10)
        assigner = ThreadAssigner(messages, embeddings, config=config)
        assigner.scorer = ThreadScorer(
            config,
            messages,
            embeddings,
            tfidf_corpus=TfidfCorpus(document_count=1, terms={}, default_idf=1.0),
            tokenized_messages=TokenizedMessages([["hello"] for _ in messages]),
        )

        thread = _thread_with_messages(messages, list(range(55)), embeddings)
        assigner.open_threads = [thread]
        for message_index in thread.message_ids:
            assigner.message_thread[message_index] = thread

        tail = assigner._split_thread_if_too_long(thread)

        self.assertEqual(len(thread.message_ids), 30)
        self.assertEqual(len(tail.message_ids), 25)
        self.assertEqual(tail.message_ids[0], 30)
        self.assertEqual(assigner.message_thread[54], tail)

    def test_keeps_long_thread_when_split_score_below_threshold(self) -> None:
        messages = [_message(i, minutes=i, content=f"ongoing {i}") for i in range(55)]
        embeddings = [np.array([1.0, 0.0], dtype=float) for _ in range(55)]

        config = ThreadConfig(long_thread_message_limit=50, long_thread_min_part_size=10)
        assigner = ThreadAssigner(messages, embeddings, config=config)
        assigner.scorer = ThreadScorer(
            config,
            messages,
            embeddings,
            tfidf_corpus=TfidfCorpus(document_count=1, terms={}, default_idf=1.0),
            tokenized_messages=TokenizedMessages([["hello"] for _ in messages]),
        )

        thread = _thread_with_messages(messages, list(range(55)), embeddings)
        assigner.open_threads = [thread]
        for message_index in thread.message_ids:
            assigner.message_thread[message_index] = thread

        result = assigner._split_thread_if_too_long(thread)

        self.assertIs(result, thread)
        self.assertEqual(len(thread.message_ids), 55)


class AssignerAccuracyTests(unittest.TestCase):
    def test_monologue_burst_keeps_same_thread(self) -> None:
        messages = [_message(i, minutes=i, content=f"part {i}") for i in range(5)]
        embeddings = [np.array([1.0, 0.0], dtype=np.float32) for _ in range(5)]
        config = ThreadConfig(monologue_gap_minutes=10.0, attach_threshold=0.99)
        assigner = ThreadAssigner(messages, embeddings, config=config)
        assigner.scorer = _scorer(messages, embeddings, config)

        threads = assigner.process_messages()

        self.assertEqual(len(threads), 1)
        self.assertEqual(threads[0].num_messages, 5)

    def test_reopens_closed_thread_for_delayed_answer(self) -> None:
        messages = [
            _message(0, minutes=0, content="topic A question"),
            _message(1, minutes=1, sender="bob", content="unrelated chatter"),
            _message(2, minutes=2, content="topic A answer"),
        ]
        embeddings = [
            np.array([1.0, 0.0], dtype=np.float32),
            np.array([0.0, 1.0], dtype=np.float32),
            np.array([1.0, 0.0], dtype=np.float32),
        ]
        config = ThreadConfig(
            attach_threshold=0.99,
            closed_thread_reopen_semantic_threshold=0.45,
            closed_thread_lookback_hours=168.0,
            close_after_hours=0.01,
        )
        assigner = ThreadAssigner(messages, embeddings, config=config)
        assigner.scorer = _scorer(messages, embeddings, config)

        threads = assigner.process_messages()

        self.assertEqual(len(threads), 2)
        topic_a = next(t for t in threads if 0 in t.message_ids)
        self.assertIn(2, topic_a.message_ids)

    def test_query_passage_asymmetry_improves_match(self) -> None:
        messages = [
            _message(0, minutes=0, content="stored passage"),
            _message(1, minutes=1, sender="bob", content="incoming query"),
        ]
        passage_embeddings = [
            np.array([1.0, 0.0], dtype=np.float32),
            np.array([0.0, 1.0], dtype=np.float32),
        ]
        query_embeddings = [
            np.array([0.0, 1.0], dtype=np.float32),
            np.array([1.0, 0.0], dtype=np.float32),
        ]
        config = ThreadConfig(w_tfidf=0.0)
        scorer = _scorer(messages, passage_embeddings, config, query_embeddings=query_embeddings)
        thread = _thread_with_messages(messages, [0], passage_embeddings)

        same_kind_score = scorer._position_decayed_similarity(
            1,
            thread,
            passage_embeddings[1],
            lambda index: passage_embeddings[index],
            thread.recent_embeddings_mean,
            lambda left, right: float(np.dot(left, right)),
        )
        asymmetric_score = scorer.embedding_semantic_similarity(1, thread)

        self.assertLess(same_kind_score, 0.5)
        self.assertGreater(asymmetric_score, 0.9)

    def test_writes_assignment_debug_log(self) -> None:
        messages = [_message(0, minutes=0), _message(1, minutes=1, sender="bob")]
        embeddings = [
            np.array([1.0, 0.0], dtype=np.float32),
            np.array([0.0, 1.0], dtype=np.float32),
        ]
        with tempfile.TemporaryDirectory() as tmp_dir:
            debug_path = Path(tmp_dir) / "debug.jsonl"
            config = ThreadConfig(assignment_debug_path=debug_path, attach_threshold=0.99)
            assigner = ThreadAssigner(messages, embeddings, config=config)
            assigner.scorer = _scorer(messages, embeddings, config)
            assigner.process_messages()

            lines = debug_path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 2)
            first = json.loads(lines[0])
            self.assertIn("decision", first)
            self.assertIn("thread_id", first)


class ScoringAccuracyTests(unittest.TestCase):
    def test_new_thread_score_uses_semantic_not_attach(self) -> None:
        messages = [_message(0), _message(1, minutes=5, sender="bob")]
        embeddings = [np.array([1.0, 0.0]), np.array([0.0, 1.0])]
        scorer = _scorer(messages, embeddings)
        thread = _thread_with_messages(messages, [0], embeddings)
        candidate = ScoredCandidate(
            thread=thread,
            attach_score=0.9,
            semantic_score=0.1,
            time_score=1.0,
            social_score=1.0,
        )

        score = scorer.new_thread_score(messages[1], messages[0].datetime, [candidate])

        self.assertGreater(score, 0.5)

    def test_social_score_bonus_for_reply_to_previous_in_thread(self) -> None:
        thread = Thread.create(0, _message(0), np.array([1.0, 0.0]), 20)
        message = _message(1, minutes=1, sender="bob")
        base = social_score(message, thread)
        with_reply = social_score(
            message,
            thread,
            previous_message_index=0,
            previous_message_in_thread=True,
        )
        self.assertGreater(with_reply, base)


if __name__ == "__main__":
    unittest.main()

