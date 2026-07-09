"""Provider registry and factory."""

from __future__ import annotations

import logging

from utils.llm_client.providers.anthropic import AnthropicProvider
from utils.llm_client.providers.base import LLMProvider
from utils.llm_client.providers.gemini import GeminiProvider
from utils.llm_client.providers.openai import OpenAIProvider


def create_provider(
    provider: str,
    model: str,
    temperature: float,
    max_tokens: int,
    batch_poll_interval: float,
    logger: logging.Logger | None = None,
) -> LLMProvider:
    registry: dict[str, type[LLMProvider]] = {
        provider_object.name: provider_object
        for provider_object in (AnthropicProvider, GeminiProvider, OpenAIProvider)
    }

    try:
        provider_cls = registry[provider]
    except KeyError:
        raise ValueError(f"Unknown LLM provider: {provider!r}; expected one of {sorted(registry.keys())}")
    return provider_cls(
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        batch_poll_interval=batch_poll_interval,
        logger=logger,
    )
