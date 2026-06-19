"""Distribution statistics for thread browsing."""

from __future__ import annotations

from collections import Counter
from typing import Any, Literal

from step_2_thread_review.models import FilterKind, SortKind, duration_sec


def _histogram(values: list[float], bucket_count: int = 20) -> list[dict[str, Any]]:
    if not values:
        return []
    vmin = min(values)
    vmax = max(values)
    if vmin == vmax:
        return [{"min": vmin, "max": vmax, "count": len(values), "label": str(int(vmin))}]

    width = (vmax - vmin) / bucket_count
    buckets = [0] * bucket_count
    for v in values:
        idx = min(bucket_count - 1, int((v - vmin) / width) if width else 0)
        buckets[idx] += 1

    result: list[dict[str, Any]] = []
    for i, count in enumerate(buckets):
        bmin = vmin + i * width
        bmax = vmin + (i + 1) * width if i < bucket_count - 1 else vmax
        label = f"{_fmt_num(bmin)}–{_fmt_num(bmax)}"
        result.append({"min": bmin, "max": bmax, "count": count, "label": label})
    return result


def _fmt_num(n: float) -> str:
    if abs(n - round(n)) < 0.01:
        return str(int(round(n)))
    return f"{n:.1f}"


def _month_histogram(values: list[str]) -> list[dict[str, Any]]:
    months = [v[:7] for v in values if v]
    counts = Counter(months)
    return [
        {"min": m, "max": m, "count": counts[m], "label": m}
        for m in sorted(counts)
    ]


def enrich_thread(
    thread: dict[str, Any],
    classification: dict[str, Any],
    *,
    has_classification: bool = True,
) -> dict[str, Any]:
    base = {
        "thread_id": thread["thread_id"],
        "start_time": thread["start_time"],
        "last_time": thread["last_time"],
        "num_messages": thread["num_messages"],
        "num_unique_senders": thread["num_unique_senders"],
        "duration_sec": duration_sec(thread["start_time"], thread["last_time"]),
        "has_classification": has_classification,
    }
    if not has_classification:
        return {
            **base,
            "is_knowledge_bearing": None,
            "is_useless": False,
            "topic_tags": [],
            "reason": "",
        }
    return {
        **base,
        "is_knowledge_bearing": bool(classification.get("is_knowledge_bearing")),
        "is_useless": not bool(classification.get("is_knowledge_bearing")),
        "topic_tags": classification.get("topic_tags") or [],
        "reason": classification.get("reason") or "",
    }


def filter_threads(
    items: list[dict[str, Any]],
    filter_kind: FilterKind,
) -> list[dict[str, Any]]:
    if filter_kind == "all":
        return items
    tagged = [i for i in items if i.get("has_classification", True)]
    if filter_kind == "useless":
        return [i for i in tagged if i["is_useless"]]
    return [i for i in tagged if i["is_knowledge_bearing"]]


def sort_threads(
    items: list[dict[str, Any]],
    sort: SortKind,
    order: Literal["asc", "desc"],
) -> list[dict[str, Any]]:
    key_map = {
        "num_messages": lambda i: i["num_messages"],
        "participants": lambda i: i["num_unique_senders"],
        "start_time": lambda i: i["start_time"],
        "duration": lambda i: i["duration_sec"],
    }
    reverse = order == "desc"
    return sorted(items, key=key_map[sort], reverse=reverse)


def compute_stats(
    items: list[dict[str, Any]],
    filter_kind: FilterKind = "all",
) -> dict[str, Any]:
    filtered = filter_threads(items, filter_kind)
    msg_counts = [float(i["num_messages"]) for i in filtered]
    participants = [float(i["num_unique_senders"]) for i in filtered]
    durations = [float(i["duration_sec"]) for i in filtered]
    start_times = [i["start_time"] for i in filtered]

    knowledge = sum(1 for i in filtered if i["is_knowledge_bearing"])
    useless = len(filtered) - knowledge

    tag_counter: Counter[str] = Counter()
    reason_counter: Counter[str] = Counter()
    for item in filtered:
        for tag in item.get("topic_tags") or []:
            tag_counter[tag] += 1
        reason = item.get("reason") or ""
        if reason:
            reason_counter[reason] += 1

    return {
        "total": len(filtered),
        "knowledge_bearing": knowledge,
        "useless": useless,
        "histograms": {
            "num_messages": _histogram(msg_counts),
            "num_unique_senders": _histogram(participants),
            "duration_sec": _histogram(durations),
            "start_time": _month_histogram(start_times),
        },
        "top_topic_tags": tag_counter.most_common(15),
        "top_reasons": reason_counter.most_common(15),
    }
