import unittest
from datetime import datetime, timedelta

import numpy as np

from preprocessing.models import Message
from threads_split.assigner import ThreadAssigner
from threads_split.models import Thread, ThreadConfig
from threads_split.scoring import (
    decide_assignment,
    new_thread_score,
    normalized_gap_from_previous,
    social_score,
    time_proximity,
)


def make_message(content: str, sender: str, dt: datetime) -> Message:
    return Message(date_time=dt, sender=sender, content=content)


class ScoringTests(unittest.TestCase):
    def setUp(self):
        self.config = ThreadConfig()

    def test_time_proximity_at_zero_gap(self):
        now = datetime(2024, 1, 1, 12, 0, 0)
        score = time_proximity(now, now, self.config)
        self.assertAlmostEqual(score, 1.0)

    def test_time_proximity_at_30_minutes(self):
        start = datetime(2024, 1, 1, 12, 0, 0)
        end = start + timedelta(minutes=30)
        score = time_proximity(end, start, self.config)
        expected = np.exp(-30 / self.config.tau_minutes)
        self.assertAlmostEqual(score, expected, places=5)

    def test_time_proximity_at_120_minutes(self):
        start = datetime(2024, 1, 1, 12, 0, 0)
        end = start + timedelta(minutes=120)
        score = time_proximity(end, start, self.config)
        expected = np.exp(-120 / self.config.tau_minutes)
        self.assertAlmostEqual(score, expected, places=5)

    def test_social_same_sender(self):
        thread = Thread.create(
            0,
            make_message("hello", "Alice", datetime(2024, 1, 1, 12, 0, 0)),
            np.ones(3),
            self.config,
        )
        message = make_message("again", "Alice", datetime(2024, 1, 1, 12, 1, 0))
        self.assertEqual(social_score(message, thread), 1.0)

    def test_social_known_participant(self):
        thread = Thread.create(
            0,
            make_message("hello", "Alice", datetime(2024, 1, 1, 12, 0, 0)),
            np.ones(3),
            self.config,
        )
        message = make_message("reply", "Bob", datetime(2024, 1, 1, 12, 1, 0))
        thread.add_message(1, message, np.ones(3), self.config)
        third = make_message("more", "Bob", datetime(2024, 1, 1, 12, 2, 0))
        self.assertEqual(social_score(third, thread), 1.0)

    def test_decide_assignment_attaches_when_clear_winner(self):
        thread_a = Thread.create(
            0,
            make_message("topic a", "Alice", datetime(2024, 1, 1, 12, 0, 0)),
            np.ones(3),
            self.config,
        )
        thread_b = Thread.create(
            1,
            make_message("topic b", "Bob", datetime(2024, 1, 1, 11, 0, 0)),
            np.ones(3),
            self.config,
        )
        from threads_split.models import ScoredCandidate

        candidates = [
            ScoredCandidate(thread=thread_a, attach_score=0.7, semantic_score=0.7, time_score=0.8, social_score=1.0),
            ScoredCandidate(thread=thread_b, attach_score=0.2, semantic_score=0.2, time_score=0.2, social_score=0.0),
        ]
        chosen = decide_assignment(candidates, new_thread_score_value=0.3, config=self.config)
        self.assertIs(chosen, thread_a)

    def test_decide_assignment_starts_new_when_below_threshold(self):
        thread = Thread.create(
            0,
            make_message("topic", "Alice", datetime(2024, 1, 1, 12, 0, 0)),
            np.ones(3),
            self.config,
        )
        from threads_split.models import ScoredCandidate

        candidates = [
            ScoredCandidate(thread=thread, attach_score=0.3, semantic_score=0.3, time_score=0.3, social_score=0.3),
        ]
        chosen = decide_assignment(candidates, new_thread_score_value=0.2, config=self.config)
        self.assertIsNone(chosen)

    def test_normalized_gap_from_previous(self):
        prev = datetime(2024, 1, 1, 12, 0, 0)
        current = prev + timedelta(minutes=180)
        score = normalized_gap_from_previous(current, prev, self.config)
        self.assertAlmostEqual(score, 0.5)


class ThreadModelTests(unittest.TestCase):
    def setUp(self):
        Thread.reset_counter()
        self.config = ThreadConfig()

    def test_add_message_updates_metadata(self):
        embedding = np.array([1.0, 0.0, 0.0])
        first = make_message("start", "Alice", datetime(2024, 1, 1, 12, 0, 0))
        thread = Thread.create(0, first, embedding, self.config)

        second = make_message("follow up", "Bob", datetime(2024, 1, 1, 12, 5, 0))
        thread.add_message(1, second, np.array([0.0, 1.0, 0.0]), self.config)

        self.assertEqual(thread.num_messages, 2)
        self.assertEqual(thread.num_unique_senders, 2)
        self.assertEqual(thread.message_ids, [0, 1])
        self.assertEqual(thread.last_sender, "Bob")


class AssignerLifecycleTests(unittest.TestCase):
    def setUp(self):
        Thread.reset_counter()
        self.config = ThreadConfig(max_open_threads=2, close_after_hours=24)

    def test_max_open_threads_cap(self):
        messages = [
            make_message("topic one", "Alice", datetime(2024, 1, 1, 10, 0, 0)),
            make_message("topic two", "Bob", datetime(2024, 1, 1, 11, 0, 0)),
            make_message("topic three", "Carol", datetime(2024, 1, 1, 12, 0, 0)),
        ]
        embeddings = [
            np.array([1.0, 0.0, 0.0]),
            np.array([0.0, 1.0, 0.0]),
            np.array([0.0, 0.0, 1.0]),
        ]
        assigner = ThreadAssigner(self.config)
        threads = assigner.process_messages(
            messages,
            embeddings,
            batch_start=0,
            batch_end=len(messages),
            reset=True,
            finalize=True,
        )
        self.assertEqual(len(threads), 3)

    def test_auto_close_after_24_hours(self):
        messages = [
            make_message("old topic", "Alice", datetime(2024, 1, 1, 10, 0, 0)),
            make_message("new topic", "Bob", datetime(2024, 1, 3, 10, 0, 0)),
        ]
        embeddings = [np.array([1.0, 0.0]), np.array([0.0, 1.0])]
        assigner = ThreadAssigner(self.config)
        threads = assigner.process_messages(
            messages,
            embeddings,
            batch_start=0,
            batch_end=len(messages),
            reset=True,
            finalize=True,
        )
        self.assertEqual(len(threads), 2)


if __name__ == "__main__":
    unittest.main()
