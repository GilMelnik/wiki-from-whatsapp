"""Pure helpers and type aliases shared by the entity store modules."""

from __future__ import annotations

from typing import Any, Literal

from step_4_entities.cluster import _union_contacts

SortKind = Literal["count", "size", "score"]
SortOrder = Literal["asc", "desc"]
Status = Literal["suggested", "accepted", "rejected"]


def _member_total(entity: dict[str, Any]) -> int:
    return sum(m.get("count") or 0 for m in entity.get("members") or [])


def _recompute_entity(entity: dict[str, Any]) -> None:
    """Refresh aliases/topics/canonical/contacts from the member list."""

    members = entity.get("members") or []
    members.sort(key=lambda m: m.get("count") or 0, reverse=True)
    entity["aliases"] = [m["name"] for m in members]
    if not entity.get("contacts_manual"):
        entity["contacts"] = _union_contacts(members)
    # Uncertain contacts (multi-entity claims) drop anything already promoted to
    # the confident set, so an accepted value never shows up twice.
    uncertain = _union_contacts(members, "contacts_uncertain")
    confident = entity.get("contacts") or {}
    entity["contacts_uncertain"] = {
        kind: sorted(set(uncertain.get(kind, [])) - set(confident.get(kind, [])))
        for kind in ("email", "phone", "website")
    }
    topics: set[str] = set()
    for member in members:
        topics.update(member.get("topics") or [])
    entity["topics"] = sorted(topics)
    if members and entity.get("canonical_name") not in entity["aliases"]:
        entity["canonical_name"] = members[0]["name"]


def _normalize_contacts(contacts: dict[str, Any]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {"email": [], "phone": [], "website": []}
    for kind in out:
        raw = contacts.get(kind) or []
        if not isinstance(raw, list):
            raise ValueError(f"contacts.{kind} must be a list")
        seen: set[str] = set()
        values: list[str] = []
        for item in raw:
            if not isinstance(item, str):
                continue
            value = item.strip()
            if value and value not in seen:
                seen.add(value)
                values.append(value)
        out[kind] = values
    return out


def _merge_highlights(
    segments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Drop overlaps; ``self`` beats ``other``, then longer span wins."""

    priority = {"self": 0, "other": 1}
    ranked = sorted(
        segments,
        key=lambda s: (
            s["start"],
            priority.get(s["kind"], 2),
            -(s["end"] - s["start"]),
        ),
    )
    out: list[dict[str, Any]] = []
    cursor = -1
    for seg in ranked:
        if seg["start"] < cursor:
            continue
        out.append(seg)
        cursor = seg["end"]
    out.sort(key=lambda s: s["start"])
    return out
