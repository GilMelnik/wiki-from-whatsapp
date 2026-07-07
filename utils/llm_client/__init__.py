"""Provider-agnostic LLM client package.

Public API (import from ``utils.llm_client``):

- ``LLMClient`` - the orchestrator (caching, JSON-retry, batch, failure logging)
- ``CacheSegment`` / ``BatchRequest`` - prompt/batch inputs
- ``GroundedCitation`` / ``GroundedResult`` - grounded-search outputs
- ``extract_json`` - lenient JSON parser
- ``web_search_enabled`` - whether Gemini grounding should run
- ``MAX_TOKENS_CEILING`` - the JSON-retry token ceiling

Configuration lives in ``config.json`` (see ``settings``); secrets in ``.env``.
"""

from __future__ import annotations

from utils.llm_client.client import LLMClient
from utils.llm_client.models import (
    BatchRequest,
    CacheSegment,
    GroundedCitation,
    GroundedResult,
)
from utils.llm_client.prompts import _flatten, _to_blocks, extract_json
from utils.llm_client.settings import CONFIG, web_search_enabled

MAX_TOKENS_CEILING = CONFIG["max_tokens_ceiling"]

__all__ = [
    "LLMClient",
    "BatchRequest",
    "CacheSegment",
    "GroundedCitation",
    "GroundedResult",
    "extract_json",
    "web_search_enabled",
    "MAX_TOKENS_CEILING",
    "_flatten",
    "_to_blocks",
]
