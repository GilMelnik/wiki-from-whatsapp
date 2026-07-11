"""Gemini provider: sync + batch JSON completions and Google Search grounding.

Gemini 3.x always reasons (thinking can't be disabled), so for JSON tasks we ask
the API for JSON directly (``response_mime_type``), optionally hard-constrained
by a raw JSON Schema, so the reply is well-formed rather than fenced or mixed
with reasoning prose. Reasoning effort is instead dialled with the config's
``thinking_param`` (mapped to ``thinking_level``: minimal/low/medium/high) so
cheap stages don't overthink.

Temperature is deliberately not sent: Gemini 3.x is optimised for the default of
1.0, and lower values can cause looping, degraded reasoning, or empty structured
output.
"""

from __future__ import annotations

import re
import time
from typing import Any, Sequence

from google import genai
from google.genai import types as genai_types

from utils.llm_client.models import (
    BatchRequest,
    GroundedCitation,
    GroundedResult,
    PromptInput,
)
from utils.llm_client.prompts import _flatten, _gemini_truncated
from utils.llm_client.providers.base import LLMProvider


class GeminiProvider(LLMProvider):
    name = "gemini"
    supports_batch = True
    supports_grounded = True

    def _thinking(self) -> Any:
        """A ThinkingConfig when a level is set, else None (model's default)."""

        if not self.thinking_param:
            return None
        # The SDK field is the ThinkingLevel enum; construct it from the config
        # string (case-insensitive) so an invalid value fails fast here.
        return genai_types.ThinkingConfig(
            thinking_level=genai_types.ThinkingLevel(self.thinking_param)
        )

    def _config(
        self,
        json_mode: bool,
        response_schema: dict[str, Any] | None = None,
        system: str | None = None,
        tools: list[Any] | None = None,
    ) -> Any:
        # ponytail: temperature is intentionally omitted (Gemini 3.x wants the
        # 1.0 default). Ceiling: a Gemini 2.5 model configured here would lose
        # temperature tuning; upgrade path is to send it only for non-3.x models.
        return genai_types.GenerateContentConfig(
            max_output_tokens=self.max_tokens,
            system_instruction=system,
            thinking_config=self._thinking(),
            response_mime_type="application/json" if json_mode else None,
            response_json_schema=response_schema if json_mode else None,
            tools=tools,
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
            self._client = genai.Client()
        system_text, user_text = _flatten(system), _flatten(user)
        response = self._client.models.generate_content(
            model=self.model,
            contents=user_text,
            config=self._config(json_mode, response_schema, system=system_text),
        )
        return response.text or "", _gemini_truncated(response)

    def generate_grounded(self, system: str, user: str) -> GroundedResult:
        if self._client is None:
            self._client = genai.Client()

        config = self._config(
            json_mode=False,
            system=system,
            tools=[genai_types.Tool(google_search=genai_types.GoogleSearch())],
        )
        response = self._client.models.generate_content(
            model=self.model,
            contents=user,
            config=config,
        )
        text = response.text or ""
        citations, search_queries = _parse_grounding_metadata(response)
        if not citations:
            citations = _citations_from_urls(text)
        return GroundedResult(
            text=text,
            citations=citations,
            search_queries=search_queries,
        )

    def _inline_request(self, req: BatchRequest) -> Any:
        # Extract/classify/plan all want JSON, so batch always runs json_mode via
        # the shared _config so it can't drift from the single-call path.
        return genai_types.InlinedRequest(
            contents=req.user,
            metadata={"key": req.request_id},
            config=self._config(
                json_mode=True,
                response_schema=req.response_schema,
                system=req.system,
            ),
        )

    def generate_batch(
        self, requests: Sequence[BatchRequest]
    ) -> dict[str, tuple[str, bool]]:
        if self._client is None:
            self._client = genai.Client()

        inline_requests = [self._inline_request(req) for req in requests]

        batch_job = self._client.batches.create(
            model=self.model,
            src=inline_requests,
            config={"display_name": "wiki-build-batch"},
        )
        self.logger.info(
            f"Gemini batch {batch_job.name} submitted "
            f"({len(requests)} requests, ~50% cheaper)..."
        )

        job_name = batch_job.name
        while True:
            batch_job = self._client.batches.get(name=job_name)
            state_name = batch_job.state.name if batch_job.state else ""
            if state_name in genai_types.JOB_STATES_ENDED:
                break
            self.logger.info(f"Gemini batch {job_name}: {state_name}")
            time.sleep(self.batch_poll_interval)

        if state_name not in genai_types.JOB_STATES_SUCCEEDED:
            raise RuntimeError(f"Gemini batch job ended with state {state_name!r}")

        out: dict[str, tuple[str, bool]] = {}
        dest = batch_job.dest
        inlined = dest.inlined_responses if dest else None
        if not inlined:
            return out

        for inline_resp in inlined:
            request_id = None
            if inline_resp.metadata:
                request_id = inline_resp.metadata.get("key")
            if inline_resp.response:
                out[request_id or ""] = (
                    inline_resp.response.text or "",
                    _gemini_truncated(inline_resp.response),
                )
            elif inline_resp.error:
                self.logger.warning(
                    f"Gemini batch item {request_id} error: {inline_resp.error}"
                )
                if request_id:
                    out[request_id] = ("", False)
        return out


def _parse_grounding_metadata(
    response: Any,
) -> tuple[tuple[GroundedCitation, ...], tuple[str, ...]]:
    citations: list[GroundedCitation] = []
    search_queries: list[str] = []
    seen_urls: set[str] = set()

    candidates = getattr(response, "candidates", None) or []
    for candidate in candidates:
        metadata = getattr(candidate, "grounding_metadata", None)
        if metadata is None:
            continue

        for query in getattr(metadata, "web_search_queries", None) or []:
            if query and query not in search_queries:
                search_queries.append(str(query))

        chunks = getattr(metadata, "grounding_chunks", None) or []
        for chunk in chunks:
            web = getattr(chunk, "web", None)
            if web is None:
                continue
            url = getattr(web, "uri", None) or getattr(web, "url", None) or ""
            title = getattr(web, "title", None) or url
            if url and url not in seen_urls:
                seen_urls.add(url)
                citations.append(GroundedCitation(title=str(title), url=str(url)))

        supports = getattr(metadata, "grounding_supports", None) or []
        for support in supports:
            for idx in getattr(support, "grounding_chunk_indices", None) or []:
                if 0 <= idx < len(chunks):
                    web = getattr(chunks[idx], "web", None)
                    if web is None:
                        continue
                    url = getattr(web, "uri", None) or getattr(web, "url", None) or ""
                    title = getattr(web, "title", None) or url
                    if url and url not in seen_urls:
                        seen_urls.add(url)
                        citations.append(GroundedCitation(title=str(title), url=str(url)))

    return tuple(citations), tuple(search_queries)


def _citations_from_urls(text: str) -> tuple[GroundedCitation, ...]:
    citations: list[GroundedCitation] = []
    seen: set[str] = set()
    for url in re.findall(r"https?://[^\s)\]>]+", text):
        url = url.rstrip(".,;")
        if url in seen:
            continue
        seen.add(url)
        citations.append(GroundedCitation(title=url, url=url))
    return tuple(citations)
