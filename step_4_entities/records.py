"""Member / entity records shared by the step, its incremental update and the reviewer store.

A *member* is one raw entity string with the claims it stands for; an *entity* is a canonical
group of members. Both shapes are what ``entities.json`` stores and the reviewer edits.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Collection, Iterable, Mapping
from typing import Any

from step_3_extract.scrub import find_emails, find_phones
from utils.config import ENTITIES_CONFIG

CONTACT_KINDS = ("email", "phone", "website")
_ID_PREFIX = ENTITIES_CONFIG["entity_id_prefix"]
_ID_DIGITS = ENTITIES_CONFIG["entity_id_digits"]
_ID_RE = re.compile(rf"{re.escape(_ID_PREFIX)}(\d+)")
_URL_RE = re.compile(
    r"https?://[^\s)\]<>\"']+"
    r"|\b(?:[a-zA-Z0-9-]+\.)+(?:com|org|net|gov|edu|io|info|biz|travel|co\.il|co|il)\b"
)

Contacts = dict[str, list[str]]
MemberClaims = Callable[[dict[str, Any]], Iterable[str]]


def empty_contacts() -> Contacts:
    return {kind: [] for kind in CONTACT_KINDS}


def union_contacts(members: Iterable[Mapping[str, Any]], key: str = "contacts") -> Contacts:
    out: dict[str, set[str]] = {kind: set() for kind in CONTACT_KINDS}
    for member in members:
        for kind, values in (member.get(key) or {}).items():
            out.setdefault(kind, set()).update(values)
    return {kind: sorted(values) for kind, values in out.items()}


def claim_contacts(
    claim: Mapping[str, Any], original: Mapping[str, Any] | None = None
) -> tuple[list[str], list[str], list[str]]:
    """Emails, phones, websites from a claim and its pipeline-original redaction trail."""

    found: dict[str, set[str]] = {kind: set() for kind in CONTACT_KINDS}
    for src in (claim,) if original is None else (claim, original):
        text = src.get("claim_text", "")
        for red in src.get("_redactions") or []:
            if red.get("type") in ("email", "phone"):
                found[red["type"]].add(red["value"])
        found["email"].update(find_emails(text))
        found["phone"].update(find_phones(text))
        found["website"].update(m.group(0).rstrip(".") for m in _URL_RE.finditer(text or ""))
    return sorted(found["email"]), sorted(found["phone"]), sorted(found["website"])


def member_record(
    name: str,
    claim_ids: Iterable[str],
    claims_by_id: Mapping[str, Mapping[str, Any]],
    original_by_id: Mapping[str, Mapping[str, Any]] | None = None,
    *,
    excluded_claim_ids: Collection[str] = (),
) -> dict[str, Any]:
    """One member: its claims, a sample, and the contacts seen in them.

    A contact is *confident* when it comes from a claim whose entity list is exactly this one
    name (it unambiguously belongs to it), otherwise *uncertain*.
    """

    ids = sorted(set(claim_ids) - set(excluded_claim_ids))
    confident: dict[str, set[str]] = {kind: set() for kind in CONTACT_KINDS}
    uncertain: dict[str, set[str]] = {kind: set() for kind in CONTACT_KINDS}
    for claim_id in ids:
        claim = claims_by_id.get(claim_id)
        if claim is None:
            continue
        original = (original_by_id or {}).get(claim_id)
        listed = {n for n in claim.get("entities") or [] if isinstance(n, str) and n.strip()}
        bucket = confident if listed == {name} else uncertain
        for kind, values in zip(CONTACT_KINDS, claim_contacts(claim, original)):
            bucket[kind].update(values)
    record: dict[str, Any] = {
        "name": name,
        "claim_ids": ids,
        "count": len(ids),
        "sample_claim_ids": ids[: ENTITIES_CONFIG["sample_claims_per_member"]],
        "contacts": {kind: sorted(confident[kind]) for kind in CONTACT_KINDS},
        "contacts_uncertain": {
            kind: sorted(uncertain[kind] - confident[kind]) for kind in CONTACT_KINDS
        },
    }
    if excluded_claim_ids:
        record["excluded_claim_ids"] = sorted(excluded_claim_ids)
    return record


def refresh_entity(entity: dict[str, Any]) -> None:
    """Refresh aliases / contacts / canonical name from the member list, keeping manual work.

    Manual contacts (``contacts_manual``) are kept, rejected uncertain contacts
    (``contacts_rejected``) never come back, and a manual canonical name
    (``canonical_manual``) is never replaced.
    """

    members = entity.get("members") or []
    members.sort(key=lambda m: m.get("count") or 0, reverse=True)
    entity["aliases"] = [m["name"] for m in members]
    if not entity.get("contacts_manual"):
        entity["contacts"] = union_contacts(members)
    uncertain = union_contacts(members, "contacts_uncertain")
    confident = entity.get("contacts") or {}
    rejected = entity.get("contacts_rejected") or {}
    entity["contacts_uncertain"] = {
        kind: sorted(
            set(uncertain.get(kind, []))
            - set(confident.get(kind, []))
            - set(rejected.get(kind, []))
        )
        for kind in CONTACT_KINDS
    }
    if (
        members
        and not entity.get("canonical_manual")
        and entity.get("canonical_name") not in entity["aliases"]
    ):
        entity["canonical_name"] = members[0]["name"]


def new_entity(entity_id: str, members: list[dict[str, Any]], **fields: Any) -> dict[str, Any]:
    """A fresh ``suggested`` entity around ``members`` (canonical = most-claimed member)."""

    entity: dict[str, Any] = {
        "entity_id": entity_id,
        "canonical_name": "",
        "status": "suggested",
        "aliases": [],
        "members": list(members),
        "contacts": empty_contacts(),
        "contacts_uncertain": empty_contacts(),
        "merge_signals": [],
        "score": 1.0,
    }
    entity.update(fields)
    refresh_entity(entity)
    return entity


def allocate_entity_id(payload: dict[str, Any]) -> str:
    """Next never-used entity id; the high-water mark lives in ``metadata``."""

    meta = payload.setdefault("metadata", {})
    numbers = [
        int(m.group(1))
        for e in payload.get("entities") or []
        if (m := _ID_RE.fullmatch(str(e.get("entity_id"))))
    ]
    number = max([int(meta.get("last_entity_number", -1)), *numbers]) + 1
    meta["last_entity_number"] = number
    return f"{_ID_PREFIX}{number:0{_ID_DIGITS}d}"


def explicit_member_claims(member: Mapping[str, Any]) -> list[str]:
    """A member's active claims: its ``claim_ids`` minus its excluded ones."""

    excluded = set(member.get("excluded_claim_ids") or [])
    return [c for c in member.get("claim_ids") or [] if c not in excluded]


def claim_entity_index(
    entities: Iterable[Mapping[str, Any]],
    *,
    deleted_claims: Collection[str] = (),
    member_claims: MemberClaims = explicit_member_claims,
) -> dict[str, list[str]]:
    """``claim_id -> [entity_id]``: the canonical entities each claim mentions (step 5)."""

    deleted = set(deleted_claims)
    index: dict[str, list[str]] = {}
    for entity in entities:
        entity_id = entity["entity_id"]
        for member in entity.get("members") or []:
            for claim_id in member_claims(member):
                if claim_id in deleted:
                    continue
                ids = index.setdefault(claim_id, [])
                if entity_id not in ids:
                    ids.append(entity_id)
    return {claim_id: sorted(ids) for claim_id, ids in sorted(index.items())}
