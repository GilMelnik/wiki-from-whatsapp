"""Support counts: emoji sentiment is independent of claim stance."""

from utils.support import (
    compute_support,
    engagement_for_claim,
    participants_from_audit,
    supporter_count_for_claims,
)


def _audit_record(reactions: list[dict], *, opposing: list[str] | None = None) -> dict:
    return {
        "supporting_senders": ["author"],
        "opposing_senders": opposing or [],
        "message_reactions": [{"reactions": reactions}],
        "reaction_senders": [s for r in reactions for s in r["senders"]],
        "all_supporters": ["author"]
        + [s for r in reactions for s in r["senders"]],
    }


def test_only_positive_emojis_count_as_supporters():
    record = _audit_record(
        [
            {"emoji": "👍", "senders": ["a"]},
            {"emoji": "❤️", "senders": ["b"]},
            {"emoji": "👎", "senders": ["c"]},
        ]
    )
    assert participants_from_audit(record, side="supporter") == {"author", "a"}


def test_negative_emojis_count_as_opposers_not_supporters():
    record = _audit_record([{"emoji": "👎", "senders": ["c"]}])
    assert participants_from_audit(record, side="supporter") == {"author"}
    assert participants_from_audit(record, side="opposer") == {"c"}


def test_negative_claim_still_uses_positive_emojis_for_support():
    record = _audit_record(
        [
            {"emoji": "👍", "senders": ["a"]},
            {"emoji": "👎", "senders": ["b"]},
        ]
    )
    assert participants_from_audit(record, side="supporter") == {"author", "a"}
    assert participants_from_audit(record, side="opposer") == {"b"}


def test_opposing_message_senders_count_as_opposers():
    record = _audit_record([], opposing=["dissenter"])
    assert participants_from_audit(record, side="opposer") == {"dissenter"}


def test_compute_support_splits_supporters_and_opposers():
    thread = {
        "messages": [
            {
                "sender": "author",
                "content": "support",
                "reactions": [
                    {"emoji": "👍", "senders": ["a"]},
                    {"emoji": "👎", "senders": ["b"]},
                ],
            },
            {"sender": "dissenter", "content": "oppose", "reactions": []},
        ],
        "message_ids": [1, 2],
    }
    line_meta = [
        {
            "sender": "author",
            "month": "2024-01",
            "message_index": 0,
            "reactions": thread["messages"][0]["reactions"],
        },
        {
            "sender": "dissenter",
            "month": "2024-01",
            "message_index": 1,
            "reactions": [],
        },
    ]
    result = compute_support(thread, line_meta, [0], opposing_local_message_ids=[1])
    assert set(result["all_supporters"]) == {"author", "a"}
    assert set(result["all_opposers"]) == {"dissenter", "b"}


def test_supporter_count_ignores_stale_all_supporters_field():
    record = _audit_record([{"emoji": "😢", "senders": ["sympathizer"]}])
    assert supporter_count_for_claims(["c1"], {"c1": record}) == 1
    assert engagement_for_claim("c1", {"c1": record}) == {
        "supporter_count": 1,
        "opposer_count": 1,
    }
