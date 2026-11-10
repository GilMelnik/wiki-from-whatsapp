"""Compute claim support and opposition from message authors and reactions.

Supporters are distinct users who authored a supporting message or reacted with a
*positive* emoji on one. Opposers are distinct users who authored an opposing
message, reacted with a *positive* emoji on an opposing message (agreeing with the
opposition) or with a *negative* emoji on a supporting one. Neutral reactions count
toward neither side. Emoji sentiment is independent of claim stance — a 👎 never adds
a supporter. A user is never double-counted within the same side.

``collect_evidence`` derives a claim's supporters / opposers / dates from its evidence
message ids (step 3). The ``*_audit*`` readers below understand the v1 audit records
still read by the old steps 5 and 7.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, TypedDict

from utils.paths import AUDIT_PATH

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


def load_audit_records(
    audit_path: Path | str = AUDIT_PATH,
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


def person_key(person: Any) -> str:
    """One stable identity string for a sender, from a plain id or an export
    ``{user_name, phone}`` dict (``user_name`` first: it is what thread messages carry).

    Identities are private: they go to the audit file, never to the LLM or the public claims.
    """

    if isinstance(person, dict):
        return str(person.get("user_name") or person.get("phone") or "")
    return str(person or "")


EvidenceSide = Literal["supporting", "opposing"]
# Who a reaction counts for, by the side of the message it is on and its sentiment. A
# positive reaction agrees with the message it is on, so on an OPPOSING message it backs the
# opposition. A negative reaction on an opposing message is not read as support (a sad face
# under a bad experience is sympathy, not disagreement), so it counts for neither side.
_REACTION_SIDE: dict[tuple[EvidenceSide, ReactionSentiment], EngagementSide] = {
    ("supporting", "positive"): "supporter",
    ("supporting", "negative"): "opposer",
    ("opposing", "positive"): "opposer",
}


@dataclass(frozen=True)
class Evidence:
    """Everything a claim's evidence messages say about who backs or disputes it.

    ``supporters`` / ``opposers`` are private events ``{person, date, via, message_id}``
    (``via`` is ``"message"`` for the author, ``"reaction"`` for a reactor; a reaction has no
    time of its own in the export, so it carries the date of the message it is on). The
    counts are distinct people per side; dates span the evidence messages (``YYYY-MM-DD``).
    """

    supporters: list[dict[str, str]]
    opposers: list[dict[str, str]]
    first_date: str | None
    last_date: str | None
    reaction_summary: list[dict[str, Any]]

    @property
    def supporter_count(self) -> int:
        return len({event["person"] for event in self.supporters})

    @property
    def opposer_count(self) -> int:
        return len({event["person"] for event in self.opposers})


def collect_evidence(
    messages_by_id: dict[str, dict[str, Any]],
    supporting_ids: list[str],
    opposing_ids: list[str],
) -> Evidence:
    """Derive supporters, opposers, dates and the reaction summary of one claim.

    ``messages_by_id`` maps WhatsApp ids to messages ``{sender, datetime, reactions?}`` whose
    reactions are ``[{emoji, senders}]``. The author of a supporting message supports the
    claim, the author of an opposing one opposes it; reactions count by ``_REACTION_SIDE``.
    An id missing from ``messages_by_id`` raises ``KeyError``: prune evidence first.
    """

    events: dict[EngagementSide, list[dict[str, str]]] = {"supporter": [], "opposer": []}
    seen: set[tuple[EngagementSide, str, str, str]] = set()
    reaction_counts: Counter[tuple[EvidenceSide, str]] = Counter()
    dates: list[str] = []

    def add(side: EngagementSide, person: str, date: str, via: str, message_id: str) -> None:
        key = (side, person, message_id, via)
        if person and key not in seen:
            seen.add(key)
            events[side].append(
                {"person": person, "date": date, "via": via, "message_id": message_id}
            )

    sides: list[tuple[EvidenceSide, list[str]]] = [
        ("supporting", supporting_ids),
        ("opposing", opposing_ids),
    ]
    for evidence_side, ids in sides:
        for message_id in ids:
            message = messages_by_id[message_id]
            date = str(message.get("datetime") or "")[:10]
            if date:
                dates.append(date)
            author_side: EngagementSide = (
                "supporter" if evidence_side == "supporting" else "opposer"
            )
            add(author_side, person_key(message.get("sender")), date, "message", message_id)
            for reaction in message.get("reactions") or []:
                emoji = reaction.get("emoji") or ""
                senders = [person_key(s) for s in reaction.get("senders") or []]
                senders = [s for s in senders if s]
                if not emoji or not senders:
                    continue
                reaction_counts[(evidence_side, emoji)] += len(senders)
                side = _REACTION_SIDE.get((evidence_side, reaction_sentiment(emoji)))
                if side is not None:
                    for sender in senders:
                        add(side, sender, date, "reaction", message_id)

    summary = [
        {
            "emoji": emoji,
            "side": evidence_side,
            "sentiment": reaction_sentiment(emoji),
            "count": count,
        }
        for (evidence_side, emoji), count in sorted(
            reaction_counts.items(),
            key=lambda item: (item[0][0] != "supporting", -item[1], item[0][1]),
        )
    ]
    return Evidence(
        supporters=events["supporter"],
        opposers=events["opposer"],
        first_date=min(dates) if dates else None,
        last_date=max(dates) if dates else None,
        reaction_summary=summary,
    )
