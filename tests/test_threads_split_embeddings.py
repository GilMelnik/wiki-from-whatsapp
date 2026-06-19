from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

from step_0_preprocessing.models import Message
from step_1_threads_split.embedding.embedding import load_message_embeddings
from step_1_threads_split.embedding.prepare_embeddings import ensure_embeddings
from utils.json_io import write_json_file


class MockEmbedder:
    model_name = "mock-e5"
    embedding_dim = 2

    def encode_messages(self, texts: Sequence[str]) -> list[np.ndarray]:
        return [np.array([1.0, 0.0], dtype=np.float32) for _ in texts]

    def encode_queries(self, texts: Sequence[str]) -> list[np.ndarray]:
        return [np.array([0.0, 1.0], dtype=np.float32) for _ in texts]


def _write_chat(path: Path, count: int = 3) -> None:
    messages = []
    for index in range(count):
        messages.append(
            {
                "date": "01/01/2024",
                "time": f"12:{index:02d}",
                "sender": {"user_name": "alice"},
                "text": f"message {index}",
            }
        )
    write_json_file(messages, path)


class EmbeddingStoreTests(unittest.TestCase):
    def test_ensure_embeddings_creates_both_passage_and_query_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            chat_path = tmp / "chat.json"
            passage_path = tmp / "passage.json"
            query_path = tmp / "query.json"
            _write_chat(chat_path)

            passage, query = ensure_embeddings(
                chat_path,
                passage_path=passage_path,
                query_path=query_path,
                model_name="mock-e5",
                embedder=MockEmbedder(),
            )

            self.assertEqual(len(passage), 3)
            self.assertEqual(len(query), 3)
            self.assertTrue(passage_path.exists())
            self.assertTrue(query_path.exists())
            self.assertEqual(passage[0].tolist(), [1.0, 0.0])
            self.assertEqual(query[0].tolist(), [0.0, 1.0])

    def test_ensure_embeddings_builds_missing_query_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            chat_path = tmp / "chat.json"
            passage_path = tmp / "passage.json"
            query_path = tmp / "query.json"
            _write_chat(chat_path, count=2)

            passage_vectors = [
                np.array([1.0, 0.0], dtype=np.float32),
                np.array([1.0, 0.0], dtype=np.float32),
            ]
            write_json_file(
                {
                    "metadata": {
                        "source": str(chat_path.resolve()),
                        "message_count": 2,
                        "embedding_model": "mock-e5",
                        "embedding_dim": 2,
                        "embedding_kind": "passage",
                    },
                    "embeddings": [vector.tolist() for vector in passage_vectors],
                },
                passage_path,
            )

            passage, query = ensure_embeddings(
                chat_path,
                passage_path=passage_path,
                query_path=query_path,
                model_name="mock-e5",
                embedder=MockEmbedder(),
            )

            self.assertEqual(len(passage), 2)
            self.assertEqual(len(query), 2)
            self.assertTrue(query_path.exists())

    def test_load_message_embeddings_returns_both_types(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            chat_path = tmp / "chat.json"
            passage_path = tmp / "passage.json"
            query_path = tmp / "query.json"
            _write_chat(chat_path, count=1)

            first_passage, first_query = load_message_embeddings(
                chat_path,
                passage_path=passage_path,
                query_path=query_path,
                model_name="mock-e5",
                embedder=MockEmbedder(),
            )
            second_passage, second_query = load_message_embeddings(
                chat_path,
                passage_path=passage_path,
                query_path=query_path,
                model_name="mock-e5",
                embedder=MockEmbedder(),
            )

            self.assertEqual(first_passage[0].tolist(), second_passage[0].tolist())
            self.assertEqual(first_query[0].tolist(), second_query[0].tolist())


if __name__ == "__main__":
    unittest.main()
