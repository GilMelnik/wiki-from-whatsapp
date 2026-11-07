"""Load, edit, and persist suggested entity clusters for manual review."""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from step_4_entities.collect import load_claims_for_entities
from step_4_entities.mentions import (
    Analyzer,
    SimpleAnalyzer,
    Word,
    analyze_claims,
    build_or_load_analysis,
)
from step_4_entities.reviewer.store.aggregations import AggregationsMixin
from step_4_entities.reviewer.store.claims import ClaimsMixin
from step_4_entities.reviewer.store.mutations import MutationsMixin
from step_4_entities.reviewer.store.views import ViewsMixin
from utils.json_io import write_json_file
from utils.paths import (
    BACKUPS_DIR,
    EDITED_ENTITIES_PATH,
    MANUAL_AGGREGATIONS_PATH,
    ORIGINAL_ENTITIES_PATH,
    init_entities_edited,
    resolve_claims_path,
)


class EntityStore(ClaimsMixin, ViewsMixin, MutationsMixin, AggregationsMixin):
    def __init__(
        self,
        *,
        entities_path: Path | str | None = None,
        claims_path: Path | str | None = None,
        analyzer: Analyzer | None = None,
        analysis_cache_path: Path | str | None = None,
        aggregations_path: Path | str | None = None,
    ) -> None:
        self._entities_override = (
            Path(entities_path) if entities_path is not None else None
        )
        self._claims_override = Path(claims_path) if claims_path is not None else None
        self._aggregations_path = (
            Path(aggregations_path)
            if aggregations_path is not None
            else MANUAL_AGGREGATIONS_PATH
        )
        # Model-free default keeps direct/test instantiation offline; the web server
        # injects a DictaAnalyzer + shared cache so highlighting matches the pipeline.
        self._analyzer: Analyzer = analyzer or SimpleAnalyzer()
        self._analysis_cache_path = analysis_cache_path
        self._analysis: dict[str, list[Word]] = {}
        self._data: dict[str, Any] | None = None
        self._claims_by_id: dict[str, dict[str, Any]] = {}
        self._original_by_id: dict[str, dict[str, Any]] = {}
        self._claim_ids_by_name: dict[str, list[str]] = {}
        self._alias_to_entity: dict[str, str] = {}
        self._entity_color_index: dict[str, int] = {}
        self._entities_source = ORIGINAL_ENTITIES_PATH
        self._backup_done = False
        self._loaded = False
        # Manual claim aggregations: groups of near-duplicate claims the reviewer
        # marks so step 5 force-merges them under one chosen representative.
        self._aggregations: list[dict[str, Any]] = []
        self._agg_by_claim: dict[str, dict[str, Any]] = {}
        self._agg_by_rep: dict[str, dict[str, Any]] = {}
        # Claims hard-deleted in the reviewer: persisted on the entities payload
        # and filtered out everywhere (display + step 5).
        self._deleted_claims: set[str] = set()

    def load(self) -> None:
        init_entities_edited()
        if self._entities_override is not None:
            self._entities_source = self._entities_override
            if not self._entities_source.is_file():
                raise FileNotFoundError(
                    f"Entities file not found: {self._entities_source}"
                )
        else:
            self._entities_source = (
                EDITED_ENTITIES_PATH
                if EDITED_ENTITIES_PATH.exists()
                else ORIGINAL_ENTITIES_PATH
            )
        with self._entities_source.open(encoding="utf-8") as f:
            self._data = json.load(f)
        self._deleted_claims = set(self._data.get("deleted_claims") or [])

        claims_path = (
            self._claims_override
            if self._claims_override is not None
            else resolve_claims_path()
        )
        claims, resolved_claims, original_by_id = load_claims_for_entities(claims_path)
        self._claims_by_id = {c["claim_id"]: c for c in claims}
        self._original_by_id = original_by_id or {}
        for claim in claims:
            for name in claim.get("entities") or []:
                self._claim_ids_by_name.setdefault(name, []).append(claim["claim_id"])
        if self._analysis_cache_path is not None:
            self._analysis = build_or_load_analysis(
                claims,
                self._analyzer,
                cache_path=self._analysis_cache_path,
                source_path=resolved_claims,
            )
        else:
            self._analysis = analyze_claims(claims, self._analyzer)
        self._rebuild_alias_index()
        self._load_aggregations()
        self._loaded = True

    def _rebuild_alias_index(self) -> None:
        self._alias_to_entity = {}
        self._entity_color_index = {}
        for idx, entity in enumerate(self.entities):
            entity_id = entity["entity_id"]
            self._entity_color_index[entity_id] = idx
            for alias in entity.get("aliases") or []:
                self._alias_to_entity.setdefault(alias, entity_id)
            canonical = entity.get("canonical_name")
            if canonical:
                self._alias_to_entity.setdefault(canonical, entity_id)

    @property
    def loaded(self) -> bool:
        return self._loaded

    @property
    def entities(self) -> list[dict[str, Any]]:
        assert self._data is not None
        return self._data["entities"]

    def _find_entity(self, entity_id: str) -> tuple[dict[str, Any], int]:
        for idx, entity in enumerate(self.entities):
            if entity["entity_id"] == entity_id:
                return entity, idx
        raise KeyError(f"unknown entity: {entity_id}")

    def _new_entity_id(self) -> str:
        nums = [
            int(m.group(1))
            for e in self.entities
            if (m := re.fullmatch(r"e(\d+)", e["entity_id"]))
        ]
        return f"e{(max(nums) + 1) if nums else 0:04d}"

    # --- persistence ----------------------------------------------------

    def _ensure_backup(self) -> None:
        if self._backup_done:
            return
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
        if self._entities_source.exists():
            dest = (
                BACKUPS_DIR
                / f"{self._entities_source.stem}_{ts}{self._entities_source.suffix}"
            )
            shutil.copy2(self._entities_source, dest)
        self._backup_done = True

    def save(self) -> None:
        assert self._data is not None
        self._ensure_backup()
        self._rebuild_alias_index()
        meta = self._data.setdefault("metadata", {})
        meta["edited_by"] = "entity_reviewer"
        meta["edited_at"] = datetime.now().isoformat(timespec="seconds")
        meta["entity_count"] = len(self.entities)
        if self._entities_override is None:
            write_json_file(self._data, EDITED_ENTITIES_PATH)
            self._entities_source = EDITED_ENTITIES_PATH
        else:
            write_json_file(self._data, self._entities_source)

    def meta(self) -> dict[str, Any]:
        s = self.stats()
        return {
            "entities_path": str(self._entities_source),
            "entity_count": s["entity_count"],
            "multi_member_count": s["multi_member_count"],
            "by_status": s["by_status"],
        }
