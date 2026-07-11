"""Gemini provider config wiring.

Guards the Gemini 3.x fixes: temperature is never sent (the model wants its 1.0
default), a set ``thinking_param`` becomes a ThinkingConfig, and the system
prompt travels via ``system_instruction`` rather than being glued onto contents.
"""

from __future__ import annotations

import pytest

pytest.importorskip("google.genai")

from utils.llm_client.models import BatchRequest
from utils.llm_client.providers.gemini import GeminiProvider


def _provider(thinking_param: str | None) -> GeminiProvider:
    return GeminiProvider(
        model="gemini-3.5-flash",
        temperature=0.2,  # must be ignored for Gemini 3.x
        max_tokens=1024,
        batch_poll_interval=1.0,
        thinking_param=thinking_param,
    )


def test_config_omits_temperature_and_wires_thinking_and_system() -> None:
    cfg = _provider("low")._config(
        json_mode=True, response_schema={"type": "object"}, system="sys"
    )

    assert cfg.temperature is None  # never forces a low temperature on Gemini 3.x
    assert cfg.system_instruction == "sys"
    assert cfg.response_mime_type == "application/json"
    assert cfg.response_json_schema == {"type": "object"}
    assert cfg.thinking_config is not None
    # thinking_level normalizes to the LOW enum regardless of input casing.
    assert cfg.thinking_config.thinking_level.name == "LOW"


def test_config_without_thinking_level_leaves_default() -> None:
    cfg = _provider(None)._config(json_mode=False, system="sys")

    assert cfg.thinking_config is None
    assert cfg.response_mime_type is None
    assert cfg.response_json_schema is None


def test_grounded_config_passes_tools_and_stays_non_json() -> None:
    from google.genai import types as genai_types

    tools = [genai_types.Tool(google_search=genai_types.GoogleSearch())]
    cfg = _provider("low")._config(json_mode=False, system="sys", tools=tools)

    assert cfg.tools == tools
    assert cfg.response_mime_type is None  # grounded wants prose, not JSON
    assert cfg.response_json_schema is None


def test_batch_inline_request_config_matches_single_config() -> None:
    provider = _provider("low")
    req = BatchRequest(
        request_id="r1",
        system="sys",
        user="hello",
        response_schema={"type": "object"},
    )

    inline = provider._inline_request(req)
    cfg = inline.config
    expected = provider._config(
        json_mode=True, response_schema={"type": "object"}, system="sys"
    )

    assert inline.metadata == {"key": "r1"}
    assert cfg.system_instruction == expected.system_instruction == "sys"
    assert cfg.response_mime_type == expected.response_mime_type == "application/json"
    assert cfg.response_json_schema == expected.response_json_schema == {"type": "object"}
    assert cfg.thinking_config.thinking_level == expected.thinking_config.thinking_level
