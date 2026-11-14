"""Types, errors and pure helpers shared by the entity store modules."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from step_4_entities.records import CONTACT_KINDS

SortKind = Literal["review", "count", "size", "score", "id", "name"]
SortOrder = Literal["asc", "desc"]
StatusFilter = Literal["all", "suggested", "accepted", "ambiguous", "rejected"]
FlagFilter = Literal["all", "attached", "conflict", "uncertain", "multi"]
Status = Literal["suggested", "accepted"]
ContactAction = Literal["accept", "reject", "restore"]
ContactKind = Literal["email", "phone", "website"]


class EntityNotFound(Exception):
    """An entity, member, claim or claim group that is not (or no longer) there; ``args[0]``
    is a Hebrew sentence the UI shows as is."""


class ReviewConflict(Exception):
    """A change that does not fit the current state; ``args[0]`` is a Hebrew sentence."""


@dataclass(frozen=True)
class EntityView:
    """Which entities the owner is going through and in what order (list, queue, next)."""

    status: StatusFilter = "all"
    flag: FlagFilter = "all"
    query: str = ""
    sort: SortKind = "review"
    order: SortOrder = "desc"


def member_total(entity: dict[str, Any]) -> int:
    return sum(m.get("count") or 0 for m in entity.get("members") or [])


def attached_members(entity: dict[str, Any]) -> list[dict[str, Any]]:
    """Members an update attached automatically (``attached_by``): they need the owner's eye."""

    return [m for m in entity.get("members") or [] if m.get("attached_by")]


def contact_count(contacts: dict[str, Any] | None) -> int:
    return sum(len(values or []) for values in (contacts or {}).values())


def normalize_contacts(contacts: dict[str, Any]) -> dict[str, list[str]]:
    """Trimmed, de-duplicated values per kind (missing kinds are empty)."""

    out: dict[str, list[str]] = {kind: [] for kind in CONTACT_KINDS}
    for kind in out:
        values: list[str] = []
        for item in contacts.get(kind) or []:
            value = item.strip() if isinstance(item, str) else ""
            if value and value not in values:
                values.append(value)
        out[kind] = values
    return out


def merge_highlights(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop overlaps; ``self`` beats ``other``, then the longer span wins."""

    priority = {"self": 0, "other": 1}
    ranked = sorted(
        segments,
        key=lambda s: (s["start"], priority.get(s["kind"], 2), -(s["end"] - s["start"])),
    )
    out: list[dict[str, Any]] = []
    cursor = -1
    for seg in ranked:
        if seg["start"] < cursor:
            continue
        out.append(seg)
        cursor = seg["end"]
    return out
