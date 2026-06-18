"""Stage D: generate Hebrew wiki page drafts from aggregated claims + plan.

Each page has four sections:

1. רקע כללי — public background via Gemini Google Search grounding
2. מידע מהקהילה — synthesized insights/opinions with supporter counts
3. מקורות — external citation list
4. עמודים קשורים — links from the planning agent

Drafts are written to ``drafts/<slug>.md`` for human review.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any

from wiki_build.llm_client import (
    BatchRequest,
    GroundedCitation,
    GroundedResult,
    LLMClient,
    web_search_enabled,
)
from wiki_build.plan import (
    DEFAULT_OUTPUT_PATH as DEFAULT_PLAN_PATH,
    identity_plan,
    links_from_plan,
    load_plan,
    page_titles,
    pages_by_category,
)
from wiki_build.scrub import summarize_redactions
from wiki_build.rtl import wrap_rtl_markdown
from wiki_build.taxonomy import all_pages, get_page

DEFAULT_AGGREGATED_PATH = Path("data/claims_aggregated.json")
DEFAULT_DRAFTS_DIR = Path("drafts")

STANCE_HE = {
    "positive": "חיובי",
    "negative": "שלילי",
    "neutral": "ניטרלי",
    "factual": "עובדתי",
}

GENERATE_BACKGROUND_SYSTEM = (
    "אתה כותב רקע כללי בעברית לערך ויקי על פונדקאות לגייז. "
    "חפש מידע ציבורי עדכני באינטרנט. כתוב בניסוח ניטרלי ועובדתי. "
    "אל תכלול דעות או חוויות של קבוצות וואטסאפ. "
    "אל תמציא עובדות — הסתמך על תוצאות החיפוש. "
    "החזר Markdown בלבד (ללא כותרת H1/H2)."
)

GENERATE_COMMUNITY_SYSTEM = (
    "אתה כותב את סעיף 'מידע מהקהילה' לערך ויקי בעברית על פונדקאות לגייז. "
    "המידע מגיע אך ורק מטענות שחולצו מקבוצת וואטסאפ — אל תוסיף ידע חיצוני. "
    "כתוב בצורת רשימה bullets. "
    "הצג קודם את ההסכמה הרווחת, ואז דעות מנוגדות בפורמט: "
    "**בעד:** N תומכים · **נגד:** M תומכים. "
    "ציין מספר תומכים ייחודיים לכל נקודה משמעותית. "
    "כלול תאריכים (חודש ושנה) כשזמינים. שמור על אנונימיות מוחלטת של חברי הקבוצה אך שתף בפרטי קשר של ספקים או בעלי מקצוע. "
    "החזר Markdown בלבד (ללא כותרת H1/H2)."
)


def _format_reaction_summary(reaction_summary: list[dict[str, Any]]) -> str:
    parts = [
        f"{item['emoji']}×{item['count']}"
        for item in (reaction_summary or [])
        if item.get("emoji")
    ]
    return ", ".join(parts)


def _format_support_line(claim: dict[str, Any]) -> str:
    support = claim.get("support_count", 1)
    parts = [f"{support} תומכים ייחודיים"]
    reaction_only = claim.get("reaction_only_count", 0)
    if reaction_only:
        parts.append(f"{reaction_only} מתוכם רק בתגובות")
    reaction_summary = _format_reaction_summary(claim.get("reaction_summary"))
    if reaction_summary:
        parts.append(f"תגובות: {reaction_summary}")
    endorsement_count = claim.get("endorsement_count", 1)
    thread_count = claim.get("thread_count", 1)
    if endorsement_count > 1:
        parts.append(f"הופיעה {endorsement_count} פעמים")
    if thread_count > 1 and thread_count != endorsement_count:
        parts.append(f"ב-{thread_count} שיחות")
    return " · ".join(parts)


def merge_topic_from_tags(
    source_tags: list[str], topics: dict[str, Any]
) -> dict[str, Any]:
    """Combine aggregated buckets referenced by a plan page."""

    merged_claims: list[dict[str, Any]] = []
    claim_count = 0
    all_dates: list[str] = []
    timeline: Counter[str] = Counter()
    contradiction_map: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    title = ""
    category = "emergent"

    for tag in source_tags:
        topic = topics.get(tag)
        if not topic:
            continue
        if not title:
            title = topic["title"]
            category = topic["category"]
        claim_count += topic["claim_count"]
        merged_claims.extend(topic["merged_claims"])
        all_dates.extend(
            d for c in topic["merged_claims"] for d in c.get("date_range", []) if d
        )
        for month, count in topic.get("timeline", {}).items():
            timeline[month] += count
        for c in topic.get("contradictions", []):
            entity = c["entity"]
            contradiction_map[entity]["positive"] += c.get("positive", 0)
            contradiction_map[entity]["negative"] += c.get("negative", 0)

    merged_claims.sort(key=lambda c: c.get("support_count", 0), reverse=True)

    contradictions: list[dict[str, Any]] = []
    for entity, stances in contradiction_map.items():
        pos = stances.get("positive", 0)
        neg = stances.get("negative", 0)
        if pos > 0 and neg > 0:
            contradictions.append({"entity": entity, "positive": pos, "negative": neg})
    contradictions.sort(key=lambda d: d["positive"] + d["negative"], reverse=True)

    all_dates_sorted = sorted(set(all_dates))
    return {
        "title": title,
        "category": category,
        "claim_count": claim_count,
        "merged_claims": merged_claims,
        "contradictions": contradictions,
        "timeline": dict(sorted(timeline.items())),
        "date_range": (
            [all_dates_sorted[0], all_dates_sorted[-1]] if all_dates_sorted else [None, None]
        ),
    }


def _related_pages_fallback(
    page_id: str,
    source_tags: list[str],
    topics: dict[str, Any],
    all_page_ids: set[str],
) -> list[str]:
    """Heuristic cross-links when the plan agent did not specify any."""

    related: list[str] = []
    seen: set[str] = {page_id}

    def add(pid: str) -> None:
        if pid in all_page_ids and pid not in seen:
            related.append(pid)
            seen.add(pid)

    page = get_page(page_id)
    if page:
        if page.parent:
            add(page.parent)
        for other in all_pages():
            if other.parent == page_id:
                add(other.id)
            if other.category == page.category:
                add(other.id)

    entities: set[str] = set()
    for tag in source_tags:
        topic = topics.get(tag)
        if not topic:
            continue
        for claim in topic["merged_claims"]:
            entities.update(claim.get("entities", []))

    if entities:
        for other_id in all_page_ids:
            if other_id in seen:
                continue
            other_topic = topics.get(other_id)
            if not other_topic:
                continue
            other_entities = {
                e for c in other_topic["merged_claims"] for e in c.get("entities", [])
            }
            if entities & other_entities:
                add(other_id)

    return related


def _build_community_facts_block(merged: dict[str, Any]) -> str:
    lines: list[str] = ["טענות ותובנות שחולצו (לפי מספר תומכים):"]
    for claim in merged["merged_claims"][:25]:
        date_range = claim.get("date_range") or [None, None]
        if date_range[0] and date_range[0] != date_range[1]:
            when = f"{date_range[0]} עד {date_range[1]}"
        else:
            when = date_range[0] or "לא ידוע"
        stance = STANCE_HE.get(claim.get("stance", ""), claim.get("stance", ""))
        support_line = _format_support_line(claim)
        lines.append(f"- [{stance}, {support_line}, {when}] {claim['claim_text']}")

    if merged["contradictions"]:
        lines.append("\nדעות מנוגדות לפי ישות (מספר תומכים):")
        for c in merged["contradictions"]:
            lines.append(
                f"- {c['entity']}: בעד {c['positive']} / נגד {c['negative']}"
            )
    return "\n".join(lines)


def build_community_prompt(page_title: str, merged: dict[str, Any]) -> str:
    return (
        f"כתוב את סעיף 'מידע מהקהילה' לעמוד: {page_title}.\n\n"
        f"{_build_community_facts_block(merged)}\n\n"
        "כתוב בפרוזה בעברית. אל תכלול כותרת. "
        "הוסף קישורי markdown [טקסט](page-id.md) בתוך הטקסט לעמודים קשורים כשמוזכר נושא שיש לו עמוד ייעודי."
    )


def build_background_prompt(page_title: str, search_focus: str) -> str:
    return (
        f"כתוב רקע כללי קצר (2 פסקאות) בעברית על: {page_title}.\n"
        f"השתמש בחיפוש באינטרנט כדי לאסוף מידע ציבורי עדכני.\n"
        f"מיקוד החיפוש: {search_focus}\n"
        "אל תכלול דעות קהילתיות. אל תכלול כותרת."
    )


def _format_pii_review_section(merged: dict[str, Any]) -> str:
    review_claims = [
        claim
        for claim in merged.get("merged_claims", [])
        if claim.get("pii_needs_review")
    ]
    if not review_claims:
        return ""

    lines = [
        "## בדיקת פרטיות",
        "",
        "הטענות הבאות עברו הסרה אוטומטית של טלפון/אימייל ודורשות בדיקה ידנית לפני פרסום:",
        "",
    ]
    for claim in review_claims:
        support = _format_support_line(claim)
        date_range = claim.get("date_range") or [None, None]
        if date_range[0] and date_range[0] != date_range[1]:
            date_label = f"{date_range[0]}–{date_range[-1]}"
        elif date_range[0]:
            date_label = str(date_range[0])
        else:
            date_label = "לא ידוע"
        redaction_note = summarize_redactions(claim.get("pii_redactions") or [])
        note = f" _(הוסר: {redaction_note})_" if redaction_note else ""
        lines.append(f"- **[{support} · {date_label}]** {claim['claim_text']}{note}")
    return "\n".join(lines) + "\n\n"


def _format_background_section(result: GroundedResult) -> str:
    if not result.text.strip():
        return ""
    body = result.text.strip()
    return (
        "## רקע כללי\n\n"
        '!!! info "ממקורות חיצוניים"\n'
        "    המידע להלן נאסף ממקורות ציבוריים באינטרנט ואינו משקף דעות חברי הקבוצה.\n\n"
        f"{body}\n"
    )


def _format_community_section(text: str) -> str:
    body = text.strip() if text else "_לא נמצא מידע קהילתי לנושא זה._"
    return f"## מידע מהקהילה\n\n{body}\n"


def _format_sources_section(citations: tuple[GroundedCitation, ...]) -> str:
    if not citations:
        return ""
    today = date.today().isoformat()
    lines = ["## מקורות", ""]
    for cite in citations:
        title = cite.title or cite.url
        lines.append(f"- [{title}]({cite.url}) — נבדק ב-{today}")
    return "\n".join(lines) + "\n"


def _format_links_section(
    page_id: str,
    plan: dict[str, Any],
    topics: dict[str, Any],
    titles: dict[str, str],
    source_tags: list[str],
) -> str:
    all_page_ids = {p["id"] for p in plan.get("pages", [])}
    related = links_from_plan(plan, page_id)
    if not related:
        related = _related_pages_fallback(page_id, source_tags, topics, all_page_ids)
    if not related:
        return ""
    lines = ["## עמודים קשורים", ""]
    for target in related:
        title = titles.get(target, topics.get(target, {}).get("title", target))
        lines.append(f"- [{title}]({target}.md)")
    return "\n".join(lines) + "\n"


def _footer_disclaimer(merged: dict[str, Any]) -> str:
    date_range = merged.get("date_range") or [None, None]
    if date_range[0] and date_range[0] != date_range[1]:
        range_str = f"{date_range[0]}–{date_range[1]}"
    elif date_range[0]:
        range_str = str(date_range[0])
    else:
        range_str = "לא ידוע"
    return (
        f"\n---\n\n"
        f"*מידע הקהילה מבוסס על {merged.get('claim_count', 0)} טענות "
        f"מתוך שיחות הקבוצה ({range_str}). אינו ייעוץ מקצועי.*\n"
    )


def generate_page(
    page: dict[str, Any],
    topics: dict[str, Any],
    plan: dict[str, Any],
    llm: LLMClient,
    research_llm: LLMClient | None,
    *,
    community_text: str | None = None,
    grounded: GroundedResult | None = None,
    enable_web_search: bool = True,
) -> str:
    page_id = page["id"]
    page_title = page.get("title") or page_id
    source_tags = page.get("source_tags") or [page_id]
    merged = merge_topic_from_tags(source_tags, topics)
    titles = page_titles(plan)

    # Background (section 1)
    background_md = ""
    citations: tuple[GroundedCitation, ...] = ()
    if enable_web_search and research_llm is not None:
        if grounded is None:
            try:
                grounded = research_llm.complete_grounded(
                    GENERATE_BACKGROUND_SYSTEM,
                    build_background_prompt(page_title, page.get("search_focus", page_title)),
                )
            except Exception as exc:  # noqa: BLE001
                grounded = GroundedResult(
                    text=f"_שגיאה בחיפוש רקע: {exc}_",
                    citations=(),
                )
        if grounded:
            background_md = _format_background_section(grounded)
            citations = grounded.citations

    # Community (section 2)
    if community_text is None:
        try:
            community_text = llm.complete_text(
                GENERATE_COMMUNITY_SYSTEM,
                build_community_prompt(page_title, merged),
                task="community",
            ).strip()
        except Exception as exc:  # noqa: BLE001
            community_text = f"_שגיאה בייצור תוכן קהילתי: {exc}_"

    header = (
        f"# {page_title}\n\n"
        '!!! warning "טיוטה - דורש בדיקה אנושית לפני פרסום"\n'
        "    עמוד זה נוצר אוטומטית. יש לוודא דיוק ואנונימיות לפני פרסום.\n\n"
    )

    parts = [
        header,
        _format_pii_review_section(merged),
        background_md,
        _format_community_section(community_text or ""),
        _format_sources_section(citations),
        _format_links_section(page_id, plan, topics, titles, source_tags),
        _footer_disclaimer(merged),
    ]
    return wrap_rtl_markdown("".join(p for p in parts if p))


def _generate_index(plan: dict[str, Any]) -> str:
    grouped = pages_by_category(plan)
    parts = [
        "# ויקי פונדקאות לגייז\n",
        '!!! info "אודות"\n'
        "    ויקי זה מסכם ידע שנאסף בקבוצת וואטסאפ קהילתית. כל המידע אנונימי "
        "ומשקף דעות של חברי הקבוצה - אינו תחליף לייעוץ מקצועי.\n",
    ]
    for category, pages in grouped.items():
        parts.append(f"\n## {category}\n")
        for page_id, title in sorted(pages, key=lambda p: p[1]):
            parts.append(f"- [{title}]({page_id}.md)")
    return wrap_rtl_markdown("\n".join(parts))


def run(
    aggregated_path: Path | str = DEFAULT_AGGREGATED_PATH,
    plan_path: Path | str = DEFAULT_PLAN_PATH,
    drafts_dir: Path | str = DEFAULT_DRAFTS_DIR,
    llm: LLMClient | None = None,
    research_llm: LLMClient | None = None,
    min_claims: int = 1,
    use_batch: bool = False,
    *,
    skip_plan: bool = False,
    enable_web_search: bool | None = None,
) -> dict[str, Any]:
    llm = llm or LLMClient()
    search_on = web_search_enabled(explicit=enable_web_search)
    if search_on:
        research_llm = research_llm or LLMClient.for_stage("research")

    with Path(aggregated_path).open(encoding="utf-8") as f:
        topics = json.load(f)["topics"]

    if skip_plan or not Path(plan_path).exists():
        plan = identity_plan(topics, min_claims=min_claims)
    else:
        plan = load_plan(plan_path, topics, min_claims=min_claims)

    drafts = Path(drafts_dir)
    drafts.mkdir(parents=True, exist_ok=True)

    pages = [
        p
        for p in plan.get("pages", [])
        if merge_topic_from_tags(p.get("source_tags") or [p["id"]], topics)["claim_count"]
        >= min_claims
    ]

    community_texts: dict[str, str] = {}
    pending_community: list[tuple[str, dict[str, Any], str]] = []
    for page in pages:
        merged = merge_topic_from_tags(page.get("source_tags") or [page["id"]], topics)
        pending_community.append(
            (page["id"], page, build_community_prompt(page.get("title", page["id"]), merged))
        )

    if pending_community:
        if use_batch and llm.supports_batch():
            print(f"  Generate: submitting {len(pending_community)} community requests via batch...")
            community_texts = llm.complete_batch(
                [
                    BatchRequest(
                        request_id=page_id,
                        system=GENERATE_COMMUNITY_SYSTEM,
                        user=prompt,
                        task="community",
                    )
                    for page_id, _, prompt in pending_community
                ]
            )
        else:
            if use_batch:
                print("  Generate: batch not supported for this provider; using sync API.")
            for page_id, _, prompt in pending_community:
                try:
                    community_texts[page_id] = llm.complete_text(
                        GENERATE_COMMUNITY_SYSTEM, prompt, task="community"
                    )
                except Exception as exc:  # noqa: BLE001
                    community_texts[page_id] = f"_שגיאה בייצור תוכן: {exc}_"

    written: list[str] = []
    for page in pages:
        page_id = page["id"]
        markdown = generate_page(
            page,
            topics,
            plan,
            llm,
            research_llm if search_on else None,
            community_text=community_texts.get(page_id),
            enable_web_search=search_on,
        )
        (drafts / f"{page_id}.md").write_text(markdown, encoding="utf-8")
        written.append(page_id)

    (drafts / "index.md").write_text(_generate_index(plan), encoding="utf-8")

    return {
        "pages_written": len(written),
        "drafts_dir": str(drafts),
        "provider": llm.provider,
        "model": llm.model,
        "research_provider": research_llm.provider if search_on and research_llm else None,
        "research_model": research_llm.model if search_on and research_llm else None,
        "web_search": search_on,
        "batch_mode": use_batch and llm.supports_batch(),
    }


if __name__ == "__main__":
    meta = run(
        llm=LLMClient.for_stage("generate", use_hybrid_defaults=True),
        research_llm=LLMClient.for_stage("research", use_hybrid_defaults=True),
        use_batch=True,
    )
    print(f"Wrote {meta['pages_written']} draft pages to {meta['drafts_dir']}/")
