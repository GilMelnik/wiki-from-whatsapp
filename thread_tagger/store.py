"""Load, join, and persist thread data for the tagger."""

from __future__ import annotations

import json
import re
import shutil
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from utils import write_json_file

from thread_tagger.models import FilterKind, SortKind, SortOrder, default_classification
from thread_tagger.operations import (
    classification_from_thread,
    merge_threads,
    move_messages,
    patch_classification,
    split_by_mode,
)
from thread_tagger.paths import (
    BACKUPS_DIR,
    edited_output_classified_path,
    edited_output_threads_path,
    init_classified_edited,
    init_threads_edited,
    resolve_classified_path,
    resolve_threads_path,
)
from thread_tagger.stats import enrich_thread, filter_threads, sort_threads

_SPLIT_ID = re.compile(r"^(.+)-split-(\d+)$")


class ThreadStore:
    def __init__(
        self,
        *,
        inspect_only: bool = False,
        threads_path: Path | str | None = None,
    ) -> None:
        self.inspect_only = inspect_only
        self._threads_path_override = (
            Path(threads_path) if threads_path is not None else None
        )
        self._threads_payload: dict[str, Any] | None = None
        self._classified_payload: dict[str, Any] | None = None
        self._classifications: dict[str, dict[str, Any]] = {}
        self._threads_by_id: dict[str, dict[str, Any]] = {}
        self._has_classification = False
        self._backup_done = False
        self._loaded = False
        self._source_threads_path = resolve_threads_path()
        self._source_classified_path = resolve_classified_path()

    @property
    def has_classification(self) -> bool:
        return self._has_classification

    def load(self) -> None:
        if self._threads_path_override is not None:
            self._source_threads_path = self._threads_path_override
            if not self._source_threads_path.is_file():
                raise FileNotFoundError(
                    f"Threads file not found: {self._source_threads_path}"
                )
        else:
            init_threads_edited()
            self._source_threads_path = resolve_threads_path()

        with self._source_threads_path.open(encoding="utf-8") as f:
            self._threads_payload = json.load(f)

        self._threads_by_id = {
            t["thread_id"]: t for t in self._threads_payload["threads"]
        }

        use_classification = (
            not self.inspect_only
            and self._threads_path_override is None
        )
        if use_classification:
            init_classified_edited()
            self._source_classified_path = resolve_classified_path()
            if self._source_classified_path.is_file():
                with self._source_classified_path.open(encoding="utf-8") as f:
                    self._classified_payload = json.load(f)
                self._classifications = {
                    r["thread_id"]: r for r in self._classified_payload["threads"]
                }
                self._has_classification = True
            else:
                use_classification = False

        if not use_classification:
            self.inspect_only = True
            self._has_classification = False
            self._classified_payload = {
                "threads": [],
                "metadata": {"source": "inspect_only"},
            }
            self._classifications = {}

        for thread in self._threads_payload["threads"]:
            tid = thread["thread_id"]
            if tid not in self._classifications:
                self._classifications[tid] = default_classification(thread)

        self._loaded = True

    @property
    def loaded(self) -> bool:
        return self._loaded

    @property
    def threads(self) -> list[dict[str, Any]]:
        if not self._loaded or self._threads_payload is None:
            raise RuntimeError("ThreadStore.load() has not completed")
        return self._threads_payload["threads"]

    def get_thread(self, thread_id: str) -> dict[str, Any] | None:
        return self._threads_by_id.get(thread_id)

    def get_classification(self, thread_id: str) -> dict[str, Any] | None:
        thread = self._threads_by_id.get(thread_id)
        if thread is None:
            raise KeyError(thread_id)
        if not self._has_classification:
            return None
        return self._classifications.get(thread_id, default_classification(thread))

    def _enrich(self, thread: dict[str, Any]) -> dict[str, Any]:
        cls = self.get_classification(thread["thread_id"])
        if cls is None:
            return enrich_thread(thread, {}, has_classification=False)
        return enrich_thread(thread, cls, has_classification=True)

    def list_enriched(
        self,
        filter_kind: FilterKind = "all",
        sort: SortKind = "start_time",
        order: SortOrder = "asc",
        offset: int = 0,
        limit: int = 50,
        *,
        num_messages_min: float | None = None,
        num_messages_max: float | None = None,
        participants_min: float | None = None,
        participants_max: float | None = None,
        duration_min: float | None = None,
        duration_max: float | None = None,
        start_month: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        items = [self._enrich(t) for t in self.threads]
        items = filter_threads(items, filter_kind)
        items = self._apply_bucket_filters(
            items,
            num_messages_min=num_messages_min,
            num_messages_max=num_messages_max,
            participants_min=participants_min,
            participants_max=participants_max,
            duration_min=duration_min,
            duration_max=duration_max,
            start_month=start_month,
        )
        items = sort_threads(items, sort, order)
        total = len(items)
        return items[offset : offset + limit], total

    @staticmethod
    def _apply_bucket_filters(
        items: list[dict[str, Any]],
        **kwargs: float | str | None,
    ) -> list[dict[str, Any]]:
        result = items
        if kwargs.get("num_messages_min") is not None:
            result = [
                i
                for i in result
                if i["num_messages"] >= float(kwargs["num_messages_min"])  # type: ignore[arg-type]
            ]
        if kwargs.get("num_messages_max") is not None:
            result = [
                i
                for i in result
                if i["num_messages"] <= float(kwargs["num_messages_max"])  # type: ignore[arg-type]
            ]
        if kwargs.get("participants_min") is not None:
            result = [
                i
                for i in result
                if i["num_unique_senders"] >= float(kwargs["participants_min"])  # type: ignore[arg-type]
            ]
        if kwargs.get("participants_max") is not None:
            result = [
                i
                for i in result
                if i["num_unique_senders"] <= float(kwargs["participants_max"])  # type: ignore[arg-type]
            ]
        if kwargs.get("duration_min") is not None:
            result = [
                i
                for i in result
                if i["duration_sec"] >= float(kwargs["duration_min"])  # type: ignore[arg-type]
            ]
        if kwargs.get("duration_max") is not None:
            result = [
                i
                for i in result
                if i["duration_sec"] <= float(kwargs["duration_max"])  # type: ignore[arg-type]
            ]
        if kwargs.get("start_month"):
            month = str(kwargs["start_month"])
            result = [i for i in result if i["start_time"].startswith(month)]
        return result

    def chrono_order(self) -> list[str]:
        ordered = sorted(
            self.threads,
            key=lambda t: (t["start_time"], t["thread_id"]),
        )
        return [t["thread_id"] for t in ordered]

    def neighbors(self, thread_id: str) -> dict[str, str | None]:
        order = self.chrono_order()
        if thread_id not in order:
            return {"prev_id": None, "next_id": None}
        idx = order.index(thread_id)
        return {
            "prev_id": order[idx - 1] if idx > 0 else None,
            "next_id": order[idx + 1] if idx < len(order) - 1 else None,
        }

    def split_family_ids(self, thread_id: str) -> list[str]:
        """Thread ids from the same manual split family, in chronological order."""
        match = _SPLIT_ID.match(thread_id)
        base = match.group(1) if match else thread_id
        prefix = f"{base}-split-"
        ids = [base]
        for tid in self._threads_by_id:
            if tid.startswith(prefix):
                ids.append(tid)
        if len(ids) == 1:
            return ids
        return sorted(
            ids,
            key=lambda tid: (
                self._threads_by_id[tid]["start_time"],
                self._threads_by_id[tid].get("last_time", ""),
                tid,
            ),
        )

    def message_context(self, thread_id: str) -> dict[str, Any]:
        """Chronological neighbors plus all parts of a split family."""
        neighbors = self.neighbors(thread_id)
        family_ids = set(self.split_family_ids(thread_id))

        prev_thread = None
        prev_id = neighbors.get("prev_id")
        if prev_id and prev_id not in family_ids:
            prev_thread = self.get_thread(prev_id)

        next_thread = None
        next_id = neighbors.get("next_id")
        if next_id and next_id not in family_ids:
            next_thread = self.get_thread(next_id)

        family = [
            self.get_thread(tid)
            for tid in self.split_family_ids(thread_id)
            if self.get_thread(tid) is not None
        ]
        return {
            "prev": prev_thread,
            "next": next_thread,
            "family": family,
        }

    def queue_neighbors(
        self,
        thread_id: str,
        filter_kind: FilterKind,
        sort: SortKind,
        order: SortOrder,
    ) -> dict[str, str | None]:
        items, _ = self.list_enriched(filter_kind=filter_kind, sort=sort, order=order, limit=100000)
        ids = [i["thread_id"] for i in items]
        if thread_id not in ids:
            return {"prev_in_queue": None, "next_in_queue": None}
        idx = ids.index(thread_id)
        return {
            "prev_in_queue": ids[idx - 1] if idx > 0 else None,
            "next_in_queue": ids[idx + 1] if idx < len(ids) - 1 else None,
        }

    def update_classification(
        self,
        thread_id: str,
        *,
        is_knowledge_bearing: bool | None = None,
        topic_tags: list[str] | None = None,
        entities: list[str] | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        if not self._has_classification:
            raise ValueError("classification data not loaded; tagging is unavailable")
        record = deepcopy(self.get_classification(thread_id) or default_classification(
            self._threads_by_id[thread_id]
        ))
        patch_classification(
            record,
            is_knowledge_bearing=is_knowledge_bearing,
            topic_tags=topic_tags,
            entities=entities,
            reason=reason,
        )
        thread = self._threads_by_id[thread_id]
        record = classification_from_thread(thread, record)
        self._classifications[thread_id] = record
        self.save()
        return record

    def merge(
        self,
        thread_ids: list[str],
        survivor_id: str | None = None,
        inherit_classification: str | None = None,
    ) -> str:
        new_threads, new_classifications, survivor = merge_threads(
            self.threads,
            self._classifications,
            thread_ids,
            survivor_id=survivor_id,
            inherit_classification=inherit_classification,
        )
        self._apply_structural(new_threads, new_classifications)
        self.save()
        return survivor

    def split(
        self,
        source_id: str,
        mode: str,
        message_indices: list[int],
    ) -> dict[str, Any]:
        new_threads, new_classifications, new_id, remainder_id = split_by_mode(
            self.threads,
            self._classifications,
            source_id,
            mode,
            message_indices,
        )
        self._apply_structural(new_threads, new_classifications)
        self.save()
        thread_ids = [new_id]
        if remainder_id:
            thread_ids.insert(0, remainder_id)
        return {
            "new_thread_id": new_id,
            "focus_thread_id": new_id,
            "remainder_id": remainder_id,
            "thread_ids": thread_ids,
        }

    def move(
        self,
        source_id: str,
        message_indices: list[int],
        target_id: str,
        position: str,
    ) -> None:
        new_threads, new_classifications = move_messages(
            self.threads,
            self._classifications,
            source_id,
            message_indices,
            target_id,
            position,
        )
        self._apply_structural(new_threads, new_classifications)
        self.save()

    def _apply_structural(
        self,
        threads: list[dict[str, Any]],
        classifications: dict[str, dict[str, Any]],
    ) -> None:
        assert self._threads_payload is not None
        assert self._classified_payload is not None
        self._threads_payload["threads"] = threads
        self._threads_by_id = {t["thread_id"]: t for t in threads}
        self._classifications = classifications
        if self._has_classification:
            self._classified_payload["threads"] = [
                classifications[t["thread_id"]]
                for t in threads
                if t["thread_id"] in classifications
            ]

    def _sync_metadata(self) -> None:
        assert self._threads_payload is not None
        assert self._classified_payload is not None

        threads_meta = self._threads_payload.setdefault("metadata", {})
        threads_meta["thread_count"] = len(self._threads_payload["threads"])

        if self._has_classification:
            classified = self._classified_payload["threads"]
            cls_meta = self._classified_payload.setdefault("metadata", {})
            cls_meta["thread_count"] = len(classified)
            cls_meta["knowledge_bearing_count"] = sum(
                1 for r in classified if r.get("is_knowledge_bearing")
            )

    def _ensure_backup(self) -> None:
        if self._backup_done:
            return
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
        sources = [self._source_threads_path]
        if self._has_classification and self._source_classified_path.exists():
            sources.append(self._source_classified_path)
        for src in sources:
            if src.exists():
                dest = BACKUPS_DIR / f"{src.stem}_{ts}{src.suffix}"
                shutil.copy2(src, dest)
        self._backup_done = True

    def save(self) -> None:
        assert self._threads_payload is not None
        assert self._classified_payload is not None
        self._ensure_backup()
        self._sync_metadata()

        if self._threads_path_override is not None:
            write_json_file(self._threads_payload, self._source_threads_path)
            return

        threads_path = edited_output_threads_path()
        meta = self._threads_payload.setdefault("metadata", {})
        meta["edited_by"] = "thread_tagger"
        meta["edited_at"] = datetime.now().isoformat(timespec="seconds")
        write_json_file(self._threads_payload, threads_path)
        self._source_threads_path = threads_path

        if self._has_classification:
            classified_path = edited_output_classified_path()
            cls_meta = self._classified_payload.setdefault("metadata", {})
            cls_meta["edited_by"] = "thread_tagger"
            cls_meta["edited_at"] = datetime.now().isoformat(timespec="seconds")
            write_json_file(self._classified_payload, classified_path)
            self._source_classified_path = classified_path

    def meta(self) -> dict[str, Any]:
        return {
            "inspect_only": self.inspect_only,
            "has_classification": self._has_classification,
            "threads_path": str(self._source_threads_path),
            "classified_path": (
                str(self._source_classified_path)
                if self._has_classification
                else None
            ),
            "thread_count": len(self.threads),
        }
