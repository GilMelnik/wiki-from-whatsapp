"""Gemini provider: sync + batch JSON completions and Google Search grounding.

Gemini 3.x always reasons (thinking can't be disabled), so for JSON tasks we ask
the API for JSON directly (``response_mime_type``), optionally hard-constrained
by a raw JSON Schema, so the reply is well-formed rather than fenced or mixed
with reasoning prose.
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

    def _config(
        self, json_mode: bool, response_schema: dict[str, Any] | None = None
    ) -> Any:
        return genai_types.GenerateContentConfig(
            temperature=self.temperature,
            max_output_tokens=self.max_tokens,
            response_mime_type="application/json" if json_mode else None,
            response_json_schema=response_schema if json_mode else None,
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
            contents=f"{system_text}\n\n{user_text}",
            config=self._config(json_mode, response_schema),
        )
        return response.text or "", _gemini_truncated(response)

    def generate_grounded(self, system: str, user: str) -> GroundedResult:
        if self._client is None:
            self._client = genai.Client()

        config = genai_types.GenerateContentConfig(
            tools=[genai_types.Tool(google_search=genai_types.GoogleSearch())],
            temperature=self.temperature,
            max_output_tokens=self.max_tokens,
        )
        response = self._client.models.generate_content(
            model=self.model,
            contents=f"{system}\n\n{user}",
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

    def generate_batch(
        self, requests: Sequence[BatchRequest]
    ) -> dict[str, tuple[str, bool]]:
        if self._client is None:
            self._client = genai.Client()

        inline_requests = [
            {
                "contents": [
                    {
                        "parts": [{"text": f"{req.system}\n\n{req.user}"}],
                        "role": "user",
                    }
                ],
                "metadata": {"key": req.request_id},
                "config": {
                    "temperature": self.temperature,
                    "max_output_tokens": self.max_tokens,
                    # Extract/classify/plan all want JSON; asking for it directly
                    # keeps the model's reasoning prose out of the response. A
                    # schema (when given) hard-constrains the structure too.
                    "response_mime_type": "application/json",
                    **(
                        {"response_json_schema": req.response_schema}
                        if req.response_schema is not None
                        else {}
                    ),
                },
            }
            for req in requests
        ]

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
        state_name = ""
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
