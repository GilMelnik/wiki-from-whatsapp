"""Synthetic claim records (contract C6) and the input files that load to exactly them.

``claim_record`` / ``claim_records`` build records for tests that pass records directly.
``write_claim_inputs`` writes claims, entities and audit files from which
``step_6_plan.claim_records.load_claim_records`` returns exactly the given records, for tests
that run a step. Nothing here comes from real data. Ids that would be private in real data
(claim, thread, person and message ids) contain ``SECRET``, so a test can assert that none of
them reaches a prompt.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from step_4_entities.records import claim_entity_index
from step_6_plan.claim_records import (
    RECORD_KEYS,
    ClaimSources,
    Record,
    allocate_short_id,
    load_claim_records,
)
from utils.json_io import write_json_file
from utils.paths import CLAIMS, ENTITIES

STANCES = ("positive", "negative", "neutral", "factual")


def _one_month(last_date: str | None, supporters: int, opposers: int) -> dict[str, dict[str, int]]:
    """Everybody backed or disputed the claim in the month of its last date."""

    if not last_date or not supporters + opposers:
        return {}
    return {last_date[:7]: {"supporters": supporters, "opposers": opposers}}


def claim_record(n: int, **fields: Any) -> Record:
    """Record number ``n`` with synthetic English text; ``fields`` override any C6 key.

    ``short_id`` follows ``claim_id`` and ``by_month`` follows the counts and ``last_date``
    unless given. Odd records mention one entity, negative ones have two supporters at least
    (so the withholding rule keeps them all), and nothing contradicts anything.
    """

    unknown = set(fields) - set(RECORD_KEYS)
    if unknown:
        raise TypeError(f"not a claim record key: {sorted(unknown)}; the keys are {RECORD_KEYS}")
    stance = STANCES[n % 4]
    first_date = f"2023-{n % 12 + 1:02d}-05"
    record: Record = {
        "claim_id": f"t-SECRET{n}-{n:08x}",
        "thread_id": f"t-SECRET{n}",
        "claim_text": f"Synthetic claim number {n} about topic {n % 4}.",
        "stance": stance,
        "first_date": first_date,
        "last_date": "2024-06-01" if n % 3 else first_date,
        "participant_count": n % 5 + 3,
        "entity_ids": [f"e{n % 4:04d}"] if n % 2 else [],
        "entity_names": [f"Entity{n % 4}"] if n % 2 else [],
        "supporter_count": max(n % 5, 2) if stance == "negative" else n % 5,
        "opposer_count": n % 2,
        "contradicts": [],
        **fields,
    }
    record.setdefault("short_id", allocate_short_id(record["claim_id"], ()))
    record.setdefault(
        "by_month",
        _one_month(record["last_date"], record["supporter_count"], record["opposer_count"]),
    )
    return {key: record[key] for key in RECORD_KEYS}


def claim_records(count: int = 12) -> list[Record]:
    """``claim_record(0)`` to ``claim_record(count - 1)``; the first two (a positive and a
    negative one) contradict each other."""

    records = [claim_record(n) for n in range(count)]
    if count >= 2:
        records[0]["contradicts"] = [records[1]["short_id"]]
        records[1]["contradicts"] = [records[0]["short_id"]]
    return records


def _events(
    side: str, total: int, by_month: Mapping[str, Mapping[str, int]]
) -> list[dict[str, Any]]:
    """Audit events of one side that count ``total`` people overall and ``by_month[m][side]``
    people in month ``m``: people are reused across months, dateless events add the rest."""

    slots = [month for month in sorted(by_month) for _ in range(by_month[month][side])]
    busiest = max((by_month[month][side] for month in by_month), default=0)
    if total < busiest:
        raise ValueError(f"{total} {side} overall cannot be fewer than {busiest} in one month")
    dated = min(total, len(slots))
    events = [(k % dated, f"{month}-01") for k, month in enumerate(slots)]
    events += [(k, "") for k in range(dated, total)]
    return [
        {"person": f"p-SECRET-{side}-{k}", "date": date, "via": "message", "message_id": "m-SECRET"}
        for k, date in events
    ]


def _entities(records: Sequence[Record]) -> list[dict[str, Any]]:
    names: dict[str, str] = {}
    members: dict[str, list[str]] = {}
    for record in records:
        if len(record["entity_ids"]) != len(record["entity_names"]):
            raise ValueError(f"{record['short_id']}: one entity name per entity id expected")
        for entity_id, name in zip(record["entity_ids"], record["entity_names"]):
            if names.setdefault(entity_id, name) != name:
                raise ValueError(f"entity {entity_id} is {names[entity_id]!r} and {name!r}")
            members.setdefault(entity_id, []).append(record["claim_id"])
    return [
        {
            "entity_id": e,
            "canonical_name": names[e],
            "members": [{"name": names[e], "claim_ids": ids}],
        }
        for e, ids in sorted(members.items())
    ]


def _first_difference(given: Sequence[Record], loaded: Sequence[Record]) -> str:
    if len(given) != len(loaded):
        return f"{len(given)} records given, {len(loaded)} loaded"
    for k, (one, other) in enumerate(zip(given, loaded)):
        for key in RECORD_KEYS:
            if one.get(key) != other.get(key):
                return f"record {k} {key}: given {one.get(key)!r}, loaded {other.get(key)!r}"
    return "key sets differ"


def clear_claim_inputs() -> None:
    """Remove every file ``load_claim_records()`` reads from the default places of the test
    data folder (original and edited claims and entities, audit, manual pairs), and the
    same-complaint answers and marks. A test that calls ``write_claim_inputs`` without ``root``
    calls this when it ends."""

    sources = ClaimSources()
    for path in (CLAIMS.original, CLAIMS.edited, ENTITIES.original, ENTITIES.edited,
                 sources.audit, sources.contradictions, sources.same_complaint,
                 sources.same_complaint_vetoes):
        path.unlink(missing_ok=True)


def write_claim_inputs(records: Sequence[Record], root: Path | None = None) -> ClaimSources:
    """Write the claims, entities and audit files ``records`` load from; returns their sources.

    With ``root``, the files go under it (``claims.json``, ``entities.json``, ``audit.json``;
    no manual pairs file) and the sources name them, with the same-complaint answers and marks
    under it too (``same_complaint.json``, ``same_complaint_vetoes.json``; not written). Without it, ``clear_claim_inputs`` runs
    first, the files go to the default places in the test data folder and
    ``load_claim_records()`` with no argument reads them (the test cleans up with
    ``clear_claim_inputs`` when it ends). Contradictions are
    written as step 3 ``counters``. Raises ``ValueError`` when the records cannot load back
    exactly (e.g. a short id that is not the claim id's, more people in one month than overall).
    """

    claim_of = {r["short_id"]: r["claim_id"] for r in records}
    claims = [
        {
            "claim_id": r["claim_id"],
            "thread_id": r["thread_id"],
            "claim_text": r["claim_text"],
            "entities": list(r["entity_names"]),
            "stance": r["stance"],
            "first_date": r["first_date"],
            "last_date": r["last_date"],
            "supporter_count": r["supporter_count"],
            "opposer_count": r["opposer_count"],
            "participant_count": r["participant_count"],
            "counters": [claim_of[s] for s in r["contradicts"] if s in claim_of],
        }
        for r in records
    ]
    audit = []
    for r in records:
        entry = {
            "claim_id": r["claim_id"],
            "supporters": _events("supporters", r["supporter_count"], r["by_month"]),
            "opposers": _events("opposers", r["opposer_count"], r["by_month"]),
        }
        if r["participant_count"] is not None:
            entry["participants"] = [f"p-SECRET-{k}" for k in range(r["participant_count"])]
        audit.append(entry)
    entities = _entities(records)
    if root is None:
        clear_claim_inputs()
        sources = ClaimSources()
        paths = (CLAIMS.original, ENTITIES.original, sources.audit)
    else:
        paths = (root / "claims.json", root / "entities.json", root / "audit.json")
        sources = ClaimSources(*paths, contradictions=root / "manual_contradictions.json",
                               same_complaint=root / "same_complaint.json",
                               same_complaint_vetoes=root / "same_complaint_vetoes.json")
    claims_path, entities_path, audit_path = paths
    write_json_file({"claims": claims, "metadata": {}}, claims_path)
    write_json_file(
        {"entities": entities, "claim_entities": claim_entity_index(entities)}, entities_path
    )
    write_json_file({"audit": audit}, audit_path)
    loaded = load_claim_records(sources)
    if loaded != list(records):
        raise ValueError(f"the records do not load back: {_first_difference(records, loaded)}")
    return sources
