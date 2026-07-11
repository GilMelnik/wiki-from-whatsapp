"""Anthropic provider: prompt caching (cache_control), structured output, and
the batch API. Reasoning effort is set via ``output_config.effort`` (the shared
``thinking_param``; default ``high``). Sampling params (temperature/top_p/top_k)
are unsupported on Opus 4.7+/Sonnet 5 — a non-default value returns 400 — so
they are omitted and the API default is used.

Auto-caching of the system prompt on every ``generate`` call is controlled by
the ``anthropic_prompt_cache`` config flag; explicit ``CacheSegment`` breakpoints
in a prompt are always honoured.
"""

from __future__ import annotations

import time
from typing import Any, Sequence

import anthropic
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request

from utils.llm_client.settings import CONFIG
from utils.llm_client.models import BatchRequest, PromptInput
from utils.llm_client.prompts import _sanitize_batch_custom_id, _to_blocks
from utils.llm_client.providers.base import LLMProvider


class AnthropicProvider(LLMProvider):
    name = "anthropic"
    supports_batch = True
    supports_grounded = False

    def _output_config(self, response_schema: dict[str, Any] | None) -> dict[str, Any]:
        """Extra ``messages.create`` kwargs: reasoning ``effort`` (from the
        shared ``thinking_param``) and/or a JSON-schema structured-output format.
        """

        cfg: dict[str, Any] = {}
        if self.thinking_param:
            cfg["effort"] = self.thinking_param
        if response_schema is not None:
            cfg["format"] = {"type": "json_schema", "schema": response_schema}
        return {"output_config": cfg} if cfg else {}

    def generate(
        self,
        system: PromptInput,
        user: PromptInput,
        *,
        json_mode: bool = False,
        response_schema: dict[str, Any] | None = None,
    ) -> tuple[str, bool]:
        if self._client is None:
            self._client = anthropic.Anthropic()
        # No temperature: Sonnet 5+ rejects non-default sampling params (400).
        # Auto-caching the system prompt is opt-out via config; explicit
        # per-segment CacheSegment breakpoints are honoured regardless.
        message = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=_to_blocks(system, cache_last=CONFIG["anthropic_prompt_cache"]),
            messages=[{"role": "user", "content": _to_blocks(user)}],
            **self._output_config(response_schema),
        )
        self._log_cache_usage(message.usage)
        text = "".join(block.text for block in message.content if block.type == "text")
        return text, message.stop_reason == "max_tokens"

    def _log_cache_usage(self, usage: Any) -> None:
        """Print prompt-cache stats when ``log_cache`` is set in config.

        HIT means tokens were read from cache; WRITE means the prefix was (re)written;
        fresh is the uncached remainder.
        """

        if not CONFIG["log_cache"]:
            return
        read = getattr(usage, "cache_read_input_tokens", 0) or 0
        write = getattr(usage, "cache_creation_input_tokens", 0) or 0
        fresh = getattr(usage, "input_tokens", 0) or 0
        out = getattr(usage, "output_tokens", 0) or 0
        state = "HIT" if read else ("WRITE" if write else "MISS")
        self.logger.info(
            f"[cache {state}] read={read} write={write} fresh={fresh} "
            f"out={out} ({self.model})"
        )

    def generate_batch(
        self, requests: Sequence[BatchRequest]
    ) -> dict[str, tuple[str, bool]]:
        if self._client is None:
            self._client = anthropic.Anthropic()

        id_map = {
            _sanitize_batch_custom_id(req.request_id): req.request_id for req in requests
        }
        api_requests = [
            Request(
                custom_id=_sanitize_batch_custom_id(req.request_id),
                params=MessageCreateParamsNonStreaming(
                    model=self.model,
                    max_tokens=self.max_tokens,
                    system=req.system,
                    messages=[{"role": "user", "content": req.user}],
                    **self._output_config(req.response_schema),
                ),
            )
            for req in requests
        ]

        batch = self._client.messages.batches.create(requests=api_requests)
        self.logger.info(
            f"Anthropic batch {batch.id} submitted "
            f"({len(requests)} requests, ~50% cheaper)..."
        )

        while True:
            batch = self._client.messages.batches.retrieve(batch.id)
            if batch.processing_status == "ended":
                break
            counts = batch.request_counts
            self.logger.info(
                f"Anthropic batch {batch.id}: "
                f"processing={counts.processing}, succeeded={counts.succeeded}, "
                f"errored={counts.errored}"
            )
            time.sleep(self.batch_poll_interval)

        out: dict[str, tuple[str, bool]] = {}
        for result in self._client.messages.batches.results(batch.id):
            request_id = id_map.get(result.custom_id, result.custom_id)
            if result.result.type == "succeeded":
                message = result.result.message
                text = "".join(
                    block.text for block in message.content if block.type == "text"
                )
                out[request_id] = (text, message.stop_reason == "max_tokens")
            else:
                self.logger.warning(
                    f"Anthropic batch item {request_id} -> {result.result.type}"
                )
                out[request_id] = ("", False)
        return out
