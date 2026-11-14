"""Reaction emojis: their sentiment and their per-emoji counts.

Emoji sentiment (``reaction_sentiment.json``, next to this module) is independent of claim
stance: a 👎 is negative wherever it is. ``summarize_reactions`` serves the thread reader
(``utils.threads_io``); step 3's support counting (``step_3_extract.support``) reads the
sentiment too.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

ReactionSentiment = Literal["positive", "neutral", "negative"]
_SENTIMENT_PATH = Path(__file__).with_name("reaction_sentiment.json")


@lru_cache(maxsize=1)
def _load_reaction_sentiment() -> dict[str, str]:
    with _SENTIMENT_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def reaction_sentiment(emoji: str) -> ReactionSentiment:
    """Map a reaction emoji to positive, neutral, or negative."""

    if not emoji:
        return "neutral"
    category = _load_reaction_sentiment().get(emoji)
    # ponytail: unmapped emojis default neutral — won't inflate either side
    if category in ("positive", "neutral", "negative"):
        return category
    return "neutral"


def summarize_reactions(reactions: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Per-emoji reaction counts for audit / display (no sender ids)."""

    out: list[dict[str, Any]] = []
    for reaction in reactions or []:
        senders = [s for s in (reaction.get("senders") or []) if s]
        if not senders:
            continue
        emoji = reaction.get("emoji", "")
        out.append(
            {
                "emoji": emoji,
                "count": len(senders),
                "sentiment": reaction_sentiment(emoji),
            }
        )
    return out
