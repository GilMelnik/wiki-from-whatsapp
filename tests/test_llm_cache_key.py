"""Response-cache key and cache/failure-log locations.

The key must cover everything that changes an answer (provider, model,
``thinking_param``, prompts, JSON mode, response schema) so a config or schema
change never returns a stale entry, and nothing that does not (temperature is
never sent by Anthropic or Gemini). Default paths must follow the data dir.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from utils.config import DATA_DIR
from utils.llm_client import BatchRequest, CacheSegment, LLMClient

SCHEMA = {
    "type": "object",
    "properties": {"a": {"type": "string"}, "b": {"type": "integer"}},
}


def _client(tmp_path: Path, **overrides: Any) -> LLMClient:
    params: dict[str, Any] = {
        "provider": "anthropic",
        "model": "claude-sonnet-5-5",
        "cache_dir": tmp_path,
        "failure_log": tmp_path / "failures.jsonl",
        "thinking_param": "low",
    }
    return LLMClient(**{**params, **overrides})


def test_same_inputs_give_the_same_key(tmp_path: Path) -> None:
    first = _client(tmp_path)._cache_key("sys", "usr", response_schema=SCHEMA)
    second = _client(tmp_path)._cache_key("sys", "usr", response_schema=SCHEMA)

    assert first == second


def test_cache_segments_key_like_their_flat_text(tmp_path: Path) -> None:
    llm = _client(tmp_path)
    segments = [CacheSegment("sy", cache=True), CacheSegment("s")]

    assert llm._cache_key(segments, "usr") == llm._cache_key("sys", "usr")


def test_schema_key_order_does_not_change_the_key(tmp_path: Path) -> None:
    llm = _client(tmp_path)
    reordered = {
        "properties": {"b": {"type": "integer"}, "a": {"type": "string"}},
        "type": "object",
    }

    assert llm._cache_key("sys", "usr", response_schema=SCHEMA) == llm._cache_key(
        "sys", "usr", response_schema=reordered
    )


def test_schema_and_json_mode_change_the_key(tmp_path: Path) -> None:
    llm = _client(tmp_path)
    other_schema = {"type": "object", "properties": {"a": {"type": "number"}}}

    keys = {
        llm._cache_key("sys", "usr"),
        llm._cache_key("sys", "usr", json_mode=True),
        llm._cache_key("sys", "usr", json_mode=True, response_schema=SCHEMA),
        llm._cache_key("sys", "usr", json_mode=True, response_schema=other_schema),
    }

    assert len(keys) == 4


def test_thinking_model_and_provider_change_the_key(tmp_path: Path) -> None:
    keys = {
        _client(tmp_path)._cache_key("sys", "usr"),
        _client(tmp_path, thinking_param="high")._cache_key("sys", "usr"),
        _client(tmp_path, thinking_param=None)._cache_key("sys", "usr"),
        _client(tmp_path, model="claude-opus-5-5")._cache_key("sys", "usr"),
        _client(tmp_path, provider="gemini", model="gemini-3.5-flash")._cache_key(
            "sys", "usr"
        ),
    }

    assert len(keys) == 5


@pytest.mark.parametrize(
    ("provider", "model"),
    [("anthropic", "claude-sonnet-5-5"), ("gemini", "gemini-3.5-flash")],
)
def test_temperature_does_not_change_the_key(
    tmp_path: Path, provider: str, model: str
) -> None:
    cold = _client(tmp_path, provider=provider, model=model, temperature=0.0)
    warm = _client(tmp_path, provider=provider, model=model, temperature=0.9)

    assert cold._cache_key("sys", "usr") == warm._cache_key("sys", "usr")


def test_batch_item_shares_its_key_with_complete_json(tmp_path: Path) -> None:
    llm = _client(tmp_path)
    req = BatchRequest(
        request_id="r1",
        system=[CacheSegment("sys", cache=True)],
        user="usr",
        response_schema=SCHEMA,
    )

    assert llm._batch_key(req) == llm._cache_key(
        "sys", "usr", json_mode=True, response_schema=SCHEMA
    )


def test_new_schema_is_requested_again_instead_of_served_stale(tmp_path: Path) -> None:
    llm = _client(tmp_path)
    calls: list[dict[str, Any] | None] = []

    def _dispatch(
        system: str,
        user: str,
        json_mode: bool = False,
        response_schema: dict[str, Any] | None = None,
    ) -> tuple[str, bool]:
        calls.append(response_schema)
        return f'{{"n": {len(calls)}}}', False

    llm._dispatch = _dispatch  # type: ignore[method-assign]

    assert llm.complete_json("sys", "usr", response_schema=SCHEMA) == {"n": 1}
    assert llm.complete_json("sys", "usr", response_schema=SCHEMA) == {"n": 1}  # cached
    assert llm.complete_json("sys", "usr") == {"n": 2}  # no schema: a different answer
    assert llm.complete_text("sys", "usr") == '{"n": 3}'  # text mode: its own entry
    assert calls == [SCHEMA, None, None]


def test_default_cache_and_failure_log_follow_the_data_dir() -> None:
    llm = LLMClient(provider="anthropic", model="claude-sonnet-5-5")

    assert llm.cache.cache_dir == DATA_DIR / "shared" / "llm_cache"
    assert llm.cache.bad_cache_dir == DATA_DIR / "shared" / "llm_cache_bad"
    assert llm.failure_log == DATA_DIR / "shared" / "llm_failures.jsonl"
