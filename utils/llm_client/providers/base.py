"""Abstract provider interface.

Each concrete provider owns its SDK client (constructed on first use) and
converts prompts to whatever shape its API expects. ``generate`` returns
``(text, truncated)`` where
``truncated`` marks a max_tokens cutoff. Grounded search and batch are optional
capabilities; the base raises ``NotImplementedError`` and advertises support via
``supports_grounded`` / ``supports_batch`` so the client can fail fast.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Sequence

from utils.llm_client.models import BatchRequest, GroundedResult, PromptInput


class LLMProvider(ABC):
    name: str = ""
    supports_batch: bool = False
    supports_grounded: bool = False

    def __init__(
        self,
        model: str,
        temperature: float,
        max_tokens: int,
        batch_poll_interval: float,
        logger: logging.Logger | None = None,
        thinking_param: str | None = None,
    ) -> None:
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.batch_poll_interval = batch_poll_interval
        self.logger = logger or logging.getLogger("utils.llm_client")
        self.thinking_param = thinking_param
        self._client: Any = None

    @abstractmethod
    def generate(
        self,
        system: PromptInput,
        user: PromptInput,
        *,
        json_mode: bool = False,
        response_schema: dict[str, Any] | None = None,
    ) -> tuple[str, bool]:
        """Return ``(text, truncated)`` for a single completion."""

    def generate_grounded(self, system: str, user: str) -> GroundedResult:
        raise NotImplementedError(
            f"Provider {self.name!r} does not support grounded search"
        )

    def generate_batch(
        self, requests: Sequence[BatchRequest]
    ) -> dict[str, tuple[str, bool]]:
        raise NotImplementedError(f"Provider {self.name!r} does not support batch")
