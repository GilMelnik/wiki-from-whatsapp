"""Manual claim aggregations: near-duplicate claims step 5 must merge under one representative.

The file (``claim_aggregations.json``) keeps the shape step 5 reads::

    {"aggregations": [{"id": "agg0000", "representative": claim_id, "claim_ids": [...]}],
     "metadata": {"edited_by", "edited_at", "count"}}

It is written only by a change, never by loading.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from step_4_entities.reviewer.store.helpers import EntityNotFound, ReviewConflict
from utils.json_io import write_json_file

_ID_RE = re.compile(r"agg(\d+)")
EDITOR = "entity_reviewer"


class ClaimAggregations:
    def __init__(self, path: Path) -> None:
        self._path = path
        self.groups: list[dict[str, Any]] = []
        if path.is_file():
            self.groups = json.loads(path.read_text(encoding="utf-8")).get("aggregations") or []
        self._reindex()

    def _reindex(self) -> None:
        self._by_claim = {cid: g for g in self.groups for cid in g.get("claim_ids") or []}
        self._by_rep = {g["representative"]: g for g in self.groups if g.get("representative")}

    def is_hidden(self, claim_id: str) -> bool:
        """A member that is not its group's representative is folded into it."""

        group = self._by_claim.get(claim_id)
        return group is not None and group.get("representative") != claim_id

    def group_of(self, claim_id: str) -> dict[str, Any] | None:
        return self._by_claim.get(claim_id)

    def view(self, claim_id: str, text_of: Callable[[str], str]) -> dict[str, Any] | None:
        """The group a representative claim stands for, with every member's text."""

        group = self._by_rep.get(claim_id)
        if group is None:
            return None
        ids = group.get("claim_ids") or []
        return {
            "id": group["id"],
            "representative": group.get("representative"),
            "count": len(ids),
            "members": [{"claim_id": cid, "claim_text": text_of(cid)} for cid in ids],
        }

    def _find(self, group_id: str) -> dict[str, Any]:
        for group in self.groups:
            if group["id"] == group_id:
                return group
        raise EntityNotFound(f"קבוצת הטענות {group_id} לא קיימת (אולי כבר פורקה)")

    def _new_id(self) -> str:
        numbers = [int(m.group(1)) for g in self.groups if (m := _ID_RE.fullmatch(g["id"]))]
        return f"agg{(max(numbers) + 1) if numbers else 0:04d}"

    def create(
        self, claim_ids: list[str], representative: str, usable: Callable[[str], bool]
    ) -> dict[str, Any]:
        ids = list(dict.fromkeys(claim_ids))
        if len(ids) < 2:
            raise ReviewConflict("כדי לאחד טענות צריך לבחור לפחות שתיים")
        unknown = [c for c in ids if not usable(c)]
        if unknown:
            raise ReviewConflict(f"טענות שלא קיימות או שנמחקו: {', '.join(unknown)}")
        if representative not in ids:
            raise ReviewConflict("הטענה המייצגת חייבת להיות אחת מהטענות המאוחדות")
        taken = [c for c in ids if c in self._by_claim]
        if taken:
            raise ReviewConflict(f"טענות שכבר שייכות לאיחוד אחר: {', '.join(taken)}")
        group = {"id": self._new_id(), "representative": representative, "claim_ids": ids}
        self.groups.append(group)
        self.save()
        return group

    def set_representative(self, group_id: str, claim_id: str) -> dict[str, Any]:
        group = self._find(group_id)
        if claim_id not in (group.get("claim_ids") or []):
            raise ReviewConflict("הטענה המייצגת חייבת להיות אחת מהטענות המאוחדות")
        group["representative"] = claim_id
        self.save()
        return group

    def decouple(self, group_id: str, claim_id: str) -> dict[str, Any] | None:
        """Take one claim out of a group; a group left with one claim dissolves (None)."""

        group = self._find(group_id)
        if claim_id not in (group.get("claim_ids") or []):
            raise ReviewConflict("הטענה לא שייכת לאיחוד הזה")
        kept = self._without(group, claim_id)
        self.save()
        return kept

    def delete(self, group_id: str) -> None:
        group = self._find(group_id)
        self.groups = [g for g in self.groups if g is not group]
        self.save()

    def remove_claim(self, claim_id: str) -> bool:
        """Drop a (deleted) claim from its group without saving; True when something changed."""

        group = self._by_claim.get(claim_id)
        if group is None:
            return False
        self._without(group, claim_id)
        return True

    def _without(self, group: dict[str, Any], claim_id: str) -> dict[str, Any] | None:
        remaining = [c for c in group["claim_ids"] if c != claim_id]
        if len(remaining) < 2:
            self.groups = [g for g in self.groups if g is not group]
            self._reindex()
            return None
        group["claim_ids"] = remaining
        if group.get("representative") == claim_id:
            group["representative"] = remaining[0]
        self._reindex()
        return group

    def save(self) -> None:
        self._reindex()
        payload = {
            "aggregations": self.groups,
            "metadata": {
                "edited_by": EDITOR,
                "edited_at": datetime.now().isoformat(timespec="seconds"),
                "count": len(self.groups),
            },
        }
        write_json_file(payload, self._path)
