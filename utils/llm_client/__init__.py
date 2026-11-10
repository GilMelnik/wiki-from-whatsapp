"""Provider-agnostic LLM client package.

Public API (import from ``utils.llm_client``):

- ``LLMClient`` - the chat orchestrator (caching, JSON-retry, batch, failure logging)
- ``CacheSegment`` / ``BatchRequest`` - prompt/batch inputs
- ``Classifier`` with ``Question`` / ``Answer`` / ``ClassifierError`` - typed
  questions about a state, answered with probabilities (see ``classifier``)
- ``extract_json`` - lenient JSON parser
- ``MAX_TOKENS_CEILING`` - the JSON-retry token ceiling

Configuration lives in ``config.json`` (see ``settings``); secrets in ``.env``.
"""

from __future__ import annotations

from utils.llm_client.classifier import Classifier
from utils.llm_client.client import LLMClient
from utils.llm_client.models import (
    Answer,
    BatchRequest,
    CacheSegment,
    ClassifierError,
    Question,
)
from utils.llm_client.prompts import _flatten, _to_blocks, extract_json
from utils.llm_client.settings import CONFIG

MAX_TOKENS_CEILING = CONFIG["max_tokens_ceiling"]

__all__ = [
    "LLMClient",
    "Classifier",
    "Question",
    "Answer",
    "ClassifierError",
    "BatchRequest",
    "CacheSegment",
    "extract_json",
    "MAX_TOKENS_CEILING",
    "_flatten",
    "_to_blocks",
]
