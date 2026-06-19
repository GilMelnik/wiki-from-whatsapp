"""Load, edit, and persist wiki plan + aggregated topic assignments."""

from __future__ import annotations

import json
import shutil
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from utils.json_io import write_json_file
from step_6_plan.run import identity_plan, pages_by_category
from utils.paths import (
    BACKUPS_DIR,
    EDITED_AGGREGATED_PATH,
    EDITED_PLAN_PATH,
    ORIGINAL_AGGREGATED_PATH,
    ORIGINAL_PLAN_PATH,
    init_aggregated_edited,
    init_plan_edited,
)
from utils.taxonomy import CATEGORIES, category_title


def _claim_key(merged: dict[str, Any]) -> str:
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


def _enrich_page(page: dict[str, Any], topics: dict[str, Any]) -> dict[str, Any]:
    source_tags = page.get("source_tags") or [page["id"]]
    claim_count = 0
    merged_count = 0
    for tag in source_tags:
        topic = topics.get(tag)
        if not topic:
            continue
        claim_count += topic.get("claim_count", 0)
        merged_count += topic.get("merged_claim_count", len(topic.get("merged_claims") or []))

    cat = page.get("category", "emergent")
    return {
        "id": page["id"],
        "title": page.get("title", page["id"]),
        "category": cat,
        "category_title": category_title(cat),
        "source_tags": source_tags,
        "rationale": page.get("rationale") or "",
        "search_focus": page.get("search_focus") or "",
        "claim_count": claim_count,
        "merged_claim_count": merged_count,
    }


def _enrich_claim(merged: dict[str, Any], topic_id: str) -> dict[str, Any]:
    return {
        "key": _claim_key(merged),
        "topic_id": topic_id,
        "claim_text": merged.get("claim_text", ""),
        "stance": merged.get("stance"),
        "support_count": merged.get("support_count"),
        "endorsement_count": merged.get("endorsement_count"),
        "thread_count": merged.get("thread_count"),
        "entities": merged.get("entities") or [],
        "date_range": merged.get("date_range"),
        "source_claim_ids": merged.get("source_claim_ids") or [],
    }


class PlanStore:
    def __init__(
        self,
        *,
        plan_path: Path | str | None = None,
        aggregated_path: Path | str | None = None,
    ) -> None:
        self._plan_override = Path(plan_path) if plan_path is not None else None
        self._aggregated_override = (
            Path(aggregated_path) if aggregated_path is not None else None
        )
        self._plan: dict[str, Any] | None = None
        self._aggregated: dict[str, Any] | None = None
        self._plan_source = ORIGINAL_PLAN_PATH
        self._aggregated_source = ORIGINAL_AGGREGATED_PATH
        self._backup_done = False
        self._loaded = False

    def load(self) -> None:
        init_aggregated_edited()
        init_plan_edited()

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

        topics = self._aggregated["topics"]
        if self._plan_override is not None:
            self._plan_source = self._plan_override
            if not self._plan_source.is_file():
                raise FileNotFoundError(f"Plan file not found: {self._plan_source}")
            with self._plan_source.open(encoding="utf-8") as f:
                self._plan = json.load(f)
        elif EDITED_PLAN_PATH.exists():
            self._plan_source = EDITED_PLAN_PATH
            with EDITED_PLAN_PATH.open(encoding="utf-8") as f:
                self._plan = json.load(f)
        elif ORIGINAL_PLAN_PATH.is_file():
            self._plan_source = ORIGINAL_PLAN_PATH
            with ORIGINAL_PLAN_PATH.open(encoding="utf-8") as f:
                self._plan = json.load(f)
        else:
            self._plan = identity_plan(topics)
            self._plan_source = EDITED_PLAN_PATH

        self._loaded = True

    @property
    def loaded(self) -> bool:
        return self._loaded

    @property
    def topics(self) -> dict[str, Any]:
        assert self._aggregated is not None
        return self._aggregated["topics"]

    @property
    def pages(self) -> list[dict[str, Any]]:
        assert self._plan is not None
        return self._plan.setdefault("pages", [])

    def _page_index(self, page_id: str) -> int:
        for idx, page in enumerate(self.pages):
            if page.get("id") == page_id:
                return idx
        raise KeyError(page_id)

    def get_page(self, page_id: str) -> dict[str, Any] | None:
        for page in self.pages:
            if page.get("id") == page_id:
                return _enrich_page(page, self.topics)
        return None

    def list_pages(self) -> list[dict[str, Any]]:
        return [_enrich_page(p, self.topics) for p in self.pages]

    def list_pages_grouped(self) -> list[dict[str, Any]]:
        grouped = pages_by_category(self._plan or {"pages": []})
        enriched_by_id = {p["id"]: p for p in self.list_pages()}
        sections: list[dict[str, Any]] = []
        for cat_title, page_pairs in grouped.items():
            sections.append(
                {
                    "category_title": cat_title,
                    "pages": [
                        enriched_by_id[pid]
                        for pid, _ in page_pairs
                        if pid in enriched_by_id
                    ],
                }
            )
        return sections

    def list_categories(self) -> list[dict[str, str]]:
        return [{"id": cid, "title": title} for cid, title in CATEGORIES.items()]

    def list_topics(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for topic_id, topic in sorted(self.topics.items()):
            out.append(
                {
                    "id": topic_id,
                    "title": topic.get("title", topic_id),
                    "category": topic.get("category", "emergent"),
                    "category_title": topic.get("category_title")
                    or category_title(topic.get("category", "emergent")),
                    "claim_count": topic.get("claim_count", 0),
                    "merged_claim_count": topic.get(
                        "merged_claim_count", len(topic.get("merged_claims") or [])
                    ),
                }
            )
        return out

    def list_claims(
        self,
        page_id: str,
        *,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[dict[str, Any]], int]:
        page = self._get_raw_page(page_id)
        items: list[dict[str, Any]] = []
        for tag in page.get("source_tags") or [page_id]:
            topic = self.topics.get(tag)
            if not topic:
                continue
            for merged in topic.get("merged_claims") or []:
                items.append(_enrich_claim(merged, tag))
        total = len(items)
        return items[offset : offset + limit], total

    def _get_raw_page(self, page_id: str) -> dict[str, Any]:
        idx = self._page_index(page_id)
        return self.pages[idx]

    def update_page(
        self,
        page_id: str,
        *,
        title: str | None = None,
        category: str | None = None,
        search_focus: str | None = None,
        rationale: str | None = None,
    ) -> dict[str, Any]:
        page = self._get_raw_page(page_id)
        if title is not None:
            page["title"] = title.strip()
        if category is not None:
            if category not in CATEGORIES:
                raise ValueError(f"unknown category: {category}")
            page["category"] = category
        if search_focus is not None:
            page["search_focus"] = search_focus.strip()
        if rationale is not None:
            page["rationale"] = rationale.strip()
        self.save()
        return _enrich_page(page, self.topics)

    def merge_pages(self, source_id: str, target_id: str) -> dict[str, Any]:
        if source_id == target_id:
            raise ValueError("cannot merge a page with itself")

        source = self._get_raw_page(source_id)
        target = self._get_raw_page(target_id)

        merged_tags: list[str] = []
        seen_tags: set[str] = set()
        for tag in (target.get("source_tags") or [target_id]) + (
            source.get("source_tags") or [source_id]
        ):
            if tag not in seen_tags:
                merged_tags.append(tag)
                seen_tags.add(tag)
        target["source_tags"] = merged_tags

        valid_ids = {p["id"] for p in self.pages if p["id"] != source_id}
        links = self._plan.setdefault("links", [])
        new_links: list[dict[str, str]] = []
        for link in links:
            src = link.get("from", "")
            dst = link.get("to", "")
            if src == source_id:
                src = target_id
            if dst == source_id:
                dst = target_id
            if src == dst or src not in valid_ids or dst not in valid_ids:
                continue
            new_links.append(
                {
                    "from": src,
                    "to": dst,
                    "reason": link.get("reason") or "",
                }
            )
        self._plan["links"] = new_links

        self.pages.pop(self._page_index(source_id))
        self.save()
        return _enrich_page(target, self.topics)

    def move_claim(
        self,
        *,
        topic_id: str,
        claim_key: str,
        target_topic_id: str,
    ) -> dict[str, Any]:
        if topic_id == target_topic_id:
            raise ValueError("source and target topic are the same")

        source_topic = self.topics.get(topic_id)
        target_topic = self.topics.get(target_topic_id)
        if source_topic is None:
            raise KeyError(f"unknown source topic: {topic_id}")
        if target_topic is None:
            raise KeyError(f"unknown target topic: {target_topic_id}")

        merged_claims = source_topic.get("merged_claims") or []
        moved: dict[str, Any] | None = None
        remaining: list[dict[str, Any]] = []
        for merged in merged_claims:
            if _claim_key(merged) == claim_key and moved is None:
                moved = deepcopy(merged)
            else:
                remaining.append(merged)

        if moved is None:
            raise KeyError(f"claim not found in topic {topic_id}: {claim_key}")

        source_topic["merged_claims"] = remaining
        target_topic.setdefault("merged_claims", []).append(moved)
        _recompute_topic_stats(source_topic)
        _recompute_topic_stats(target_topic)
        self.save()
        return _enrich_claim(moved, target_topic_id)

    def _ensure_backup(self) -> None:
        if self._backup_done:
            return
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
        for path in (self._plan_source, self._aggregated_source):
            if path.exists():
                dest = BACKUPS_DIR / f"{path.stem}_{ts}{path.suffix}"
                shutil.copy2(path, dest)
        self._backup_done = True

    def save(self) -> None:
        assert self._plan is not None and self._aggregated is not None
        self._ensure_backup()

        plan_meta = self._plan.setdefault("metadata", {})
        plan_meta["edited_by"] = "plan_reviewer"
        plan_meta["edited_at"] = datetime.now().isoformat(timespec="seconds")

        agg_meta = self._aggregated.setdefault("metadata", {})
        agg_meta["edited_by"] = "plan_reviewer"
        agg_meta["edited_at"] = datetime.now().isoformat(timespec="seconds")

        if self._plan_override is None:
            write_json_file(self._plan, EDITED_PLAN_PATH)
            self._plan_source = EDITED_PLAN_PATH
        else:
            write_json_file(self._plan, self._plan_source)

        if self._aggregated_override is None:
            write_json_file(self._aggregated, EDITED_AGGREGATED_PATH)
            self._aggregated_source = EDITED_AGGREGATED_PATH
        else:
            write_json_file(self._aggregated, self._aggregated_source)

    def meta(self) -> dict[str, Any]:
        pages = self.list_pages()
        return {
            "plan_path": str(self._plan_source),
            "aggregated_path": str(self._aggregated_source),
            "page_count": len(pages),
            "topic_count": len(self.topics),
            "link_count": len((self._plan or {}).get("links") or []),
            "total_claims": sum(p["claim_count"] for p in pages),
        }
