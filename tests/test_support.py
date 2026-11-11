"""Support counts: emoji sentiment is independent of claim stance; evidence per claim."""

from utils.support import collect_evidence, person_key


def _messages() -> dict[str, dict]:
    return {
        "W1": {
            "sender": "author",
            "datetime": "2024-01-05T10:00:00",
            "reactions": [
                {"emoji": "👍", "senders": ["a", "author"]},
                {"emoji": "👎", "senders": ["b"]},
                {"emoji": "🙂", "senders": ["n"]},
            ],
        },
        "W2": {
            "sender": "dissenter",
            "datetime": "2024-03-02T08:00:00",
            "reactions": [
                {"emoji": "👍", "senders": ["agrees-with-dissent"]},
                {"emoji": "😢", "senders": ["sympathizer"]},
            ],
        },
        "W3": {"sender": "a", "datetime": "2024-02-01T00:00:00", "reactions": []},
    }


def _people(events: list[dict]) -> set[str]:
    return {e["person"] for e in events}


def test_positive_reaction_on_an_opposing_message_backs_the_opposition() -> None:
    """The v1 bug: a 👍 on an opposing message was counted as SUPPORT for the claim."""
    evidence = collect_evidence(_messages(), ["W1"], ["W2"])
    assert "agrees-with-dissent" not in _people(evidence.supporters)
    assert "agrees-with-dissent" in _people(evidence.opposers)
    # A sad face under a dissenting message is sympathy: it counts for neither side.
    assert "sympathizer" not in _people(evidence.supporters) | _people(evidence.opposers)


def test_supporters_opposers_and_distinct_counts() -> None:
    evidence = collect_evidence(_messages(), ["W1", "W3"], ["W2"])
    assert _people(evidence.supporters) == {"author", "a"}
    assert _people(evidence.opposers) == {"b", "dissenter", "agrees-with-dissent"}
    # "a" wrote W3 and reacted to W1: two events, one person.
    assert len([e for e in evidence.supporters if e["person"] == "a"]) == 2
    assert evidence.supporter_count == 2
    assert evidence.opposer_count == 3


def test_event_shape_and_dates() -> None:
    evidence = collect_evidence(_messages(), ["W1", "W3"], ["W2"])
    assert evidence.first_date == "2024-01-05"
    assert evidence.last_date == "2024-03-02"
    assert {"person": "a", "date": "2024-01-05", "via": "reaction", "message_id": "W1"} in (
        evidence.supporters
    )
    assert {"person": "a", "date": "2024-02-01", "via": "message", "message_id": "W3"} in (
        evidence.supporters
    )


def test_reaction_summary_by_side() -> None:
    summary = collect_evidence(_messages(), ["W1"], ["W2"]).reaction_summary
    assert summary[0] == {"emoji": "👍", "side": "supporting", "sentiment": "positive", "count": 2}
    assert {"emoji": "😢", "side": "opposing", "sentiment": "negative", "count": 1} in summary
    sides = [s["side"] for s in summary]
    assert sides == sorted(sides, key=lambda side: side != "supporting")


def test_person_key_accepts_export_dicts() -> None:
    assert person_key({"user_name": "u", "phone": "p"}) == "u"
    assert person_key({"user_name": "", "phone": "p"}) == "p"
    assert person_key("u") == "u"
    assert person_key(None) == ""
