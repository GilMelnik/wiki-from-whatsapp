"""Anthropic provider request wiring.

Guards that the shared ``thinking_param`` routes to ``output_config.effort`` and
composes with the JSON-schema structured-output ``format`` in one config object,
that batch items carry the same prompt-cache breakpoints as sync calls, that a
safety refusal never passes as an answer, and that every configured stage builds.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("anthropic")

from utils.llm_client import BatchRequest, CacheSegment, LLMClient
from utils.llm_client.providers.anthropic import AnthropicProvider
from utils.llm_client.settings import CONFIG

EPHEMERAL = {"type": "ephemeral"}


def _provider(thinking_param: str | None) -> AnthropicProvider:
    return AnthropicProvider(
        model="claude-sonnet-5-5",
        temperature=0.2,
        max_tokens=1024,
        batch_poll_interval=0.0,
        thinking_param=thinking_param,
    )


def _message(text: str, stop_reason: str = "end_turn") -> SimpleNamespace:
    return SimpleNamespace(
        content=[SimpleNamespace(type="thinking"), SimpleNamespace(type="text", text=text)],
        stop_reason=stop_reason,
        stop_details=SimpleNamespace(category="bio") if stop_reason == "refusal" else None,
        usage=SimpleNamespace(
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
            input_tokens=10,
            output_tokens=5,
        ),
    )


class _FakeBatches:
    """Records the submitted batch and replays canned per-item results."""

    def __init__(self, results: dict[str, SimpleNamespace]) -> None:
        self.submitted: list[dict[str, Any]] = []
        self._results = results

    def create(self, requests: list[dict[str, Any]]) -> SimpleNamespace:
        self.submitted = list(requests)
        return SimpleNamespace(id="batch_1")

    def retrieve(self, batch_id: str) -> SimpleNamespace:
        return SimpleNamespace(id=batch_id, processing_status="ended")

    def results(self, batch_id: str) -> list[SimpleNamespace]:
        return [
            SimpleNamespace(custom_id=custom_id, result=result)
            for custom_id, result in self._results.items()
        ]


def test_effort_and_format_compose() -> None:
    out = _provider("low")._output_config({"type": "object"})

    assert out == {
        "output_config": {
            "effort": "low",
            "format": {"type": "json_schema", "schema": {"type": "object"}},
        }
    }


def test_effort_only_when_no_schema() -> None:
    assert _provider("low")._output_config(None) == {"output_config": {"effort": "low"}}


def test_empty_when_neither_set() -> None:
    assert _provider(None)._output_config(None) == {}


def test_message_params_cache_the_system_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(CONFIG, "anthropic_prompt_cache", True)

    params = _provider("low")._message_params("sys", "usr", {"type": "object"})

    assert params["model"] == "claude-sonnet-5-5"
    assert params["system"] == [{"type": "text", "text": "sys", "cache_control": EPHEMERAL}]
    assert params["messages"] == [
        {"role": "user", "content": [{"type": "text", "text": "usr"}]}
    ]
    assert params["output_config"]["effort"] == "low"
    assert "temperature" not in params and "thinking" not in params


def test_message_params_without_auto_cache_keep_explicit_breakpoints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(CONFIG, "anthropic_prompt_cache", False)
    system = [CacheSegment("shared", cache=True), CacheSegment("tail")]

    params = _provider(None)._message_params(system, "usr", None)

    assert params["system"] == [
        {"type": "text", "text": "shared", "cache_control": EPHEMERAL},
        {"type": "text", "text": "tail"},
    ]


def test_batch_items_carry_cache_control_like_sync_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(CONFIG, "anthropic_prompt_cache", True)
    provider = _provider("low")
    batches = _FakeBatches(
        {
            "a": SimpleNamespace(type="succeeded", message=_message('{"v": 1}')),
            "b": SimpleNamespace(type="succeeded", message=_message("cut", "max_tokens")),
            "c": SimpleNamespace(type="succeeded", message=_message("partial", "refusal")),
            "d": SimpleNamespace(type="errored"),
        }
    )
    provider._client = SimpleNamespace(messages=SimpleNamespace(batches=batches))
    requests = [
        BatchRequest(
            request_id=request_id,
            system="shared system prompt",
            user=[CacheSegment("catalog", cache=True), CacheSegment(f"item {request_id}")],
            response_schema={"type": "object"},
        )
        for request_id in "abcd"
    ]

    out = provider.generate_batch(requests)

    assert [item["custom_id"] for item in batches.submitted] == ["a", "b", "c", "d"]
    for item, request in zip(batches.submitted, requests):
        assert item["params"] == provider._message_params(
            request.system, request.user, request.response_schema
        )
        assert item["params"]["system"][-1]["cache_control"] == EPHEMERAL
        assert item["params"]["messages"][0]["content"][0]["cache_control"] == EPHEMERAL
    # A truncated item is flagged; a refused or errored one never yields text.
    assert out == {
        "a": ('{"v": 1}', False),
        "b": ("cut", True),
        "c": ("", False),
        "d": ("", False),
    }


class _FakeStream:
    """Stands in for ``messages.stream(...)``: records the params, replays a message."""

    def __init__(self, message: SimpleNamespace) -> None:
        self.message = message
        self.params: dict[str, Any] = {}

    def __call__(self, **params: Any) -> "_FakeStream":
        self.params = params
        return self

    def __enter__(self) -> "_FakeStream":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def get_final_message(self) -> SimpleNamespace:
        return self.message


def test_generate_raises_on_refusal_instead_of_returning_text() -> None:
    provider = _provider("low")
    provider._client = SimpleNamespace(
        messages=SimpleNamespace(stream=_FakeStream(_message("", "refusal")))
    )

    with pytest.raises(RuntimeError, match="category=bio"):
        provider.generate("sys", "usr")


def test_generate_streams_and_reads_text_blocks_by_type() -> None:
    provider = _provider("low")
    stream = _FakeStream(_message("hello"))
    provider._client = SimpleNamespace(messages=SimpleNamespace(stream=stream))

    assert provider.generate("sys", "usr") == ("hello", False)
    assert stream.params["model"] == "claude-sonnet-5-5"
    assert stream.params["output_config"] == {"effort": "low"}


def test_every_configured_stage_builds_a_client() -> None:
    stages = CONFIG["stages"]

    for stage, resolved in stages.items():
        assert LLMClient.for_stage(stage).model == resolved["model"]
    # Names the pipeline already uses, plus the ones the new steps are built on.
    assert set(stages) >= {
        "classify", "extract", "plan", "generate", "research",
        "write", "review", "apply", "propose", "update",
    }
