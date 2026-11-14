"""Compute claim support and opposition from message authors and reactions.

Supporters are distinct users who authored a supporting message or reacted with a
*positive* emoji on one. Opposers are distinct users who authored an opposing
message, reacted with a *positive* emoji on an opposing message (agreeing with the
opposition) or with a *negative* emoji on a supporting one. Neutral reactions count
toward neither side. Emoji sentiment is independent of claim stance — a 👎 never adds
a supporter. A user is never double-counted within the same side.

``collect_evidence`` derives a claim's supporters / opposers / dates from its evidence
message ids (step 3). Emoji sentiment is ``utils.reactions``; ``person_key`` is the thread
reader's (``utils.threads_io``).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Literal

from utils.reactions import ReactionSentiment, reaction_sentiment
from utils.threads_io import person_key

EngagementSide = Literal["supporter", "opposer"]
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
