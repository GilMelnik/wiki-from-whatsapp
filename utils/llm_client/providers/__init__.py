"""Provider registries and factories: chat providers and classifier providers."""

from __future__ import annotations

import logging

from utils.llm_client.providers.anthropic import (
    AnthropicClassifierProvider,
    AnthropicProvider,
)
from utils.llm_client.providers.base import ClassifierProvider, LLMProvider
from utils.llm_client.providers.typesafe import TypeSafeClassifierProvider


def create_provider(
    provider: str,
    model: str,
    temperature: float,
    max_tokens: int,
    batch_poll_interval: float,
    logger: logging.Logger | None = None,
    thinking_param: str | None = None,
) -> LLMProvider:
    registry: dict[str, type[LLMProvider]] = {
        provider_object.name: provider_object
        for provider_object in (AnthropicProvider,)
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
        thinking_param=thinking_param,
    )


def create_classifier_provider(
    provider: str, model: str, logger: logging.Logger
) -> ClassifierProvider:
    registry: dict[str, type[ClassifierProvider]] = {
        provider_object.name: provider_object
        for provider_object in (TypeSafeClassifierProvider, AnthropicClassifierProvider)
    }

    try:
        provider_cls = registry[provider]
    except KeyError:
        raise ValueError(
            f"Unknown classifier provider: {provider!r}; "
            f"expected one of {sorted(registry.keys())}"
        ) from None
    return provider_cls(model=model, logger=logger)
