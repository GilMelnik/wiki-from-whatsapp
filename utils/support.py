"""Compute claim support and opposition from message authors and reactions.

Supporters are distinct users who authored a supporting message or reacted with a
*positive* emoji on one. Opposers are distinct users who authored an opposing
message or reacted with a *negative* emoji. Neutral reactions count toward
neither side. Emoji sentiment is independent of claim stance — a 👎 never adds
a supporter. A user is never double-counted within the same side.
"""

from __future__ import annotations

import json
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, TypedDict

ReactionSentiment = Literal["positive", "neutral", "negative"]
_SENTIMENT_PATH = Path(__file__).with_name("reaction_sentiment.json")


class ClaimEngagement(TypedDict):
    supporter_count: int
    opposer_count: int


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


def _reaction_senders(
    reactions: list[dict[str, Any]] | None,
    *,
    sentiment: ReactionSentiment | None = None,
) -> set[str]:
    senders: set[str] = set()
    for reaction in reactions or []:
        emoji = reaction.get("emoji") or ""
        if sentiment is not None and reaction_sentiment(emoji) != sentiment:
            continue
        for sender in reaction.get("senders") or []:
            if sender:
                senders.add(sender)
    return senders


def reaction_senders_from_messages(
    message_reactions: list[dict[str, Any]],
    *,
    sentiment: ReactionSentiment,
) -> set[str]:
    """Distinct users who reacted with the given emoji sentiment on linked messages."""

    senders: set[str] = set()
    for entry in message_reactions:
        senders.update(
            _reaction_senders(entry.get("reactions"), sentiment=sentiment)
        )
    return senders


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


def format_reactions_for_llm(reactions: list[dict[str, Any]] | None) -> str:
    """Anonymized reaction summary appended to a rendered message line."""

    parts = summarize_reactions(reactions)
    if not parts:
        return ""
    detail = ", ".join(
        f"{item['emoji']}×{item['count']}" for item in parts if item["emoji"]
    )
    return f" [תגובות: {detail}]" if detail else ""


def aggregate_reaction_summary(
    message_reactions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Sum reaction counts by emoji across all linked messages."""

    counts: Counter[str] = Counter()
    for entry in message_reactions:
        for reaction in entry.get("reactions") or []:
            emoji = reaction.get("emoji") or ""
            if emoji:
                counts[emoji] += len(reaction.get("senders") or [])
    return [
        {
            "emoji": emoji,
            "count": count,
            "sentiment": reaction_sentiment(emoji),
        }
        for emoji, count in counts.items()
    ]


DEFAULT_AUDIT_PATH = Path("data/audit/claims_audit.json")


def load_audit_records(
    audit_path: Path | str = DEFAULT_AUDIT_PATH,
) -> dict[str, dict[str, Any]]:
    """claim_id -> private audit record (supporter identities + reactions)."""

    path = Path(audit_path)
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        audit = json.load(f)
    return {rec["claim_id"]: rec for rec in audit.get("audit") or []}


EngagementSide = Literal["supporter", "opposer"]


def participants_from_audit(
    record: dict[str, Any], *, side: EngagementSide
) -> set[str]:
    """Distinct users on one side of a claim (statements + matching emoji reactions)."""

    if side == "supporter":
        statement_key = "supporting_senders"
        sentiment: ReactionSentiment = "positive"
    else:
        statement_key = "opposing_senders"
        sentiment = "negative"

    participants = set(record.get(statement_key) or [])
    message_reactions = record.get("message_reactions")
    if message_reactions is not None:
        participants.update(
            reaction_senders_from_messages(message_reactions, sentiment=sentiment)
        )
        return participants

    if side == "supporter":
        # Legacy audit rows may list every reactor in reaction_senders; without
        # per-emoji detail we cannot re-filter, so ignore that field.
        if record.get("all_supporters") and not record.get("reaction_senders"):
            return set(record["all_supporters"])
        return participants

    if record.get("reaction_opposers"):
        participants.update(record["reaction_opposers"])
    return participants


def engagement_for_claim(
    claim_id: str, audit_by_id: dict[str, dict[str, Any]]
) -> ClaimEngagement:
    record = audit_by_id.get(claim_id)
    if not record:
        return {"supporter_count": 1, "opposer_count": 0}
    supporters = participants_from_audit(record, side="supporter")
    opposers = participants_from_audit(record, side="opposer")
    return {
        "supporter_count": max(len(supporters), 1),
        "opposer_count": len(opposers),
    }


def supporter_count_for_claims(
    claim_ids: list[str],
    audit_by_id: dict[str, dict[str, Any]],
) -> int:
    """Distinct supporters backing a set of claims, deduped by identity."""

    supporters: set[str] = set()
    for cid in claim_ids:
        record = audit_by_id.get(cid)
        if record:
            supporters.update(participants_from_audit(record, side="supporter"))
    if supporters:
        return len(supporters)
    # ponytail: no audit (e.g. mock/offline) — can't dedup, report at least 1.
    return 1 if claim_ids else 0


def engagement_for_claims(
    claim_ids: list[str],
    audit_by_id: dict[str, dict[str, Any]],
) -> ClaimEngagement:
    """Distinct supporters and opposers across a set of claims."""

    supporters: set[str] = set()
    opposers: set[str] = set()
    for cid in claim_ids:
        record = audit_by_id.get(cid)
        if record:
            supporters.update(participants_from_audit(record, side="supporter"))
            opposers.update(participants_from_audit(record, side="opposer"))
    return {
        "supporter_count": max(len(supporters), 1) if claim_ids else 0,
        "opposer_count": len(opposers),
    }


def compute_support(
    thread: dict[str, Any],
    line_meta: list[dict[str, Any]],
    local_message_ids: list[int],
    *,
    opposing_local_message_ids: list[int] | None = None,
) -> dict[str, Any]:
    """Derive supporter/opposer sets and reaction trace for a claim.

    ``local_message_ids`` are indices into ``line_meta`` (rendered lines),
    not raw ``thread["messages"]`` indices.
    """

    messages = thread.get("messages", [])
    message_ids = thread.get("message_ids", [])

    statement_senders: set[str] = set()
    opposing_senders: set[str] = set()
    reaction_supporters: set[str] = set()
    reaction_opposers: set[str] = set()
    message_reactions: list[dict[str, Any]] = []

    linked_ids = set(local_message_ids)
    linked_ids.update(opposing_local_message_ids or [])

    for local_id in sorted(linked_ids):
        meta = line_meta[local_id]
        if local_id in local_message_ids:
            statement_senders.add(meta["sender"])

        msg_index = meta["message_index"]
        msg = messages[msg_index]
        rx_list = msg.get("reactions") or meta.get("reactions") or []
        reaction_supporters.update(
            _reaction_senders(rx_list, sentiment="positive")
        )
        reaction_opposers.update(_reaction_senders(rx_list, sentiment="negative"))

        rx_audit: list[dict[str, Any]] = []
        for reaction in rx_list:
            senders = [s for s in (reaction.get("senders") or []) if s]
            if not senders:
                continue
            emoji = reaction.get("emoji", "")
            rx_audit.append(
                {
                    "emoji": emoji,
                    "senders": senders,
                    "sentiment": reaction_sentiment(emoji),
                }
            )

        if rx_audit:
            global_id = message_ids[msg_index] if msg_index < len(message_ids) else None
            message_reactions.append(
                {
                    "local_message_id": local_id,
                    "message_index": msg_index,
                    "global_message_id": global_id,
                    "reactions": rx_audit,
                }
            )

    for local_id in opposing_local_message_ids or []:
        opposing_senders.add(line_meta[local_id]["sender"])

    all_supporters = statement_senders | reaction_supporters
    all_opposers = opposing_senders | reaction_opposers
    reaction_only = reaction_supporters - statement_senders

    return {
        "statement_senders": sorted(statement_senders),
        "opposing_senders": sorted(opposing_senders),
        "reaction_senders": sorted(reaction_supporters),
        "reaction_opposers": sorted(reaction_opposers),
        "all_supporters": sorted(all_supporters),
        "all_opposers": sorted(all_opposers),
        "support_count": max(len(all_supporters), 1),
        "opposer_count": len(all_opposers),
        "statement_count": len(statement_senders),
        "reaction_endorser_count": len(reaction_supporters),
        "reaction_only_count": len(reaction_only),
        "message_reactions": message_reactions,
        "reaction_summary": aggregate_reaction_summary(message_reactions),
    }
