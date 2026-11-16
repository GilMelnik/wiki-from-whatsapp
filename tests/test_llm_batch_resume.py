"""A resumable batch: its id is kept before polling, a later run with the same requests picks it
up instead of paying again, and the id is dropped once the answers are cached. Fakes only."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from types import SimpleNamespace

import anthropic
import httpx
import pytest

from tests.test_anthropic_config import _FakeBatches, _message, _provider
from utils.llm_client import BatchGone, BatchRequest, BatchUnfinished, LLMClient


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


class EndsUnfinished(FakeBatchProvider):
    """The batch ends with ``b`` expired; only the requests in ``answer`` are answered."""

    def __init__(self) -> None:
        super().__init__()
        self.fail_polling = False
        self.submitted: list[list[str]] = []

    def generate_batch(self, requests: Sequence[BatchRequest], *, batch_id: str | None = None,
                       on_submit: Callable[[str], None] | None = None) -> dict:
        self.submitted.append([r.request_id for r in requests])
        if on_submit is not None:
            on_submit(f"batch_{len(self.submitted)}")
        self.unfinished = [r.request_id for r in requests if r.request_id == "b"
                           and len(self.submitted) == 1]
        return {r.request_id: ("", False) if r.request_id in self.unfinished
                else ('{"ok": true}', False) for r in requests}


def test_a_batch_with_expired_items_caches_the_rest_and_resubmits_only_those(
        tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    fake = EndsUnfinished()
    client._provider = fake
    with pytest.raises(BatchUnfinished) as raised:
        client.complete_batch_json(REQUESTS, resumable=True)
    assert raised.value.request_ids == ["b"]
    assert not list((tmp_path / "batches").glob("*.json"))
    assert client.complete_batch_json(REQUESTS, resumable=True) == {
        "a": {"ok": True}, "b": {"ok": True}}
    assert fake.submitted == [["a", "b"], ["b"]]


def test_without_resumable_an_expired_item_is_just_missing(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    client._provider = EndsUnfinished()
    assert client.complete_batch_json(REQUESTS) == {"a": {"ok": True}}


class GoneProvider(FakeBatchProvider):
    def generate_batch(self, requests: Sequence[BatchRequest], *, batch_id: str | None = None,
                       on_submit: Callable[[str], None] | None = None) -> dict:
        if batch_id is not None:
            self.calls.append(batch_id)
            raise BatchGone(batch_id, RuntimeError("not found"))
        return super().generate_batch(requests, batch_id=batch_id, on_submit=on_submit)


def test_a_saved_batch_that_is_gone_drops_its_id_and_the_next_run_submits_anew(
        tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    fake = GoneProvider()
    client._provider = fake
    with pytest.raises(RuntimeError, match="polling"):
        client.complete_batch_json(REQUESTS, resumable=True)
    with pytest.raises(BatchGone):
        client.complete_batch_json(REQUESTS, resumable=True)
    assert not list((tmp_path / "batches").glob("*.json"))
    fake.fail_polling = False
    assert client.complete_batch_json(REQUESTS, resumable=True)["a"] == {"ok": True}
    assert fake.calls == [None, "batch_1", None]


def _not_found() -> anthropic.NotFoundError:
    request = httpx.Request("GET", "https://api.example/v1/messages/batches/x")
    return anthropic.NotFoundError("not found", response=httpx.Response(404, request=request),
                                   body=None)


def test_the_anthropic_provider_marks_expired_items_and_a_gone_batch() -> None:
    provider = _provider("low")
    batches = _FakeBatches({"a": SimpleNamespace(type="succeeded", message=_message('{"v": 1}')),
                            "b": SimpleNamespace(type="expired"),
                            "c": SimpleNamespace(type="canceled"),
                            "d": SimpleNamespace(type="errored")})
    provider._client = SimpleNamespace(messages=SimpleNamespace(batches=batches))
    out = provider.generate_batch([BatchRequest(r, "s", "u") for r in "abcd"])
    assert provider.unfinished == ["b", "c"] and out["d"] == ("", False)

    def gone(batch_id: str) -> None:
        raise _not_found()

    batches.retrieve = gone
    with pytest.raises(BatchGone):
        provider.generate_batch([BatchRequest("a", "s", "u")], batch_id="batch_old")


def test_a_cut_off_answer_is_not_retried_when_the_window_cannot_grow_enough(
        tmp_path: Path) -> None:
    client, fake = _client(tmp_path)
    fake.fail_polling = False
    client.max_tokens = 16000  # the ceiling (16384) is not 1.5 times larger
    calls: list[int] = []

    def cut(requests: Sequence[BatchRequest], **kwargs: object) -> dict:
        calls.append(client.max_tokens)
        return {r.request_id: ('{"cut', True) for r in requests}

    fake.generate_batch = cut
    assert client.complete_batch_json(REQUESTS) == {}
    assert calls == [16000] and client.max_tokens == 16000
    client.max_tokens = 8192
    client.complete_batch_json([BatchRequest("z", "system", "other")])
    assert calls == [16000, 8192, 16384]
