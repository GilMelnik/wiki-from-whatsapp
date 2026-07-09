"""Pure helpers for the aggregate reviewer store (grouping, stats, histograms)."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any


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
