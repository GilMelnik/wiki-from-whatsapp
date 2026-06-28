"""Step 6: plan wiki pages, merges, and cross-links from aggregated claims.

The planning agent reads all aggregated topic buckets and the taxonomy seed list,
then proposes a page catalog (with merges/splits/new pages) and a link graph.
Output: ``data/wiki_plan.json``.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from utils.json_io import write_json_file
from utils.llm_client import BatchRequest, LLMClient
from step_3_extract.scrub import FORBIDDEN_TERM_INSTRUCTION
from utils.taxonomy import CATEGORIES, category_title, resolve_search_focus, taxonomy_seed_block

from utils.paths import resolve_aggregated_path
DEFAULT_AGGREGATED_PATH = Path("data/claims_aggregated.json")
DEFAULT_OUTPUT_PATH = Path("data/wiki_plan.json")

PLAN_SYSTEM = (
    "אתה מתכנן את מבנה הויקי בעברית על פונדקאות לגייז, על בסיס טענות שחולצו מקבוצת וואטסאפ.\n"
    "כללים:\n"
    "1. רשימת הנושאים המוצעים היא נקודת התחלה בלבד — אתה רשאי למזג נושאים קרובים, "
    "לפצל נושאים רחבים וליצור עמודים חדשים.\n"
    "2. כל עמוד חייב לכלול לפחות טענה אחת, באמצעות source_tags המצביעים על מזהי הנושאים המקוריים.\n"
    "3. הצע גרף קישורים סמנטי בין עמודים (ללא הגבלת מספר): קשר כל עמוד לעמודים "
    "המשלימים או הקרובים לו מבחינת תוכן. קישורים אלה מזינים את סעיף 'עמודים קשורים'.\n"
    "4. עבור עמודים ברשימת הנושאים עם search_focus מוגדר — השתמש בערך הזה כפי שהוא. "
    "עבור עמודים חדשים שלא ברשימה, הצע search_focus — שאילתת חיפוש לרקע ציבורי כללי (לא דעות הקבוצה).\n"
    f"5. {FORBIDDEN_TERM_INSTRUCTION}\n"
    "החזר אך ורק אובייקט JSON תקין, ללא טקסט נוסף."
)


def _topic_summary_lines(topics: dict[str, Any]) -> str:
    lines: list[str] = []
    for topic_id, topic in sorted(
        topics.items(), key=lambda kv: kv[1]["claim_count"], reverse=True
    ):
        entities = sorted(
            {e for c in topic["merged_claims"][:5] for e in c.get("entities", [])}
        )[:6]
        samples = [c["claim_text"][:120] for c in topic["merged_claims"][:3]]
        sample_text = " / ".join(samples) if samples else "(אין דוגמאות)"
        entity_text = ", ".join(entities) if entities else "(אין)"
        lines.append(
            f"- id: {topic_id} | title: {topic['title']} | claims: {topic['claim_count']} "
            f"| category: {topic['category']} | entities: {entity_text} "
            f"| samples: {sample_text}"
        )
    return "\n".join(lines)


def build_plan_prompt(topics: dict[str, Any]) -> str:
    categories = "\n".join(f"- {cid}: {title}" for cid, title in CATEGORIES.items())
    return (
        "נושאים מוצעים (seed taxonomy — ניתן להתעלם, למזג או להרחיב):\n"
        f"{taxonomy_seed_block()}\n\n"
        "קטגוריות אפשריות לעמודים:\n"
        f"{categories}\n\n"
        "נושאים שחולצו מהשיחות (כל שורה = bucket אגרגציה):\n"
        f"{_topic_summary_lines(topics)}\n\n"
        "החזר JSON במבנה:\n"
        "{\n"
        '  "pages": [\n'
        "    {\n"
        '      "id": "<slug-latin>",\n'
        '      "title": "<כותרת בעברית>",\n'
        '      "category": "<category-id>",\n'
        '      "source_tags": ["<topic-id>", ...],\n'
        '      "rationale": "<משפט קצר>",\n'
        '      "search_focus": "<search query — use seed value when defined, else propose one>"\n'
        "    }\n"
        "  ],\n"
        '  "links": [\n'
        '    {"from": "<page-id>", "to": "<page-id>", "reason": "<משפט קצר>"}\n'
        "  ]\n"
        "}\n"
        "כל source_tag חייב להיות מזהה מהרשימה למעלה. "
        "אל תיצור עמודים ללא טענות. מזג buckets קשורים לעמוד אחד כשמתאים."
    )


def identity_plan(
    topics: dict[str, Any], *, min_claims: int = 1
) -> dict[str, Any]:
    """One page per aggregated topic (legacy / --no-plan behaviour)."""

    pages: list[dict[str, Any]] = []
    for topic_id, topic in topics.items():
        if topic["claim_count"] < min_claims:
            continue
        pages.append(
            {
                "id": topic_id,
                "title": topic["title"],
                "category": topic["category"],
                "source_tags": [topic_id],
                "rationale": "identity mapping (no plan agent)",
                "search_focus": resolve_search_focus(topic_id, [topic_id]),
            }
        )
    return {"pages": pages, "links": []}


def _normalize_plan(raw: dict[str, Any], topics: dict[str, Any]) -> dict[str, Any]:
    known_tags = set(topics.keys())
    pages_out: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for page in raw.get("pages") or []:
        if not isinstance(page, dict):
            continue
        page_id = str(page.get("id") or "").strip()
        if not page_id or page_id in seen_ids:
            continue
        source_tags = [
            str(t).strip()
            for t in (page.get("source_tags") or [])
            if isinstance(t, str) and str(t).strip() in known_tags
        ]
        if not source_tags:
            continue
        title = str(page.get("title") or topics[source_tags[0]]["title"]).strip()
        category = str(page.get("category") or topics[source_tags[0]]["category"]).strip()
        if category not in CATEGORIES:
            category = topics[source_tags[0]]["category"]
        pages_out.append(
            {
                "id": page_id,
                "title": title,
                "category": category,
                "source_tags": source_tags,
                "rationale": str(page.get("rationale") or ""),
                "search_focus": resolve_search_focus(
                    page_id,
                    source_tags,
                    llm_value=page.get("search_focus"),
                ),
            }
        )
        seen_ids.add(page_id)

    if not pages_out:
        return identity_plan(topics)

    valid_ids = {p["id"] for p in pages_out}
    links_out: list[dict[str, str]] = []
    for link in raw.get("links") or []:
        if not isinstance(link, dict):
            continue
        src = str(link.get("from") or "").strip()
        dst = str(link.get("to") or "").strip()
        if src in valid_ids and dst in valid_ids and src != dst:
            links_out.append(
                {
                    "from": src,
                    "to": dst,
                    "reason": str(link.get("reason") or ""),
                }
            )

    return {"pages": pages_out, "links": links_out}


def load_plan(
    path: Path | str,
    topics: dict[str, Any],
    *,
    min_claims: int = 1,
) -> dict[str, Any]:
    plan_path = Path(path)
    if not plan_path.exists():
        return identity_plan(topics, min_claims=min_claims)
    with plan_path.open(encoding="utf-8") as f:
        raw = json.load(f)
    return _normalize_plan(raw, topics)


def page_titles(plan: dict[str, Any]) -> dict[str, str]:
    return {p["id"]: p["title"] for p in plan.get("pages", [])}


def links_from_plan(plan: dict[str, Any], page_id: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = {page_id}
    for link in plan.get("links") or []:
        if link.get("from") == page_id:
            target = link.get("to")
            if target and target not in seen:
                out.append(target)
                seen.add(target)
    return out


def run(
    aggregated_path: Path | str = DEFAULT_AGGREGATED_PATH,
    output_path: Path | str = DEFAULT_OUTPUT_PATH,
    llm: LLMClient | None = None,
    min_claims: int = 1,
    use_batch: bool = False,
    *,
    skip_agent: bool = False,
) -> dict[str, Any]:
    agg_path = Path(aggregated_path) if aggregated_path is not None else resolve_aggregated_path()
    with agg_path.open(encoding="utf-8") as f:
        topics = json.load(f)["topics"]

    if skip_agent:
        plan = identity_plan(topics, min_claims=min_claims)
        write_json_file(plan, Path(output_path))
        return {
            "page_count": len(plan["pages"]),
            "link_count": 0,
            "output_path": str(output_path),
            "mode": "identity",
        }

    llm = llm or LLMClient()
    prompt = build_plan_prompt(topics)

    if use_batch and llm.supports_batch():
        results = llm.complete_batch(
            [BatchRequest(request_id="plan", system=PLAN_SYSTEM, user=prompt, task="plan")]
        )
        raw_text = results.get("plan", "{}")
    else:
        raw_text = llm.complete_text(PLAN_SYSTEM, prompt, task="plan")

    try:
        raw = json.loads(raw_text) if raw_text.strip().startswith("{") else {}
        if not raw:
            from utils.llm_client import extract_json

            raw = extract_json(raw_text)
    except (json.JSONDecodeError, ValueError):
        raw = {}

    plan = _normalize_plan(raw if isinstance(raw, dict) else {}, topics)
    write_json_file(plan, Path(output_path))

    return {
        "page_count": len(plan["pages"]),
        "link_count": len(plan.get("links") or []),
        "output_path": str(output_path),
        "mode": "agent",
        "provider": llm.provider,
        "model": llm.model,
        "batch_mode": use_batch and llm.supports_batch(),
    }


def pages_by_category(plan: dict[str, Any]) -> dict[str, list[tuple[str, str]]]:
    grouped: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for page in plan.get("pages") or []:
        cat = page.get("category", "emergent")
        grouped[category_title(cat)].append((page["id"], page["title"]))
    return dict(grouped)


if __name__ == "__main__":
    run(
        llm=LLMClient.for_stage(stage='plan', use_hybrid_defaults=True),
        use_batch=True
    )