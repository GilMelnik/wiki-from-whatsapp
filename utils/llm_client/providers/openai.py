"""OpenAI provider: plain chat completions. No batch or grounded support."""

from __future__ import annotations

from typing import Any

import openai

from utils.llm_client.models import PromptInput
from utils.llm_client.prompts import _flatten
from utils.llm_client.providers.base import LLMProvider


class OpenAIProvider(LLMProvider):
    name = "openai"
    supports_batch = False
    supports_grounded = False

    def generate(
        self,
        system: PromptInput,
        user: PromptInput,
        *,
        json_mode: bool = False,
        response_schema: dict[str, Any] | None = None,
    ) -> tuple[str, bool]:
        if self._client is None:
            self._client = openai.OpenAI()
        response = self._client.chat.completions.create(
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            messages=[
                {"role": "system", "content": _flatten(system)},
                {"role": "user", "content": _flatten(user)},
            ],
        )
        choice = response.choices[0]
        return choice.message.content or "", choice.finish_reason == "length"
