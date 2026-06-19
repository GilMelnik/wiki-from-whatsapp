from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from step_0_preprocessing.models import Message
from step_1_threads_split.run import load_messages, sort_messages_quote_aware


def _message(
    *,
    minutes: int = 0,
    sender: str = "alice",
    content: str = "hello",
    message_id: str | None = None,
    quote: dict[str, str] | None = None,
) -> Message:
    return Message(
        date_time=datetime(2024, 1, 1, 12, minutes),
        sender=sender,
        content=content,
        message_id=message_id,
        quote=quote,
    )


class QuoteAwareSortTests(unittest.TestCase):
    def test_quoting_message_follows_quoted_when_same_timestamp(self) -> None:
        quoted = _message(sender="alice", content="question", message_id="quoted")
        quoting = _message(
            sender="bob",
            content="answer",
            message_id="quoting",
            quote={"sender": "alice", "text": "question"},
        )

        ordered = sort_messages_quote_aware([quoting, quoted])

        ids = [m.id for m in ordered]
        self.assertLess(ids.index("quoted"), ids.index("quoting"))

    def test_preserves_datetime_order_without_quotes(self) -> None:
        first = _message(minutes=0, content="first", message_id="a")
        second = _message(minutes=1, content="second", message_id="b")

        ordered = sort_messages_quote_aware([second, first])

        self.assertEqual([m.id for m in ordered], ["a", "b"])

    def test_transitive_quote_chain_same_timestamp(self) -> None:
        root = _message(sender="alice", content="root", message_id="root")
        middle = _message(
            sender="bob",
            content="middle",
            message_id="middle",
            quote={"sender": "alice", "text": "root"},
        )
        leaf = _message(
            sender="alice",
            content="leaf",
            message_id="leaf",
            quote={"sender": "bob", "text": "middle"},
        )

        ordered = sort_messages_quote_aware([leaf, middle, root])

        ids = [m.id for m in ordered]
        self.assertLess(ids.index("root"), ids.index("middle"))
        self.assertLess(ids.index("middle"), ids.index("leaf"))


class LoadMessagesContentFilterTests(unittest.TestCase):
    def test_drops_media_only_and_empty_messages(self) -> None:
        raw = [
            {"date": "01/01/2024", "time": "12:00", "sender": "alice", "text": "hello world", "id": "m1"},
            {"date": "01/01/2024", "time": "12:01", "sender": "alice", "text": "<image omitted>", "id": "m2"},
            {"date": "01/01/2024", "time": "12:02", "sender": "bob", "text": "", "id": "m3"},
        ]
        with tempfile.TemporaryDirectory() as tmp_dir:
            input_path = Path(tmp_dir) / "chat.json"
            input_path.write_text(json.dumps(raw), encoding="utf-8")

            messages = load_messages(input_path)

        self.assertEqual([m.id for m in messages], ["m1"])


if __name__ == "__main__":
    unittest.main()
