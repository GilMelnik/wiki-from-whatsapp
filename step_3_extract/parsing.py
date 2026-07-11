"""Normalize the extract model's ad-hoc JSON shapes into canonical claim dicts.

The extract model is sent ``EXTRACT_SCHEMA`` but does not enforce it, so it
returns claims under many shapes: bare lists, alternate keys (``claim``/``text``),
packed ``claims``/``insights`` lists, and ``[m..]``-style string ids. These
helpers find each field regardless of the exact keys the model chose so a single
``_normalize_claims`` call yields a flat list of canonical claim dicts.
"""

from __future__ import annotations

import re
from typing import Any

# Aliases let us find each field regardless of the exact keys the model chose.
_TEXT_KEYS = ("claim_text", "claim", "text")
_LIST_CLAIM_KEYS = ("claims", "insights")
_TAG_KEYS = (
    "topic_tags", "topics", "topic", "sub_topic", "subcategory", "category", "tag"
)
_STANCE_KEYS = ("stance", "sentiment", "tone", "tonality")
_SUPPORT_ID_KEYS = (
    "supporting_message_ids", "source_message_ids", "source_message", "source_message_id"
)
_STANCES = {"positive", "negative", "neutral", "factual"}


def _as_str_list(value: Any) -> list[str]:
    """Coerce a str or list-of-str into a list of non-empty trimmed strings."""

    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        return [v.strip() for v in value if isinstance(v, str) and v.strip()]
    return []


def _text_from(item: dict[str, Any]) -> str:
    for key in _TEXT_KEYS:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _tags_from(item: dict[str, Any]) -> list[str]:
    tags: list[str] = []
    for key in _TAG_KEYS:
        for tag in _as_str_list(item.get(key)):
            if tag not in tags:
                tags.append(tag)
    return tags


def _stance_from(item: dict[str, Any]) -> str:
    for key in _STANCE_KEYS:
        value = item.get(key)
        if isinstance(value, str) and value.strip().lower() in _STANCES:
            return value.strip().lower()
    if item.get("is_factual") is True:
        return "factual"
    if item.get("is_caution") is True or item.get("is_warning") is True:
        return "negative"
    return "neutral"


def _coerce_id(value: Any) -> int | None:
    """A message id as an int, or the digits of an ``[m..]``-style label ("m2" -> 2)."""

    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        match = re.search(r"\d+", value)
        if match:
            return int(match.group())
    return None


def _ids_from(item: dict[str, Any], keys: tuple[str, ...]) -> list[int]:
    ids: list[int] = []
    for key in keys:
        value = item.get(key)
        for element in value if isinstance(value, list) else [value]:
            coerced = _coerce_id(element)
            if coerced is not None:
                ids.append(coerced)
    return ids


def _canonical_claim(text: str, item: dict[str, Any]) -> dict[str, Any]:
    """One raw claim with the exact keys the pipeline expects, from any shape."""

    return {
        "claim_text": text,
        "topic_tags": _tags_from(item),
        "entities": item.get("entities") or [],
        "stance": _stance_from(item),
        "supporting_message_ids": _ids_from(item, _SUPPORT_ID_KEYS),
        "opposing_message_ids": _ids_from(item, ("opposing_message_ids",)),
    }


def _claims_from_item(item: Any) -> list[dict[str, Any]]:
    if isinstance(item, str):
        text = item.strip()
        return [_canonical_claim(text, {})] if text else []
    if not isinstance(item, dict):
        return []
    if not _text_from(item):
        # A container: several claims packed under a list-valued key.
        for key in _LIST_CLAIM_KEYS:
            elements = item.get(key)
            if isinstance(elements, list) and elements:
                packed: list[dict[str, Any]] = []
                for element in elements:
                    if isinstance(element, str) and element.strip():
                        packed.append(_canonical_claim(element.strip(), item))
                    elif isinstance(element, dict):
                        packed.extend(_claims_from_item(element))
                if packed:
                    return packed
    text = _text_from(item)
    return [_canonical_claim(text, item)] if text else []


def _normalize_claims(result: Any) -> list[dict[str, Any]]:
    """Coerce any LLM output shape into a flat list of canonical claim dicts."""

    if isinstance(result, dict):
        inner = result.get("claims")
        items = inner if isinstance(inner, list) else [result]
    elif isinstance(result, list):
        items = result
    else:
        return []
    claims: list[dict[str, Any]] = []
    for item in items:
        claims.extend(_claims_from_item(item))
    return claims
