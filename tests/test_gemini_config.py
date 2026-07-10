"""Gemini provider config wiring.

Guards the Gemini 3.x fixes: temperature is never sent (the model wants its 1.0
default), a set ``thinking_level`` becomes a ThinkingConfig, and the system
prompt travels via ``system_instruction`` rather than being glued onto contents.
"""

from __future__ import annotations

import pytest

pytest.importorskip("google.genai")

from utils.llm_client.providers.gemini import GeminiProvider


def _provider(thinking_level: str | None) -> GeminiProvider:
    return GeminiProvider(
        model="gemini-3.5-flash",
        temperature=0.2,  # must be ignored for Gemini 3.x
        max_tokens=1024,
        batch_poll_interval=1.0,
        thinking_level=thinking_level,
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
