"""Compute claim support from message authors and reactions.

Each claim is backed by one or more supporting messages. Support counts
distinct users who either authored those messages or reacted to them.
A user is never counted twice, even if they both stated and reacted, or
appeared in multiple merged duplicate claims.
"""

from __future__ import annotations

from collections import Counter
from typing import Any


def _reaction_senders(reactions: list[dict[str, Any]] | None) -> set[str]:
    senders: set[str] = set()
    for reaction in reactions or []:
        for sender in reaction.get("senders") or []:
            if sender:
                senders.add(sender)
    return senders


def summarize_reactions(reactions: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Per-emoji reaction counts for audit / display (no sender ids)."""

    out: list[dict[str, Any]] = []
    for reaction in reactions or []:
        senders = [s for s in (reaction.get("senders") or []) if s]
        if not senders:
            continue
        out.append({"emoji": reaction.get("emoji", ""), "count": len(senders)})
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
    """Sum reaction counts by emoji across all supporting messages."""

    counts: Counter[str] = Counter()
    for entry in message_reactions:
        for reaction in entry.get("reactions") or []:
            emoji = reaction.get("emoji") or ""
            if emoji:
                counts[emoji] += len(reaction.get("senders") or [])
    return [{"emoji": emoji, "count": count} for emoji, count in counts.items()]


def compute_support(
    thread: dict[str, Any],
    line_meta: list[dict[str, Any]],
    local_message_ids: list[int],
) -> dict[str, Any]:
    """Derive supporter sets and reaction trace for a claim.

    ``local_message_ids`` are indices into ``line_meta`` (rendered lines),
    not raw ``thread["messages"]`` indices.
    """

    messages = thread.get("messages", [])
    message_ids = thread.get("message_ids", [])

    statement_senders: set[str] = set()
    reaction_senders: set[str] = set()
    message_reactions: list[dict[str, Any]] = []

    for local_id in local_message_ids:
        meta = line_meta[local_id]
        statement_senders.add(meta["sender"])

        msg_index = meta["message_index"]
        msg = messages[msg_index]
        rx_list = msg.get("reactions") or meta.get("reactions") or []
        reaction_senders.update(_reaction_senders(rx_list))

        rx_audit: list[dict[str, Any]] = []
        for reaction in rx_list:
            senders = [s for s in (reaction.get("senders") or []) if s]
            if not senders:
                continue
            rx_audit.append({"emoji": reaction.get("emoji", ""), "senders": senders})

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

    all_supporters = statement_senders | reaction_senders
    reaction_only = reaction_senders - statement_senders

    return {
        "statement_senders": sorted(statement_senders),
        "reaction_senders": sorted(reaction_senders),
        "all_supporters": sorted(all_supporters),
        "support_count": max(len(all_supporters), 1),
        "statement_count": len(statement_senders),
        "reaction_endorser_count": len(reaction_senders),
        "reaction_only_count": len(reaction_only),
        "message_reactions": message_reactions,
        "reaction_summary": aggregate_reaction_summary(message_reactions),
    }
