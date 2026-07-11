"""Anthropic provider output_config wiring.

Guards that the shared ``thinking_param`` routes to ``output_config.effort`` and
composes with the JSON-schema structured-output ``format`` in one config object.
"""

from __future__ import annotations

import pytest

pytest.importorskip("anthropic")

from utils.llm_client.providers.anthropic import AnthropicProvider


def _provider(thinking_param: str | None) -> AnthropicProvider:
    return AnthropicProvider(
        model="claude-sonnet-5",
        temperature=0.2,
        max_tokens=1024,
        batch_poll_interval=1.0,
        thinking_param=thinking_param,
    )


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
