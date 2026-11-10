"""The old thread tagger loads and tags the C2 classified file that step 2 now writes."""

from __future__ import annotations

import importlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from step_1_threads_split.review.operations import recompute_thread_stats
from step_1_threads_split.review.stats import compute_stats
from step_1_threads_split.review.store import ThreadStore
from utils import config, paths

C2_RECORDS = [
    {"thread_id": "t-1", "passed_heuristic": True, "p_knowledge": 0.9, "is_knowledge_bearing": True},
    {"thread_id": "t-2", "passed_heuristic": False, "p_knowledge": None,
     "is_knowledge_bearing": False},
]


@pytest.fixture
def isolated_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("WIKI_DATA_DIR", str(tmp_path))
    importlib.reload(config)
    importlib.reload(paths)
    yield
    monkeypatch.undo()
    importlib.reload(config)
    importlib.reload(paths)


def _thread(thread_id: str, day: int, count: int) -> dict[str, Any]:
    messages = [
        {"id": f"{thread_id}-{i}", "datetime": f"2022-01-0{day}T1{i}:00:00",
         "sender": f"p{i % 2}", "content": "x"}
        for i in range(count)
    ]
    return recompute_thread_stats({"thread_id": thread_id, "messages": messages})


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_store_loads_and_tags_a_c2_file(isolated_data: None) -> None:
    _write(paths.ORIGINAL_THREADS_PATH, {"threads": [_thread("t-1", 1, 3), _thread("t-2", 2, 2)]})
    _write(
        paths.ORIGINAL_CLASSIFIED_PATH,
        {"threads": C2_RECORDS, "metadata": {"inputs": {"t-1": "a", "t-2": "b"}}},
    )

    store = ThreadStore()
    store.load()
    items, total = store.list_enriched()
    assert total == 2
    assert [item["is_knowledge_bearing"] for item in items] == [True, False]
    assert compute_stats(items, filter_kind="all")["total"] == 2

    store.update_classification("t-2", is_knowledge_bearing=True)
    saved = json.loads(paths.EDITED_CLASSIFIED_PATH.read_text(encoding="utf-8"))
    by_id = {record["thread_id"]: record for record in saved["threads"]}
    assert by_id["t-2"]["is_knowledge_bearing"] is True
    assert by_id["t-1"] == C2_RECORDS[0]
    assert saved["metadata"]["inputs"] == {"t-1": "a", "t-2": "b"}

    reloaded = ThreadStore()
    reloaded.load()
    assert reloaded.get_classification("t-2")["is_knowledge_bearing"] is True
