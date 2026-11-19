"""The claim records (contract C6): one record per claim, joined on read from the claims, the
entities, the private audit and the manual contradiction pairs. Synthetic data only."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest

from step_6_plan import claim_records
from step_6_plan.claim_records import (
    RECORD_KEYS,
    ClaimSources,
    add_manual_contradiction,
    allocate_short_id,
    load_claim_records,
    people,
    short_ids,
)
from tests.claim_fixtures import (
    claim_record,
    claim_records as make_records,
    clear_claim_inputs,
    write_claim_inputs,
)
from utils.json_io import write_json_file
from utils.paths import CLAIMS, ENTITIES


def _sha(text: str) -> str:
    return claim_records.SHORT_ID_PREFIX + hashlib.sha1(text.encode()).hexdigest()[:8]


def _claim(claim_id: str, **extra: Any) -> dict[str, Any]:
    return {
        "claim_id": claim_id,
        "thread_id": f"t-SECRET-{claim_id}",
        "claim_text": f"Claim {claim_id}.",
        "entities": ["free text name"],
        "stance": "factual",
        "first_date": "2024-01-01",
        "last_date": "2024-02-03",
        **extra,
    }


def _entity(entity_id: str, name: str | None, *claim_ids: str) -> dict[str, Any]:
    entity = {"entity_id": entity_id, "members": [{"name": "x", "claim_ids": list(claim_ids)}]}
    if name is not None:
        entity["canonical_name"] = name
    return entity


def _events(*pairs: tuple[str, str]) -> list[dict[str, str]]:
    return [{"person": p, "date": d, "via": "message", "message_id": "m"} for p, d in pairs]


def _write(
    root: Path,
    claims: Sequence[dict[str, Any]],
    entities: Sequence[dict[str, Any]] = (),
    audit: Sequence[dict[str, Any]] | None = None,
    deleted: Sequence[str] = (),
) -> ClaimSources:
    """Raw input files under ``root``; by default every claim has an empty audit record."""

    sources = ClaimSources(
        root / "claims.json", root / "entities.json", root / "audit.json", root / "pairs.json"
    )
    write_json_file({"claims": list(claims), "metadata": {}}, sources.claims)
    write_json_file({"entities": list(entities), "deleted_claims": list(deleted)}, sources.entities)
    if audit is None:
        audit = [{"claim_id": c["claim_id"], "supporters": [], "opposers": []} for c in claims]
    write_json_file({"audit": list(audit)}, sources.audit)
    return sources


@pytest.fixture
def default_place() -> Iterator[None]:
    """Tests that write the default input files leave the test data folder clean."""

    clear_claim_inputs()
    yield
    clear_claim_inputs()


def test_a_record_has_exactly_the_contract_keys_in_file_order(tmp_path: Path) -> None:
    claims = [_claim("b", participant_count=9), _claim("a", stance="negative", counters=[])]
    entities = [_entity("e2", "Two", "b"), _entity("e1", "One", "b"), _entity("e3", None, "b")]
    audit = [{"claim_id": "b", "supporters": [], "opposers": [], "participants": ["p1", "p2"]}]
    (first, second) = load_claim_records(_write(tmp_path, claims, entities, audit))
    assert all(tuple(record) == RECORD_KEYS for record in (first, second))
    assert "entities" not in first
    assert first == {
        "short_id": _sha("b"),
        "claim_id": "b",
        "thread_id": "t-SECRET-b",
        "claim_text": "Claim b.",
        "stance": "factual",
        "first_date": "2024-01-01",
        "last_date": "2024-02-03",
        "participant_count": 2,  # from the audit, not the claim
        "entity_ids": ["e1", "e2", "e3"],
        "entity_names": ["One", "Two", "e3"],  # no canonical name: the id
        "supporter_count": 0,
        "opposer_count": 0,
        "by_month": {},
        "contradicts": [],
    }
    assert (second["short_id"], second["stance"]) == (_sha("a"), "negative")
    assert second["entity_ids"] == []
    assert second["participant_count"] is None  # no audit record for "a"


def test_the_short_id_is_stable_and_a_collision_takes_a_suffix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert allocate_short_id("t-SECRET-1-abc", ()) == _sha("t-SECRET-1-abc")
    assert allocate_short_id("x", {_sha("x")}) == _sha("x#1")
    assert allocate_short_id("x", {_sha("x"), _sha("x#1")}) == _sha("x#2")
    monkeypatch.setattr(claim_records, "SHORT_ID_HASH_CHARS", 1)
    first_char = {f"c{k}": hashlib.sha1(f"c{k}".encode()).hexdigest()[0] for k in range(40)}
    a, b = next(
        (x, y) for x in first_char for y in first_char if x < y and first_char[x] == first_char[y]
    )
    ids = short_ids([b, a])
    assert ids[b] == claim_records.SHORT_ID_PREFIX + first_char[b]  # earlier in the file: plain
    assert ids[a] == allocate_short_id(a, {ids[b]}) != ids[b]
    loaded = load_claim_records(_write(tmp_path, [_claim(b, counters=[a]), _claim(a)]))
    assert [(r["short_id"], r["contradicts"]) for r in loaded] == [
        (ids[b], [ids[a]]),
        (ids[a], [ids[b]]),
    ]


def test_deleted_claims_are_dropped_with_their_links(tmp_path: Path) -> None:
    claims = [_claim("a", counters=["b"]), _claim("b", counters=["a"]), _claim("c")]
    sources = _write(tmp_path, claims, [_entity("e1", "One", "a", "b")], deleted=["b"])
    write_json_file({"pairs": [[_sha("b"), _sha("c")]], "metadata": {}}, sources.contradictions)
    loaded = load_claim_records(sources)
    assert [(r["claim_id"], r["entity_ids"], r["contradicts"]) for r in loaded] == [
        ("a", ["e1"], []),
        ("c", [], []),
    ]


def test_the_edited_files_win_and_entities_follow_a_hand_edit(default_place: None) -> None:
    records = make_records(4)
    write_claim_inputs(records)
    assert load_claim_records() == records
    entities = json.loads(ENTITIES.original.read_text(encoding="utf-8"))
    entities["entities"][0]["canonical_name"] = "Renamed by hand"
    entities["entities"][0]["members"][0]["claim_ids"].append(records[0]["claim_id"])
    entities["claim_entities"] = {}  # stale: never read
    write_json_file(entities, ENTITIES.edited)
    claims = json.loads(CLAIMS.original.read_text(encoding="utf-8"))
    claims["claims"][2]["claim_text"] = "Scrubbed on the Privacy page."
    write_json_file(claims, CLAIMS.edited)
    edited_entity = entities["entities"][0]["entity_id"]
    loaded = load_claim_records()
    assert loaded[0]["entity_ids"] == [edited_entity]
    assert loaded[0]["entity_names"] == ["Renamed by hand"]
    assert loaded[1]["entity_names"] == ["Renamed by hand"]
    assert loaded[2]["claim_text"] == "Scrubbed on the Privacy page."


def test_people_are_distinct_and_on_the_side_of_their_latest_event() -> None:
    # Overlapping people over three months, some of them on both sides.
    audit = {
        "supporters": _events(
            ("p1", "2024-01-05"),
            ("p2", "2024-01-05"),
            ("p3", "2024-02-01"),
            ("p1", "2024-03-01"),  # p1 again: counted once
            ("p4", "2024-03-02"),  # p4 changed their mind: supporter
            ("p5", "2024-02-01"),
        ),
        "opposers": _events(("p4", "2024-02-03"), ("p5", "2024-02-01")),  # p5 same day: opposer
    }
    out = people(audit)
    assert out["supporter_count"] == 4  # p1 p2 p3 p4
    assert out["opposer_count"] == 1  # p5
    assert out["by_month"] == {
        "2024-01": {"supporters": 2, "opposers": 0},
        "2024-02": {"supporters": 1, "opposers": 2},
        "2024-03": {"supporters": 2, "opposers": 0},
    }


def test_a_later_dispute_moves_a_supporter_to_the_opposers() -> None:
    audit = {
        "supporters": _events(("p1", "2024-01-01"), ("p2", "2024-04-02")),
        "opposers": _events(("p1", "2024-04-01"), ("p2", "2024-04-01"), ("p3", "")),
    }
    out = people(audit)
    assert (out["supporter_count"], out["opposer_count"]) == (1, 2)  # p2 / p1, p3 (no date)
    assert out["by_month"] == {
        "2024-01": {"supporters": 1, "opposers": 0},
        "2024-04": {"supporters": 1, "opposers": 1},
    }
    assert people({}) == {"supporter_count": 0, "opposer_count": 0, "by_month": {}}


def test_the_counts_come_from_the_audit_not_from_step_3(tmp_path: Path) -> None:
    claims = [_claim("a", supporter_count=2, opposer_count=1)]  # step 3's own rule
    audit = [
        {
            "claim_id": "a",
            "supporters": _events(("p1", "2024-01-01"), ("p2", "2024-01-01")),
            "opposers": _events(("p2", "2024-01-01")),
            "participants": ["p1", "p2", "p3", "p3"],
        }
    ]
    (record,) = load_claim_records(_write(tmp_path, claims, audit=audit))
    assert (record["supporter_count"], record["opposer_count"]) == (1, 1)
    assert record["by_month"] == {"2024-01": {"supporters": 1, "opposers": 1}}
    assert record["participant_count"] == 3


def test_counters_are_made_mutual_and_never_point_at_a_missing_record(tmp_path: Path) -> None:
    claims = [_claim("a", counters=["b"]), _claim("b"), _claim("c", counters=["a", "gone", "c"])]
    loaded = load_claim_records(_write(tmp_path, claims))
    assert {r["claim_id"]: r["contradicts"] for r in loaded} == {
        "a": sorted([_sha("b"), _sha("c")]),
        "b": [_sha("a")],
        "c": [_sha("a")],
    }


def test_manual_pairs_are_added_once_and_join_the_counters(tmp_path: Path) -> None:
    sources = _write(tmp_path, [_claim("a", counters=["b"]), _claim("b"), _claim("c")])
    assert add_manual_contradiction(_sha("c"), _sha("a"), sources.contradictions) is True
    assert add_manual_contradiction(_sha("a"), _sha("c"), sources.contradictions) is False
    assert add_manual_contradiction(_sha("b"), "g-notthere", sources.contradictions) is True
    payload = json.loads(sources.contradictions.read_text(encoding="utf-8"))
    expected = [sorted([_sha("a"), _sha("c")]), sorted([_sha("b"), "g-notthere"])]
    assert payload["pairs"] == sorted(expected)
    assert payload["metadata"]["pair_count"] == 2
    loaded = load_claim_records(sources)
    assert {r["claim_id"]: r["contradicts"] for r in loaded} == {
        "a": sorted([_sha("b"), _sha("c")]),
        "b": [_sha("a")],  # the pair with a short id no record has is ignored
        "c": [_sha("a")],
    }
    with pytest.raises(ValueError, match="itself"):
        add_manual_contradiction(_sha("a"), _sha("a"), sources.contradictions)
    write_json_file({"pairs": [["only-one"]]}, sources.contradictions)
    with pytest.raises(ValueError, match="manual contradictions"):
        load_claim_records(sources)


def test_a_missing_audit_is_an_error(tmp_path: Path) -> None:
    sources = _write(tmp_path, [_claim("a")])
    sources.audit.unlink()
    with pytest.raises(FileNotFoundError, match="private audit"):
        load_claim_records(sources)


def test_the_fixture_writer_round_trips(tmp_path: Path, default_place: None) -> None:
    records = [
        *make_records(12),
        claim_record(20, participant_count=None),
        claim_record(21, supporter_count=5, by_month={  # 3 dated people + 2 without a date
            "2024-01": {"supporters": 1, "opposers": 0},
            "2024-02": {"supporters": 2, "opposers": 1},
        }),
        claim_record(22, supporter_count=2, opposer_count=1, by_month={  # people in two months
            "2024-01": {"supporters": 2, "opposers": 1},
            "2024-03": {"supporters": 2, "opposers": 1},
        }),
    ]
    assert records[0]["contradicts"] == [records[1]["short_id"]]
    sources = write_claim_inputs(records, tmp_path)
    assert load_claim_records(sources) == records
    assert write_claim_inputs(records) == ClaimSources()
    assert load_claim_records() == records
    with pytest.raises(ValueError, match="fewer than"):
        write_claim_inputs([claim_record(1, supporter_count=1, by_month={
            "2024-01": {"supporters": 2, "opposers": 0}})], tmp_path)
    with pytest.raises(ValueError, match="record 0 short_id"):
        write_claim_inputs([claim_record(1, short_id="g-00000000")], tmp_path)
    with pytest.raises(TypeError, match="entities"):
        claim_record(1, entities=["e0001"])
