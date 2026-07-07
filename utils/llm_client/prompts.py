"""Prompt/response plumbing: JSON extraction, prompt flattening, Anthropic
cache-control blocks, and provider response inspection.
"""

from __future__ import annotations

import json
import re
from typing import Any

from utils.llm_client.models import CacheSegment, PromptInput


def _strip_code_fence(text: str) -> str:
    """Remove a surrounding ```json ... ``` fence if present."""

    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[a-zA-Z]*\n?", "", stripped)
        stripped = re.sub(r"\n?```$", "", stripped)
    return stripped.strip()


def extract_json(text: str) -> Any:
    cleaned = _strip_code_fence(text)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # Fall back to the first balanced JSON object/array in the text.
        match = re.search(r"(\{.*\}|\[.*\])", cleaned, re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(1))


def _gemini_truncated(response: Any) -> bool:
    """True if a Gemini response stopped because it hit max_output_tokens."""

    for cand in getattr(response, "candidates", None) or []:
        reason = getattr(cand, "finish_reason", None)
        name = getattr(reason, "name", None) or (str(reason) if reason else "")
        if name.endswith("MAX_TOKENS"):
            return True
    return False


def _flatten(prompt: PromptInput) -> str:
    """Collapse a prompt into plain text for the disk cache key and non-anthropic providers."""

    if isinstance(prompt, str):
        return prompt
    return "".join(seg.text for seg in prompt)


def _to_blocks(prompt: PromptInput, *, cache_last: bool = False) -> list[dict[str, Any]]:
    """Build Anthropic text blocks, adding cache_control per-segment or on the final block."""

    if isinstance(prompt, str):
        segments = [CacheSegment(prompt)]
    else:
        segments = list(prompt)
    if not segments:
        segments = [CacheSegment("")]
    blocks: list[dict[str, Any]] = []
    for i, seg in enumerate(segments):
        block: dict[str, Any] = {"type": "text", "text": seg.text}
        if seg.cache or (cache_last and i == len(segments) - 1):
            block["cache_control"] = {"type": "ephemeral"}
        blocks.append(block)
    return blocks


def _sanitize_batch_custom_id(request_id: str) -> str:
    """Anthropic batch custom_id: 1-64 chars, alphanumeric / hyphen / underscore."""

    sanitized = re.sub(r"[^a-zA-Z0-9_-]", "_", request_id)
    return sanitized[:64] or "req"
