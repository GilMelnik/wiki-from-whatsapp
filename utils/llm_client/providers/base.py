"""Abstract provider interfaces.

``LLMProvider`` (chat): each concrete provider owns its SDK client (constructed
on first use) and converts prompts to whatever shape its API expects.
``generate`` returns ``(text, truncated)`` where
``truncated`` marks a max_tokens cutoff. Grounded search and batch are optional
capabilities; the base raises ``NotImplementedError`` and advertises support via
``supports_grounded`` / ``supports_batch`` so the client can fail fast.

``ClassifierProvider`` (non-chat): answers typed questions about a state with
probabilities. It is a separate hierarchy because it shares nothing with a chat
completion but the SDK client.
"""

from __future__ import annotations

import logging
import threading
from abc import ABC, abstractmethod
from typing import Any, Sequence

from utils.llm_client.models import (
    Answer,
    BatchRequest,
    GroundedResult,
    PromptInput,
    Question,
)


class LLMProvider(ABC):
    name: str = ""
    supports_batch: bool = False
    supports_grounded: bool = False
    # Whether requests carry ``temperature``. The client keys its response cache
    # on temperature only when this is True: Anthropic and Gemini both omit it.
    sends_temperature: bool = False

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


class ClassifierProvider(ABC):
    """One classifier backend. ``classify`` may be called from several threads
    at once (``Classifier.classify_many``), so the SDK client is created once
    under a lock and must itself be safe to share between threads.

    Every failure of the backend — transport, HTTP status, refusal, malformed
    answer — is raised as ``ClassifierError`` so the caller can log and index it.
    """

    name: str = ""

    def __init__(self, model: str, logger: logging.Logger) -> None:
        self.model = model
        self.logger = logger
        self._client: Any = None
        self._client_lock = threading.Lock()

    @abstractmethod
    def _create_client(self) -> Any:
        """Build the SDK client (reads its API key from the environment)."""

    def _get_client(self) -> Any:
        with self._client_lock:
            if self._client is None:
                self._client = self._create_client()
            return self._client

    def cache_identity(self) -> dict[str, Any]:
        """Provider settings, beyond provider name and model, that change an
        answer; they become part of the classifier's cache key."""

        return {}

    @abstractmethod
    def classify(self, state: str, questions: Sequence[Question]) -> list[Answer]:
        """Answer every question about ``state``, in the order asked."""

    @staticmethod
    def _normalised_score(question: Question, position: float) -> float:
        """Map a position on a score question's ordered levels (0 .. last level)
        to 0.0 .. 1.0, so scales of different lengths are comparable."""

        top_level = len(question.options or ()) - 1
        return min(1.0, max(0.0, position / top_level))
