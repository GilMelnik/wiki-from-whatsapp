"""Counting a request's input tokens: with the API's free counting endpoint, or by characters.

A ``TokenCounter`` takes ``(system, user)`` and returns the input tokens. Steps 3, 6, 7 and 8
check their prompts with ``AnthropicTokenCounter`` before a call; an estimate (step 8's parts,
the update's estimates, step 3's split experiment) counts by characters with
``CharTokenCounter``, which needs no endpoint.
"""

from __future__ import annotations

import math
from collections.abc import Callable

import anthropic

from utils.model_prices import ESTIMATE_CONFIG

TokenCounter = Callable[[str, str], int]


class AnthropicTokenCounter:
    """Counts a request's input tokens with the Messages API ``count_tokens`` endpoint (free)."""

    def __init__(self, model: str) -> None:
        self.model = model
        self.client = anthropic.Anthropic()

    def __call__(self, system: str, user: str) -> int:
        return self.client.messages.count_tokens(
            model=self.model,
            system=system,
            messages=[{"role": "user", "content": user}],
        ).input_tokens


class CharTokenCounter:
    """A ``TokenCounter`` that needs no endpoint: characters / ``chars_per_token``."""

    def __init__(self, chars_per_token: float = ESTIMATE_CONFIG["chars_per_token"]) -> None:
        self.chars_per_token = chars_per_token

    def __call__(self, system: str, user: str) -> int:
        return math.ceil((len(system) + len(user)) / self.chars_per_token)
