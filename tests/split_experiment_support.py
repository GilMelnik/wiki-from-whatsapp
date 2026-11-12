"""A synthetic chat, gold file and two splits for the split experiment's tests.

Twenty messages ``m01`` .. ``m20``, one per minute, three members. Two gold windows:

- window 1: messages ``m04`` .. ``m07`` with context ``m08``, ``m09``; the owner's
  conversations ``m04 m05`` | ``m06 m07 m08`` | ``m09``;
- window 2: messages ``m14`` .. ``m16``, one conversation, no context.

The new split's thread ``t-a`` starts before the set (``m01 m02 m04 m05``), ``t-b`` is cut at
the set's edge (``m03`` before, ``m10`` after); the old split has two big threads.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from step_3_extract.split_experiment.units import MessageSet

SENDERS = ("p1@s.whatsapp.net", "p2@s.whatsapp.net", "p3@s.whatsapp.net")
IDS = [f"m{n:02d}" for n in range(1, 21)]


def chat() -> list[dict[str, Any]]:
    """C1 records in chat order; ``m07`` replies to ``m06``, ``m05`` has a reaction."""
    records = []
    for n, wa_id in enumerate(IDS):
        record: dict[str, Any] = {"id": wa_id, "datetime": f"2024-01-01T10:{n:02d}:00",
                                  "sender": SENDERS[n % 3], "content": f"synthetic text {wa_id}"}
        records.append(record)
    records[6]["quote"] = {"sender": SENDERS[5 % 3], "text": "synthetic text m06"}
    records[4]["reactions"] = [{"emoji": "👍", "senders": [SENDERS[0], SENDERS[2]]}]
    return records


def gold() -> dict[str, Any]:
    labels = {"m04": "m04", "m05": "m04", "m06": "m06", "m07": "m06",
              "m14": "m14", "m15": "m14", "m16": "m14"}
    return {
        "windows": [
            {"start": "2024-01-01T10:03", "end": "2024-01-01T10:07",
             "message_ids": ["m04", "m05", "m06", "m07"], "context_message_ids": ["m08", "m09"]},
            {"start": "2024-01-01T10:13", "end": "2024-01-01T10:16",
             "message_ids": ["m14", "m15", "m16"], "context_message_ids": []},
        ],
        "messages": labels,
        "context_messages": {"m08": "m06", "m09": "m09"},
        "metadata": {},
    }


def threads(groups: dict[str, list[str]]) -> list[dict[str, Any]]:
    by_id = {m["id"]: m for m in chat()}
    return [{"thread_id": tid, "messages": [by_id[i] for i in ids]} for tid, ids in groups.items()]


NEW_SPLIT = {"t-a": ["m01", "m02", "m04", "m05"], "t-b": ["m03", "m06", "m07", "m08", "m09", "m10"],
             "t-c": ["m11", "m12", "m13"], "t-d": ["m14", "m15"],
             "t-e": ["m16", "m17", "m18", "m19", "m20"]}
OLD_SPLIT = {"old-1": IDS[:10], "old-2": IDS[10:]}


def message_set() -> MessageSet:
    return MessageSet(gold(), chat())


def write_inputs(root: Path) -> dict[str, Path]:
    """The four input files under ``root``; returns the CLI paths."""
    root.mkdir(parents=True, exist_ok=True)
    export = [{"id": m["id"], "date": "01/01/2024", "time": m["datetime"][11:16],
               "sender": {"user_name": m["sender"], "phone": ""}, "text": m["content"],
               "quote": None, "reactions": []} for m in chat()]
    files = {"gold": gold(), "chat": export, "new-split": {"threads": threads(NEW_SPLIT)},
             "old-split": {"threads": threads(OLD_SPLIT)}}
    paths = {}
    for name, payload in files.items():
        paths[name] = root / f"{name}.json"
        paths[name].write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return paths


def claim(text: str, supporting: list[int], stance: str = "factual",
          counters: list[int] | None = None) -> dict[str, Any]:
    """One claim of a model answer."""
    return {"text": text, "entities": [], "stance": stance, "supporting": supporting,
            "opposing": [], "counters": counters or []}


USAGE = {"input_tokens": 100, "output_tokens": 20, "cache_creation_input_tokens": 10,
         "cache_read_input_tokens": 50}


class FakeBatches:
    """``messages.batches`` of a fake Anthropic client: every item answers one claim on
    message [1] and reports ``USAGE``; counts the batches submitted."""

    def __init__(self) -> None:
        self.created = 0
        self.submitted: list[str] = []

    def create(self, requests: list[dict[str, Any]]) -> SimpleNamespace:
        self.created += 1
        self.submitted = [item["custom_id"] for item in requests]
        return SimpleNamespace(id=f"batch-{self.created}")

    def retrieve(self, batch_id: str) -> SimpleNamespace:
        return SimpleNamespace(id=batch_id, processing_status="ended")

    def results(self, batch_id: str) -> list[SimpleNamespace]:
        del batch_id
        text = json.dumps({"claims": [claim("A synthetic claim", [1])]})
        message = SimpleNamespace(content=[SimpleNamespace(type="text", text=text)],
                                  stop_reason="end_turn", stop_details=None,
                                  usage=SimpleNamespace(**USAGE))
        return [SimpleNamespace(custom_id=custom_id,
                                result=SimpleNamespace(type="succeeded", message=message))
                for custom_id in self.submitted]
