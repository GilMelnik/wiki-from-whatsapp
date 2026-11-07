"""Manual claim aggregations for the entity store."""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from utils.json_io import write_json_file


class AggregationsMixin:
    """Groups of near-duplicate claims kept in ``claim_aggregations.json``.

    Relies on ``EntityStore`` attributes: ``_aggregations``,
    ``_aggregations_path``, ``_agg_by_claim``, ``_agg_by_rep`` and
    ``_claims_by_id``.
    """

    def _load_aggregations(self) -> None:
        self._aggregations = []
        if self._aggregations_path.is_file():
            with self._aggregations_path.open(encoding="utf-8") as f:
                self._aggregations = json.load(f).get("aggregations") or []
        self._reindex_aggregations()

    def _reindex_aggregations(self) -> None:
        self._agg_by_claim = {}
        self._agg_by_rep = {}
        for group in self._aggregations:
            rep = group.get("representative")
            for claim_id in group.get("claim_ids") or []:
                self._agg_by_claim[claim_id] = group
            if rep:
                self._agg_by_rep[rep] = group

    # --- manual claim aggregation (Req 4) -------------------------------

    def aggregations(self) -> list[dict[str, Any]]:
        return self._aggregations

    def _find_aggregation(self, group_id: str) -> dict[str, Any]:
        for group in self._aggregations:
            if group["id"] == group_id:
                return group
        raise KeyError(f"unknown aggregation: {group_id}")

    def _new_aggregation_id(self) -> str:
        nums = [
            int(m.group(1))
            for g in self._aggregations
            if (m := re.fullmatch(r"agg(\d+)", g["id"]))
        ]
        return f"agg{(max(nums) + 1) if nums else 0:04d}"

    def create_aggregation(
        self, claim_ids: list[str], representative: str
    ) -> dict[str, Any]:
        ids = list(dict.fromkeys(claim_ids))
        if len(ids) < 2:
            raise ValueError("an aggregation needs at least two claims")
        missing = [c for c in ids if c not in self._claims_by_id]
        if missing:
            raise ValueError(f"unknown claim_ids: {', '.join(missing)}")
        if representative not in ids:
            raise ValueError("representative must be one of the aggregated claims")
        clash = [c for c in ids if c in self._agg_by_claim]
        if clash:
            raise ValueError(f"claims already aggregated: {', '.join(clash)}")
        group = {
            "id": self._new_aggregation_id(),
            "representative": representative,
            "claim_ids": ids,
        }
        self._aggregations.append(group)
        self._save_aggregations()
        return group

    def set_representative(self, group_id: str, claim_id: str) -> dict[str, Any]:
        group = self._find_aggregation(group_id)
        if claim_id not in (group.get("claim_ids") or []):
            raise ValueError("representative must be one of the aggregated claims")
        group["representative"] = claim_id
        self._save_aggregations()
        return group

    def decouple_claim(self, group_id: str, claim_id: str) -> dict[str, Any]:
        group = self._find_aggregation(group_id)
        if claim_id not in (group.get("claim_ids") or []):
            raise ValueError("claim is not part of this aggregation")
        remaining = [c for c in group["claim_ids"] if c != claim_id]
        if len(remaining) < 2:
            self._aggregations = [g for g in self._aggregations if g is not group]
            self._save_aggregations()
            return {"id": group_id, "dissolved": True}
        group["claim_ids"] = remaining
        if group["representative"] == claim_id:
            group["representative"] = remaining[0]
        self._save_aggregations()
        return group

    def delete_aggregation(self, group_id: str) -> dict[str, Any]:
        self._find_aggregation(group_id)
        self._aggregations = [g for g in self._aggregations if g["id"] != group_id]
        self._save_aggregations()
        return {"id": group_id, "dissolved": True}

    def _remove_claim_from_aggregations(self, claim_id: str) -> None:
        """Drop a (deleted) claim from any group, dissolving groups under two."""

        changed = False
        kept: list[dict[str, Any]] = []
        for group in self._aggregations:
            ids = group.get("claim_ids") or []
            if claim_id not in ids:
                kept.append(group)
                continue
            changed = True
            remaining = [c for c in ids if c != claim_id]
            if len(remaining) < 2:
                continue  # dissolve
            group["claim_ids"] = remaining
            if group.get("representative") == claim_id:
                group["representative"] = remaining[0]
            kept.append(group)
        if changed:
            self._aggregations = kept
            self._save_aggregations()

    def _save_aggregations(self) -> None:
        self._reindex_aggregations()
        write_json_file(
            {
                "aggregations": self._aggregations,
                "metadata": {
                    "edited_by": "entity_reviewer",
                    "edited_at": datetime.now().isoformat(timespec="seconds"),
                    "count": len(self._aggregations),
                },
            },
            self._aggregations_path,
        )
