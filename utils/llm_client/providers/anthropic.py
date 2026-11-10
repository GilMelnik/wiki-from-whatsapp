"""Anthropic provider: prompt caching (cache_control), structured output, and
the batch API. Reasoning effort is set via ``output_config.effort`` (the shared
``thinking_param``; when unset the model default applies — ``medium`` on Opus
5.5 and Haiku 5.5, ``high`` on Sonnet 5.5). ``thinking`` itself is never sent:
the 5.x models think adaptively by default and Opus 5.5 / Sonnet 5.5 reject
``disabled``. Sampling params (temperature/top_p/top_k) are unsupported on Opus
4.7+/Sonnet 5 — a non-default value returns 400 — so they are omitted and the
API default is used.

Auto-caching of the system prompt, on sync calls and on every batch item alike,
is controlled by the ``anthropic_prompt_cache`` config flag; explicit
``CacheSegment`` breakpoints in a prompt are always honoured.
"""

from __future__ import annotations

import time
from typing import Any, Sequence, cast

import anthropic
from anthropic.types import (
    JSONOutputFormatParam,
    Message,
    MessageParam,
    OutputConfigParam,
    Usage,
)
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

    def _output_config(
        self, response_schema: dict[str, Any] | None
    ) -> dict[str, OutputConfigParam]:
        """Extra ``messages.create`` kwargs: reasoning ``effort`` (from the
        shared ``thinking_param``) and/or a JSON-schema structured-output format.
        """

        cfg: OutputConfigParam = {}
        if self.thinking_param:
            # effort is a fixed Literal in the SDK; thinking_param is a free
            # config string, so cast (an invalid value is rejected by the API).
            cfg["effort"] = cast(Any, self.thinking_param)
        if response_schema is not None:
            cfg["format"] = JSONOutputFormatParam(
                type="json_schema", schema=response_schema
            )
        return {"output_config": cfg} if cfg else {}

    def _message_params(
        self,
        system: PromptInput,
        user: PromptInput,
        response_schema: dict[str, Any] | None,
    ) -> MessageCreateParamsNonStreaming:
        """The request body of one message, shared by the sync and batch paths
        so prompt caching and output settings cannot drift between them.

        No temperature: Sonnet 5+ rejects non-default sampling params (400).
        The system prompt is auto-cached (a ``cache_control`` breakpoint on its
        last block) unless ``anthropic_prompt_cache`` is off; explicit
        ``CacheSegment(cache=True)`` breakpoints are honoured regardless.
        """

        return MessageCreateParamsNonStreaming(
            model=self.model,
            max_tokens=self.max_tokens,
            system=_to_blocks(system, cache_last=CONFIG["anthropic_prompt_cache"]),
            messages=[MessageParam(role="user", content=_to_blocks(user))],
            **self._output_config(response_schema),
        )

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
        message = self._client.messages.create(
            **self._message_params(system, user, response_schema)
        )
        self._log_cache_usage([message.usage])
        refusal = _refusal(message)
        if refusal:
            raise RuntimeError(
                f"Anthropic {self.model} declined the request ({refusal})"
            )
        return _message_text(message), message.stop_reason == "max_tokens"

    def _log_cache_usage(self, usages: Sequence[Usage]) -> None:
        """Log prompt-cache stats, summed over ``usages``, when ``log_cache`` is
        set in config.

        HIT means tokens were read from cache; WRITE means the prefix was (re)written;
        fresh is the uncached remainder.
        """

        if not CONFIG["log_cache"] or not usages:
            return
        read = sum(usage.cache_read_input_tokens or 0 for usage in usages)
        write = sum(usage.cache_creation_input_tokens or 0 for usage in usages)
        fresh = sum(usage.input_tokens for usage in usages)
        out = sum(usage.output_tokens for usage in usages)
        state = "HIT" if read else ("WRITE" if write else "MISS")
        self.logger.info(
            f"[cache {state}] read={read} write={write} fresh={fresh} "
            f"out={out} ({self.model}, {len(usages)} response(s))"
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
                params=self._message_params(
                    req.system, req.user, req.response_schema
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
        usages: list[Usage] = []
        for result in self._client.messages.batches.results(batch.id):
            request_id = id_map.get(result.custom_id, result.custom_id)
            if result.result.type != "succeeded":
                self.logger.warning(
                    f"Anthropic batch item {request_id} -> {result.result.type}"
                )
                out[request_id] = ("", False)
                continue
            message = result.result.message
            usages.append(message.usage)
            refusal = _refusal(message)
            if refusal:
                # Whatever text precedes a refusal is not a usable answer.
                self.logger.warning(
                    f"Anthropic batch item {request_id} declined ({refusal})"
                )
                out[request_id] = ("", False)
                continue
            out[request_id] = (
                _message_text(message),
                message.stop_reason == "max_tokens",
            )
        self._log_cache_usage(usages)
        return out


def _message_text(message: Message) -> str:
    """Join the text blocks; a response may begin with (empty) thinking blocks."""

    return "".join(block.text for block in message.content if block.type == "text")


def _refusal(message: Message) -> str | None:
    """Describe a safety-classifier refusal, or ``None`` for a normal response.

    A refusal arrives as HTTP 200 with ``stop_reason == "refusal"`` and partial
    or empty content, so it must be checked before the text is used.
    """

    if message.stop_reason != "refusal":
        return None
    details = message.stop_details
    return f"stop_reason=refusal, category={details.category if details else None}"
