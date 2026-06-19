"""Load, edit, and persist aggregated claim clusters for manual review."""

from __future__ import annotations

import json
import shutil
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from step_5_aggregate.run import (
    DEFAULT_AUDIT_PATH,
    _load_audit_records,
    build_merged_claim,
)
from utils.json_io import write_json_file
from utils.paths import (
    BACKUPS_DIR,
    EDITED_AGGREGATED_PATH,
    ORIGINAL_AGGREGATED_PATH,
    init_aggregated_edited,
    resolve_claims_path,
)
from utils.taxonomy import category_title

SortKind = Literal["support", "size"]
SortOrder = Literal["asc", "desc"]


def group_key(merged: dict[str, Any]) -> str:
    ids = merged.get("source_claim_ids") or []
    if ids:
        return str(ids[0])
    text = (merged.get("claim_text") or "")[:48]
    return f"anon:{hash(text)}"


def _recompute_topic_stats(topic: dict[str, Any]) -> None:
    merged = topic.get("merged_claims") or []
    topic["merged_claim_count"] = len(merged)
    topic["claim_count"] = sum(
        len(c.get("source_claim_ids") or [1]) for c in merged
    ) or len(merged)

    entity_stances: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    timeline: Counter[str] = Counter()
    all_dates: list[str] = []

    for claim in merged:
        support = claim.get("support_count", 1)
        for entity in claim.get("entities") or []:
            entity_stances[entity][claim.get("stance", "neutral")] += support
        for date in claim.get("date_range") or []:
            if date:
                timeline[date[:7]] += 1
                all_dates.append(date)

    contradictions: list[dict[str, Any]] = []
    for entity, stances in entity_stances.items():
        pos = stances.get("positive", 0)
        neg = stances.get("negative", 0)
        if pos > 0 and neg > 0:
            contradictions.append({"entity": entity, "positive": pos, "negative": neg})
    contradictions.sort(key=lambda d: d["positive"] + d["negative"], reverse=True)

    all_dates_sorted = sorted(set(all_dates))
    topic["entity_stances"] = {e: dict(s) for e, s in entity_stances.items()}
    topic["contradictions"] = contradictions
    topic["timeline"] = dict(sorted(timeline.items()))
    topic["date_range"] = (
        [all_dates_sorted[0], all_dates_sorted[-1]] if all_dates_sorted else [None, None]
    )


def _size_bucket(size: int, count: int) -> dict[str, Any]:
    if size == 1:
        desc = f"טענה אחת: {count} קבוצות"
    else:
        desc = f"{size} טענות: {count} קבוצות"
    return {
        "size": size,
        "count": count,
        "label": str(size),
        "description": desc,
    }


def _cluster_size_histogram(sizes: list[int]) -> list[dict[str, Any]]:
    counts: Counter[int] = Counter(sizes)
    return [_size_bucket(size, counts[size]) for size in sorted(counts)]


def _enrich_group(merged: dict[str, Any], topic_id: str) -> dict[str, Any]:
    return {
        "key": group_key(merged),
        "topic_id": topic_id,
        "claim_text": merged.get("claim_text", ""),
        "stance": merged.get("stance"),
        "support_count": merged.get("support_count"),
        "endorsement_count": merged.get("endorsement_count"),
        "thread_count": merged.get("thread_count"),
        "entities": merged.get("entities") or [],
        "source_claim_ids": merged.get("source_claim_ids") or [],
    }


class AggregateStore:
    def __init__(
        self,
        *,
        aggregated_path: Path | str | None = None,
        claims_path: Path | str | None = None,
        audit_path: Path | str | None = None,
    ) -> None:
        self._aggregated_override = (
            Path(aggregated_path) if aggregated_path is not None else None
        )
        self._claims_override = Path(claims_path) if claims_path is not None else None
        self._audit_path = Path(audit_path) if audit_path is not None else DEFAULT_AUDIT_PATH
        self._aggregated: dict[str, Any] | None = None
        self._claims_by_id: dict[str, dict[str, Any]] = {}
        self._audit_by_id: dict[str, dict[str, Any]] = {}
        self._aggregated_source = ORIGINAL_AGGREGATED_PATH
        self._backup_done = False
        self._loaded = False

    def load(self) -> None:
        init_aggregated_edited()

        if self._aggregated_override is not None:
            self._aggregated_source = self._aggregated_override
            if not self._aggregated_source.is_file():
                raise FileNotFoundError(
                    f"Aggregated file not found: {self._aggregated_source}"
                )
        else:
            self._aggregated_source = (
                EDITED_AGGREGATED_PATH
                if EDITED_AGGREGATED_PATH.exists()
                else ORIGINAL_AGGREGATED_PATH
            )

        with self._aggregated_source.open(encoding="utf-8") as f:
            self._aggregated = json.load(f)

        claims_path = (
            self._claims_override
            if self._claims_override is not None
            else resolve_claims_path()
        )
        with claims_path.open(encoding="utf-8") as f:
            claims_payload = json.load(f)
        self._claims_by_id = {
            c["claim_id"]: c for c in claims_payload.get("claims") or []
        }
        self._audit_by_id = _load_audit_records(self._audit_path)
        self._loaded = True

    @property
    def loaded(self) -> bool:
        return self._loaded

    @property
    def topics(self) -> dict[str, Any]:
        assert self._aggregated is not None
        return self._aggregated["topics"]

    def _find_group(
        self, topic_id: str, group_key_val: str
    ) -> tuple[dict[str, Any], dict[str, Any], int]:
        topic = self.topics.get(topic_id)
        if topic is None:
            raise KeyError(f"unknown topic: {topic_id}")
        merged_claims = topic.get("merged_claims") or []
        for idx, merged in enumerate(merged_claims):
            if group_key(merged) == group_key_val:
                return topic, merged, idx
        raise KeyError(f"group not found in topic {topic_id}: {group_key_val}")

    def _member_rows(
        self, merged: dict[str, Any]
    ) -> list[dict[str, Any]]:
        rep_text = merged.get("claim_text", "")
        rows: list[dict[str, Any]] = []
        for claim_id in merged.get("source_claim_ids") or []:
            source = self._claims_by_id.get(claim_id, {})
            text = source.get("claim_text") or ""
            rows.append(
                {
                    "source_claim_id": claim_id,
                    "claim_text": text,
                    "stance": source.get("stance"),
                    "thread_id": source.get("thread_id"),
                    "support_count": source.get("support_count"),
                    "is_representative": text == rep_text,
                }
            )
        return rows

    def _sorted_groups(
        self,
        topic_id: str,
        *,
        size_min: int | None = None,
        size_max: int | None = None,
        sort: SortKind = "support",
        order: SortOrder = "desc",
    ) -> list[dict[str, Any]]:
        topic = self.topics.get(topic_id)
        if topic is None:
            raise KeyError(f"unknown topic: {topic_id}")
        items = [_enrich_group(m, topic_id) for m in topic.get("merged_claims") or []]
        if size_min is not None:
            items = [i for i in items if (i["endorsement_count"] or 1) >= size_min]
        if size_max is not None:
            items = [i for i in items if (i["endorsement_count"] or 1) <= size_max]
        reverse = order == "desc"
        if sort == "size":
            items.sort(key=lambda i: i["endorsement_count"] or 1, reverse=reverse)
        else:
            items.sort(key=lambda i: i["support_count"] or 0, reverse=reverse)
        return items

    def list_topics(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for topic_id, topic in sorted(self.topics.items()):
            merged = topic.get("merged_claims") or []
            out.append(
                {
                    "id": topic_id,
                    "title": topic.get("title", topic_id),
                    "category": topic.get("category", "emergent"),
                    "category_title": topic.get("category_title")
                    or category_title(topic.get("category", "emergent")),
                    "claim_count": topic.get("claim_count", 0),
                    "group_count": len(merged),
                }
            )
        return out

    def list_groups(
        self,
        topic_id: str,
        *,
        size_min: int | None = None,
        size_max: int | None = None,
        sort: SortKind = "support",
        order: SortOrder = "desc",
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[dict[str, Any]], int]:
        items = self._sorted_groups(
            topic_id,
            size_min=size_min,
            size_max=size_max,
            sort=sort,
            order=order,
        )
        total = len(items)
        return items[offset : offset + limit], total

    def get_group(
        self,
        topic_id: str,
        group_key_val: str,
        *,
        size_min: int | None = None,
        size_max: int | None = None,
        sort: SortKind = "support",
        order: SortOrder = "desc",
    ) -> dict[str, Any]:
        _, merged, _ = self._find_group(topic_id, group_key_val)
        items = self._sorted_groups(
            topic_id,
            size_min=size_min,
            size_max=size_max,
            sort=sort,
            order=order,
        )
        keys = [i["key"] for i in items]
        try:
            pos = keys.index(group_key_val)
        except ValueError:
            pos = -1
        prev_key = keys[pos - 1] if pos > 0 else None
        next_key = keys[pos + 1] if 0 <= pos < len(keys) - 1 else None
        enriched = _enrich_group(merged, topic_id)
        enriched["members"] = self._member_rows(merged)
        enriched["variants"] = merged.get("variants") or []
        return {
            "group": enriched,
            "queue": {
                "prev_key": prev_key,
                "next_key": next_key,
                "position": pos + 1 if pos >= 0 else None,
                "total": len(keys),
            },
        }

    def stats(self) -> dict[str, Any]:
        all_sizes: list[int] = []
        by_topic: list[dict[str, Any]] = []
        for topic_id, topic in sorted(self.topics.items()):
            merged = topic.get("merged_claims") or []
            sizes = [m.get("endorsement_count") or 1 for m in merged]
            all_sizes.extend(sizes)
            by_topic.append(
                {
                    "id": topic_id,
                    "title": topic.get("title", topic_id),
                    "group_count": len(merged),
                    "source_claim_count": sum(sizes),
                    "singleton_count": sum(1 for s in sizes if s == 1),
                    "cluster_size": _cluster_size_histogram(sizes),
                }
            )
        max_observed = max(all_sizes) if all_sizes else 1
        return {
            "topic_count": len(self.topics),
            "group_count": len(all_sizes),
            "source_claim_count": sum(all_sizes),
            "singleton_count": sum(1 for s in all_sizes if s == 1),
            "max_cluster_size": max_observed,
            "cluster_size": _cluster_size_histogram(all_sizes),
            "by_topic": by_topic,
        }

    def set_representative(
        self, topic_id: str, group_key_val: str, source_claim_id: str
    ) -> dict[str, Any]:
        topic, merged, _ = self._find_group(topic_id, group_key_val)
        ids = merged.get("source_claim_ids") or []
        if source_claim_id not in ids:
            raise ValueError(f"claim not in group: {source_claim_id}")
        source = self._claims_by_id.get(source_claim_id)
        if source is None:
            raise KeyError(f"unknown source claim: {source_claim_id}")
        merged["claim_text"] = source["claim_text"]
        _recompute_topic_stats(topic)
        self.save()
        return self.get_group(topic_id, group_key_val)["group"]

    def move_member(
        self,
        topic_id: str,
        group_key_val: str,
        *,
        source_claim_id: str,
        target_group_key: str,
    ) -> dict[str, Any]:
        if group_key_val == target_group_key:
            raise ValueError("source and target group are the same")

        topic = self.topics[topic_id]
        merged_claims = topic.get("merged_claims") or []

        source_merged: dict[str, Any] | None = None
        source_idx = -1
        target_merged: dict[str, Any] | None = None
        target_idx = -1
        for idx, merged in enumerate(merged_claims):
            key = group_key(merged)
            if key == group_key_val:
                source_merged = merged
                source_idx = idx
            if key == target_group_key:
                target_merged = merged
                target_idx = idx

        if source_merged is None:
            raise KeyError(f"group not found: {group_key_val}")
        if target_merged is None:
            raise KeyError(f"target group not found: {target_group_key}")

        ids = source_merged.get("source_claim_ids") or []
        if source_claim_id not in ids:
            raise ValueError(f"claim not in source group: {source_claim_id}")

        moving = self._claims_by_id.get(source_claim_id)
        if moving is None:
            raise KeyError(f"unknown source claim: {source_claim_id}")

        remaining_ids = [i for i in ids if i != source_claim_id]
        if not remaining_ids:
            merged_claims.pop(source_idx)
            if source_idx < target_idx:
                target_idx -= 1
        else:
            remaining_claims = [self._claims_by_id[i] for i in remaining_ids]
            rep_text = source_merged.get("claim_text")
            if moving["claim_text"] == rep_text:
                rep_text = remaining_claims[0]["claim_text"]
            merged_claims[source_idx] = build_merged_claim(
                remaining_claims,
                self._audit_by_id,
                claim_text=rep_text,
            )

        target_ids = list(target_merged.get("source_claim_ids") or [])
        target_ids.append(source_claim_id)
        target_claims = [self._claims_by_id[i] for i in target_ids]
        merged_claims[target_idx] = build_merged_claim(
            target_claims,
            self._audit_by_id,
            claim_text=target_merged.get("claim_text"),
        )

        topic["merged_claims"] = merged_claims
        _recompute_topic_stats(topic)
        self.save()
        new_key = group_key(merged_claims[target_idx])
        return self.get_group(topic_id, new_key)["group"]

    def split_cluster(
        self,
        topic_id: str,
        group_key_val: str,
        *,
        source_claim_ids: list[str],
    ) -> dict[str, Any]:
        if not source_claim_ids:
            raise ValueError("source_claim_ids must not be empty")

        topic, merged, idx = self._find_group(topic_id, group_key_val)
        all_ids = set(merged.get("source_claim_ids") or [])
        split_set = set(source_claim_ids)
        if not split_set.issubset(all_ids):
            raise ValueError("source_claim_ids must be subset of group members")
        remain_ids = [i for i in merged.get("source_claim_ids") or [] if i not in split_set]
        if not remain_ids:
            raise ValueError("split would empty the original group")
        if len(split_set) == len(all_ids):
            raise ValueError("split must leave at least one member in original group")

        remain_claims = [self._claims_by_id[i] for i in remain_ids]
        split_claims = [self._claims_by_id[i] for i in source_claim_ids]

        rep_text = merged.get("claim_text")
        if rep_text in {c["claim_text"] for c in split_claims}:
            rep_text = remain_claims[0]["claim_text"]

        merged_claims = topic.get("merged_claims") or []
        merged_claims[idx] = build_merged_claim(
            remain_claims,
            self._audit_by_id,
            claim_text=rep_text,
        )
        new_group = build_merged_claim(split_claims, self._audit_by_id)
        merged_claims.append(new_group)
        topic["merged_claims"] = merged_claims
        _recompute_topic_stats(topic)
        self.save()
        return {
            "original": self.get_group(topic_id, group_key(merged_claims[idx]))["group"],
            "new": self.get_group(topic_id, group_key(new_group))["group"],
        }

    def _ensure_backup(self) -> None:
        if self._backup_done:
            return
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
        if self._aggregated_source.exists():
            dest = BACKUPS_DIR / f"{self._aggregated_source.stem}_{ts}{self._aggregated_source.suffix}"
            shutil.copy2(self._aggregated_source, dest)
        self._backup_done = True

    def save(self) -> None:
        assert self._aggregated is not None
        self._ensure_backup()
        meta = self._aggregated.setdefault("metadata", {})
        meta["edited_by"] = "aggregate_reviewer"
        meta["edited_at"] = datetime.now().isoformat(timespec="seconds")
        if self._aggregated_override is None:
            write_json_file(self._aggregated, EDITED_AGGREGATED_PATH)
            self._aggregated_source = EDITED_AGGREGATED_PATH
        else:
            write_json_file(self._aggregated, self._aggregated_source)

    def meta(self) -> dict[str, Any]:
        s = self.stats()
        return {
            "aggregated_path": str(self._aggregated_source),
            "topic_count": s["topic_count"],
            "group_count": s["group_count"],
            "source_claim_count": s["source_claim_count"],
        }
