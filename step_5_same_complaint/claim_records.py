"""The claim records steps 6 to 10 and the review site read (contract C6): one record per claim,
joined on every call from the files people edit, never stored.

Inputs:
- the claims (C3; ``claims_edited.json`` when present, the copy the Privacy page edits);
- the entities (``entities_edited.json`` when present). Claims listed in its
  ``deleted_claims`` are dropped. A claim's canonical entities are recomputed from the entity
  members with ``step_4_entities.records.claim_entity_index`` (never the stored
  ``claim_entities``), so a hand edit on the Entities page shows at once;
- step 3's private audit (who backed or disputed each claim); only counts leave it;
- the contradictions the owner accepted by hand (``MANUAL_CONTRADICTIONS_PATH``, optional).

There is no output file on purpose: a derived copy could keep a detail the owner already removed
on the Privacy page, or entity ids the owner already merged.

A record holds exactly ``RECORD_KEYS``:
- ``short_id``: ``claim_records.short_id_prefix`` + the first ``short_id_hash_chars`` hex digits
  of ``sha1(claim_id)``; on a collision the claim id is hashed again with a ``#<n>`` suffix, in
  the order of the claims file. FROM STEP 6 ON A CLAIM IS NAMED BY ITS ``short_id`` ONLY.
- ``claim_id`` and ``thread_id`` hold WhatsApp message ids. They stay in the record for the
  review tool's own lookups and must NEVER reach a prompt, a page or the site.
- ``entity_ids`` (canonical ids, sorted) and ``entity_names`` (``canonical_name`` per id, the
  id when it has none). There is deliberately no key ``entities``: on a C3 claim it holds
  free-text names, not entity ids.
- ``supporter_count``, ``opposer_count`` and ``by_month`` (``{"YYYY-MM": {supporters,
  opposers}}``) follow the owner's rule: a person counts once, on the side of
  their latest event (they changed their mind); on the same date the opposing side wins, so a
  dispute is never hidden. ``by_month`` applies the rule inside each month of the event dates.
- ``participant_count``: the distinct people who wrote in the claim's conversation (the audit's
  ``participants``), ``None`` when the audit record predates that field.
- ``contradicts``: the short ids of the records this one disputes: step 3's ``counters`` made
  mutual, plus the manual pairs, sorted, only between records that exist, never itself.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from step_4_entities.records import claim_entity_index
from utils.config import section
from utils.file_lock import file_lock
from utils.json_io import read_json_file, read_json_if_present, write_json_file
from utils.paths import (
    AUDIT_PATH,
    MANUAL_CONTRADICTIONS_PATH,
    SAME_COMPLAINT_PATH,
    SAME_COMPLAINT_VETOES_PATH,
    resolve_claims_path,
    resolve_entities_path,
)
from utils.timestamps import utc_now

logger = logging.getLogger(__name__)

CLAIM_RECORDS_CONFIG = section("claim_records")
SHORT_ID_PREFIX = str(CLAIM_RECORDS_CONFIG["short_id_prefix"])
SHORT_ID_HASH_CHARS = int(CLAIM_RECORDS_CONFIG["short_id_hash_chars"])
RECORD_KEYS = (
    "short_id", "claim_id", "thread_id", "claim_text", "stance", "first_date", "last_date",
    "participant_count", "entity_ids", "entity_names", "supporter_count", "opposer_count",
    "by_month", "contradicts",
)  # fmt: skip
_SIDES = ("supporters", "opposers")

Record = dict[str, Any]


@dataclass(frozen=True)
class ClaimSources:
    """The files one load reads. ``claims`` / ``entities`` left ``None`` resolve to the edited
    file when present, else the original, at load time.

    ``same_complaint`` and ``same_complaint_vetoes`` are not read by the loader: they are the
    same-complaint check's answers and the owner's "keep withheld" marks, which decide with the
    records what is withheld from the wiki now
    (``step_5_same_complaint.release.withheld_ids``)."""

    claims: Path | None = None
    entities: Path | None = None
    audit: Path = AUDIT_PATH
    contradictions: Path = MANUAL_CONTRADICTIONS_PATH
    same_complaint: Path = SAME_COMPLAINT_PATH
    same_complaint_vetoes: Path = SAME_COMPLAINT_VETOES_PATH

    def files(self) -> dict[str, Path]:
        """The files a load would read now, by role (``claims``, ``entities``, ``audit``,
        ``contradictions``), only those that exist: for a step's "an input is newer than my
        output" check and its metadata."""

        files = {
            "claims": self.claims or resolve_claims_path(),
            "entities": self.entities or resolve_entities_path(),
            "audit": self.audit,
            "contradictions": self.contradictions,
        }
        return {role: path for role, path in files.items() if path.is_file()}


def allocate_short_id(claim_id: str, used: Collection[str]) -> str:
    """The short id of ``claim_id`` that is not in ``used`` (see the module docstring)."""

    key, attempt = claim_id, 0
    while True:
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()
        short_id = SHORT_ID_PREFIX + digest[:SHORT_ID_HASH_CHARS]
        if short_id not in used:
            return short_id
        attempt += 1
        key = f"{claim_id}#{attempt}"


def short_ids(claim_ids: Iterable[str]) -> dict[str, str]:
    """``claim_id -> short_id`` for claims in file order (a collision suffixes the later one)."""

    out: dict[str, str] = {}
    used: set[str] = set()
    for claim_id in claim_ids:
        out[claim_id] = allocate_short_id(claim_id, used)
        used.add(out[claim_id])
    return out


def _latest_sides(events: Iterable[tuple[str, str, str]]) -> dict[str, bool]:
    """``person -> True when they count as an opposer``: each person on the side of their
    latest ``(person, date, side)`` event, the opposing side on the same date."""

    latest: dict[str, tuple[str, bool]] = {}
    for person, date, side in events:
        key = (date, side == "opposers")
        if person not in latest or key > latest[person]:
            latest[person] = key
    return {person: is_opposer for person, (_, is_opposer) in latest.items()}


def _sides(events: Iterable[tuple[str, str, str]]) -> dict[str, int]:
    """``{supporters, opposers}`` distinct-people counts from ``(person, date, side)`` events."""

    latest = _latest_sides(events)
    opposers = sum(1 for is_opposer in latest.values() if is_opposer)
    return {"supporters": len(latest) - opposers, "opposers": opposers}


def _events(audit_record: Mapping[str, Any]) -> list[tuple[str, str, str]]:
    """The ``(person, date, side)`` events of one claim's audit record."""

    return [
        (str(event["person"]), str(event.get("date") or ""), side)
        for side in _SIDES
        for event in audit_record.get(side) or []
        if event.get("person")
    ]


def people(audit_record: Mapping[str, Any]) -> dict[str, Any]:
    """``supporter_count``, ``opposer_count`` and ``by_month`` of one claim's audit record."""

    events = _events(audit_record)
    months: dict[str, list[tuple[str, str, str]]] = {}
    for event in events:
        if event[1]:
            months.setdefault(event[1][:7], []).append(event)
    totals = _sides(events)
    return {
        "supporter_count": totals["supporters"],
        "opposer_count": totals["opposers"],
        "by_month": {month: _sides(months[month]) for month in sorted(months)},
    }


def claim_backers(records: Iterable[Record], audit: Path = AUDIT_PATH
                  ) -> dict[str, frozenset[str]]:
    """``short_id -> the people its supporter_count counts`` (the same rule, read from the
    private audit again). These are IDENTITIES: keep them in memory, never write or show them;
    only counts of them may leave. A claim without an audit record has no backer."""

    if not audit.is_file():
        raise FileNotFoundError(f"the backers of the claims need the private audit {audit}")
    entries = {r["claim_id"]: r for r in read_json_file(audit).get("audit") or []}
    backers = {}
    for record in records:
        sides = _latest_sides(_events(entries.get(record["claim_id"]) or {}))
        backers[record["short_id"]] = frozenset(p for p, is_opposer in sides.items() if not is_opposer)
    return backers


def participant_count(audit_record: Mapping[str, Any]) -> int | None:
    """Distinct people who wrote in the claim's conversation, ``None`` when unknown."""

    participants = audit_record.get("participants")
    return None if participants is None else len(set(participants))


def counter_links(claims: Mapping[str, Mapping[str, Any]]) -> dict[str, set[str]]:
    """``claim_id -> claim ids it disputes``: step 3's ``counters``, mutual even when only one
    side lists the other; claims not in ``claims`` and self-links are ignored."""

    links: dict[str, set[str]] = {claim_id: set() for claim_id in claims}
    for claim_id, claim in claims.items():
        for other in claim.get("counters") or []:
            if other in claims and other != claim_id:
                links[claim_id].add(other)
                links[other].add(claim_id)
    return links


def load_manual_pairs(path: Path = MANUAL_CONTRADICTIONS_PATH) -> list[tuple[str, str]]:
    """The owner's contradiction pairs (short ids), ``[]`` when the file does not exist yet."""

    payload = read_json_if_present(path)
    if payload is None:
        return []
    pairs = payload.get("pairs") if isinstance(payload, dict) else None
    if not isinstance(pairs, list) or not all(
        isinstance(p, list) and len(p) == 2 and all(isinstance(s, str) for s in p) for p in pairs
    ):
        raise ValueError(
            f"{path} is not the manual contradictions file: expected "
            '{"pairs": [["<short_id>", "<short_id>"], ...], "metadata": {...}}'
        )
    return [(a, b) for a, b in pairs]


def add_manual_contradiction(a: str, b: str, path: Path = MANUAL_CONTRADICTIONS_PATH) -> bool:
    """Record that claims ``a`` and ``b`` (short ids) contradict each other; ``False`` when the
    pair was already there. Takes the file's lock (``utils.file_lock``), so do not call it while
    holding that lock; the write is atomic."""

    if a == b:
        raise ValueError(f"a claim cannot contradict itself: {a}")
    pair = sorted((a, b))
    with file_lock(path):
        pairs = [sorted(p) for p in load_manual_pairs(path)]
        if pair in pairs:
            return False
        pairs = sorted([*pairs, pair])
        metadata = {"pair_count": len(pairs), "updated_at": utc_now()}
        write_json_file({"pairs": pairs, "metadata": metadata}, path)
    logger.info(f"manual contradiction {pair[0]} <-> {pair[1]} recorded in {path}")
    return True


def load_claim_records(sources: ClaimSources = ClaimSources()) -> list[Record]:
    """One new record per claim, in the order of the claims file (see the module docstring).

    A missing entities file means no claim has an entity, a missing manual pairs file means no
    manual pair; a missing audit is an error (the people counts come from it).
    """

    entities_path = sources.entities or resolve_entities_path()
    entities = read_json_file(entities_path) if entities_path.is_file() else {}
    deleted = set(entities.get("deleted_claims") or [])
    payload = read_json_file(sources.claims or resolve_claims_path())
    claims = {c["claim_id"]: c for c in payload.get("claims") or [] if c["claim_id"] not in deleted}
    if not sources.audit.is_file():
        raise FileNotFoundError(
            f"the claim records need the private audit {sources.audit} (who backed / disputed "
            f"each claim); run step 3 first"
        )
    audit = {r["claim_id"]: r for r in read_json_file(sources.audit).get("audit") or []}
    entity_list = entities.get("entities") or []
    entity_index = claim_entity_index(entity_list, deleted_claims=deleted)
    names = {e["entity_id"]: e.get("canonical_name") or e["entity_id"] for e in entity_list}
    short = short_ids(claims)
    links = counter_links(claims)
    contradicts = {short[c]: {short[o] for o in others} for c, others in links.items()}
    for a, b in load_manual_pairs(sources.contradictions):
        if a in contradicts and b in contradicts and a != b:
            contradicts[a].add(b)
            contradicts[b].add(a)
    records = []
    for claim_id, claim in claims.items():
        record = audit.get(claim_id) or {}
        counts = people(record)
        entity_ids = entity_index.get(claim_id, [])
        records.append(
            {
                "short_id": short[claim_id],
                "claim_id": claim_id,
                "thread_id": claim.get("thread_id"),
                "claim_text": claim.get("claim_text") or "",
                "stance": claim.get("stance") or "",
                "first_date": str(claim["first_date"]) if claim.get("first_date") else None,
                "last_date": str(claim["last_date"]) if claim.get("last_date") else None,
                "participant_count": participant_count(record),
                "entity_ids": list(entity_ids),
                "entity_names": [names[e] for e in entity_ids],
                "supporter_count": counts["supporter_count"],
                "opposer_count": counts["opposer_count"],
                "by_month": counts["by_month"],
                "contradicts": sorted(contradicts[short[claim_id]]),
            }
        )
    return records
