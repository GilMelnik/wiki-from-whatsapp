"""A resumable batch: its id is kept before polling, a later run with the same requests picks it
up instead of paying again, and the id is dropped once the answers are cached. Fakes only."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_anthropic_config import _FakeBatches, _message, _provider
from utils.llm_client import BatchRequest, LLMClient


class FakeBatchProvider:
    """Submits ``batch_1``; ``fail_polling`` makes the first poll fail after the submit."""

    supports_batch = True

    def __init__(self) -> None:
        self.max_tokens = 0
        self.usage: list[dict] = []
        self.fail_polling = True
        self.calls: list[str | None] = []

    def generate_batch(self, requests: Sequence[BatchRequest], *, batch_id: str | None = None,
                       on_submit: Callable[[str], None] | None = None) -> dict:
        self.calls.append(batch_id)
        if batch_id is None and on_submit is not None:
            on_submit("batch_1")
        if self.fail_polling:
            raise RuntimeError("the connection dropped while polling")
        return {req.request_id: ('{"ok": true}', False) for req in requests}


def _client(tmp_path: Path) -> tuple[LLMClient, FakeBatchProvider]:
    client = LLMClient(provider="anthropic", model="claude-opus-5-5", cache_dir=tmp_path / "cache",
                       failure_log=tmp_path / "failures.jsonl")
    fake = FakeBatchProvider()
    client._provider = fake
    client.batch_state_dir = tmp_path / "batches"
    return client, fake


REQUESTS = [BatchRequest("a", "system", "one"), BatchRequest("b", "system", "two")]


def test_an_interrupted_resumable_batch_is_picked_up_not_paid_again(tmp_path: Path) -> None:
    client, fake = _client(tmp_path)
    with pytest.raises(RuntimeError, match="polling"):
        client.complete_batch_json(REQUESTS, resumable=True)
    kept = list((tmp_path / "batches").glob("*.json"))
    assert len(kept) == 1 and '"batch_1"' in kept[0].read_text(encoding="utf-8")
    fake.fail_polling = False
    answers = client.complete_batch_json(REQUESTS, resumable=True)
    assert answers == {"a": {"ok": True}, "b": {"ok": True}}
    assert fake.calls == [None, "batch_1"]  # the second run submitted nothing
    assert not list((tmp_path / "batches").glob("*.json"))
    assert client.complete_batch_json(REQUESTS, resumable=True) == answers  # from the cache
    assert fake.calls == [None, "batch_1"]


def test_without_resumable_nothing_is_kept(tmp_path: Path) -> None:
    client, fake = _client(tmp_path)
    with pytest.raises(RuntimeError):
        client.complete_batch_json(REQUESTS)
    assert not (tmp_path / "batches").exists()
    fake.fail_polling = False
    client.complete_batch_json(REQUESTS)
    assert fake.calls == [None, None]


def test_other_requests_never_pick_up_a_kept_batch(tmp_path: Path) -> None:
    client, fake = _client(tmp_path)
    with pytest.raises(RuntimeError):
        client.complete_batch_json(REQUESTS, resumable=True)
    fake.fail_polling = False
    client.complete_batch_json([BatchRequest("c", "system", "three")], resumable=True)
    assert fake.calls == [None, None]


def test_the_anthropic_provider_retrieves_a_given_batch_instead_of_submitting() -> None:
    provider = _provider("low")
    batches = _FakeBatches({"a": SimpleNamespace(type="succeeded", message=_message('{"v": 1}'))})
    provider._client = SimpleNamespace(messages=SimpleNamespace(batches=batches))
    out = provider.generate_batch([BatchRequest("a", "s", "u")], batch_id="batch_7")
    assert out == {"a": ('{"v": 1}', False)} and batches.submitted == []
    submitted: list[str] = []
    provider.generate_batch([BatchRequest("a", "s", "u")], on_submit=submitted.append)
    assert submitted == ["batch_1"] and len(batches.submitted) == 1
