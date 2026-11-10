"""Pure helpers and type aliases shared by the entity store modules."""

from __future__ import annotations

from typing import Any, Literal

SortKind = Literal["count", "size", "score"]
SortOrder = Literal["asc", "desc"]
Status = Literal["suggested", "accepted", "rejected"]


def _member_total(entity: dict[str, Any]) -> int:
    return sum(m.get("count") or 0 for m in entity.get("members") or [])


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
