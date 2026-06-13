"""Stage D: generate Hebrew wiki page drafts from aggregated claims.

For each topic the LLM writes neutral Hebrew prose that states the consensus,
presents contradicting opinions with support counts, dates the points and
cross-links related pages. A deterministic, data-derived section (extracted
claims, timeline, related pages, draft footer) is always appended so every
page is verifiable during review and useful even without an LLM.

Drafts are written to ``drafts/<slug>.md`` for human review; approved files are
moved to ``docs/`` before publishing.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from wiki_build.llm_client import BatchRequest, LLMClient
from wiki_build.taxonomy import all_pages, get_page

DEFAULT_AGGREGATED_PATH = Path("data/claims_aggregated.json")
DEFAULT_DRAFTS_DIR = Path("drafts")

STANCE_HE = {
    "positive": "חיובי",
    "negative": "שלילי",
    "neutral": "ניטרלי",
    "factual": "עובדתי",
}

GENERATE_SYSTEM = (
    "אתה כותב ערכי ויקי בעברית על פונדקאות לגייז, על בסיס ידע שחולץ מקבוצת וואטסאפ. "
    "כתוב בעברית רהוטה, ניטרלית וברורה. הצג קודם את ההסכמה הרווחת, ואז דעות מנוגדות "
    "תוך ציון מספר התומכים הייחודיים (משתתפים שכתבו או הגיבו בתגובות — כל אחד נספר פעם אחת). "
    "כשטענה חוזרת במספר שיחות, ציין כמה פעמים היא הופיעה. "
    "תגובות אימוג'י משקפות תמיכה נוספת — העדף טענות עם יותר תומכים ותגובות. "
    "ציין תאריכים (חודש ושנה) כשרלוונטי. "
    "השתמש בקישורים שסופקו כדי לקשר לעמודים קשורים. אל תמציא מידע שאינו בטענות. "
    "שמור על אנונימיות מוחלטת - בלי שמות אנשים. החזר Markdown בלבד, ללא כותרת H1."
)


def _format_reaction_summary(reaction_summary: list[dict[str, Any]]) -> str:
    parts = [
        f"{item['emoji']}×{item['count']}"
        for item in (reaction_summary or [])
        if item.get("emoji")
    ]
    return ", ".join(parts)


def _format_support_line(claim: dict[str, Any]) -> str:
    """Human-readable support stats for prompts and deterministic sections."""

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


def _related_pages(topic_id: str, topics: dict[str, Any]) -> list[str]:
    page = get_page(topic_id)
    related: list[str] = []
    seen: set[str] = {topic_id}

    def add(pid: str) -> None:
        if pid in topics and pid not in seen:
            related.append(pid)
            seen.add(pid)

    if page:
        if page.parent:
            add(page.parent)
        for other in all_pages():
            if other.parent == topic_id:
                add(other.id)
        for other in all_pages():
            if other.category == page.category:
                add(other.id)

    # Entity-shared topics.
    entities = {
        e for claim in topics[topic_id]["merged_claims"] for e in claim["entities"]
    }
    if entities:
        for other_id, other in topics.items():
            if other_id in seen:
                continue
            other_entities = {
                e for claim in other["merged_claims"] for e in claim["entities"]
            }
            if entities & other_entities:
                add(other_id)

    return related[:8]


def _build_facts_block(topic: dict[str, Any]) -> str:
    lines: list[str] = ["טענות שחולצו (לפי מספר תומכים):"]
    for claim in topic["merged_claims"][:25]:
        date_range = claim["date_range"]
        if date_range[0] and date_range[0] != date_range[1]:
            when = f"{date_range[0]} עד {date_range[1]}"
        else:
            when = date_range[0] or "לא ידוע"
        stance = STANCE_HE.get(claim["stance"], claim["stance"])
        support_line = _format_support_line(claim)
        lines.append(
            f"- [{stance}, {support_line}, {when}] {claim['claim_text']}"
        )
    if topic["contradictions"]:
        lines.append("\nדעות מנוגדות לפי ישות:")
        for c in topic["contradictions"]:
            lines.append(
                f"- {c['entity']}: חיובי {c['positive']} / שלילי {c['negative']}"
            )
    return "\n".join(lines)


def build_generate_prompt(
    topic_id: str, topic: dict[str, Any], topics: dict[str, Any]
) -> str:
    related = _related_pages(topic_id, topics)
    links = "\n".join(
        f"- [{topics[r]['title']}]({r}.md)" for r in related
    ) or "- (אין)"
    return (
        f"כתוב את תוכן עמוד הויקי לנושא: {topic['title']}.\n\n"
        f"{_build_facts_block(topic)}\n\n"
        "עמודים קשורים שניתן לקשר אליהם (השתמש בנתיב המדויק):\n"
        f"{links}\n\n"
        "כתוב את גוף העמוד ב-Markdown (ללא כותרת H1)."
    )


def _deterministic_sections(
    topic_id: str, topic: dict[str, Any], topics: dict[str, Any]
) -> str:
    parts: list[str] = []

    parts.append("## טענות מרכזיות שחולצו\n")
    for claim in topic["merged_claims"][:30]:
        date_range = claim["date_range"]
        when = (
            f"{date_range[0]}–{date_range[1]}"
            if date_range[0] and date_range[0] != date_range[1]
            else (date_range[0] or "לא ידוע")
        )
        stance = STANCE_HE.get(claim["stance"], claim["stance"])
        support_line = _format_support_line(claim)
        parts.append(f"- **[{stance} · {support_line} · {when}]** {claim['claim_text']}")

    if topic["contradictions"]:
        parts.append("\n## דעות מנוגדות\n")
        for c in topic["contradictions"]:
            parts.append(
                f"- **{c['entity']}**: {c['positive']} בעד / {c['negative']} נגד"
            )

    if topic["timeline"]:
        parts.append("\n## ציר זמן (אזכורים לפי חודש)\n")
        for month, count in topic["timeline"].items():
            parts.append(f"- {month}: {count}")

    related = _related_pages(topic_id, topics)
    if related:
        parts.append("\n## עמודים קשורים\n")
        for r in related:
            parts.append(f"- [{topics[r]['title']}]({r}.md)")

    return "\n".join(parts)


def _narrative_from_text(raw: str) -> str:
    if not raw:
        return "_שגיאה בייצור תוכן: empty response_"
    return raw.strip()


def generate_page(
    topic_id: str,
    topic: dict[str, Any],
    topics: dict[str, Any],
    llm: LLMClient,
    *,
    narrative: str | None = None,
) -> str:
    if narrative is None:
        prompt = build_generate_prompt(topic_id, topic, topics)
        try:
            narrative = llm.complete_text(GENERATE_SYSTEM, prompt, task="generate").strip()
        except Exception as exc:  # noqa: BLE001
            narrative = f"_שגיאה בייצור תוכן: {exc}_"
    else:
        narrative = _narrative_from_text(narrative)

    date_range = topic["date_range"]
    range_str = (
        f"{date_range[0]} עד {date_range[1]}"
        if date_range[0]
        else "לא ידוע"
    )

    header = (
        f"# {topic['title']}\n\n"
        '!!! warning "טיוטה - דורש בדיקה אנושית לפני פרסום"\n'
        "    עמוד זה נוצר אוטומטית מתוך שיחות הקבוצה. יש לוודא דיוק ואנונימיות לפני פרסום.\n\n"
    )
    footer = (
        "\n\n---\n\n"
        f"*מבוסס על {topic['claim_count']} טענות מתוך שיחות הקבוצה. "
        f"טווח הנתונים: {range_str}. "
        "המידע משקף דעות חברי הקבוצה ואינו ייעוץ מקצועי.*\n"
    )
    return header + narrative + "\n\n" + _deterministic_sections(topic_id, topic, topics) + footer


def _generate_index(topics: dict[str, Any]) -> str:
    by_category: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for topic_id, topic in topics.items():
        by_category[topic["category_title"]].append((topic_id, topic["title"]))

    parts = [
        "# ויקי פונדקאות לגייז\n",
        '!!! info "אודות"\n'
        "    ויקי זה מסכם ידע שנאסף בקבוצת וואטסאפ קהילתית. כל המידע אנונימי "
        "ומשקף דעות של חברי הקבוצה - אינו תחליף לייעוץ מקצועי.\n",
    ]
    for category, pages in by_category.items():
        parts.append(f"\n## {category}\n")
        for topic_id, title in sorted(pages, key=lambda p: p[1]):
            parts.append(f"- [{title}]({topic_id}.md)")
    return "\n".join(parts) + "\n"


def run(
    aggregated_path: Path | str = DEFAULT_AGGREGATED_PATH,
    drafts_dir: Path | str = DEFAULT_DRAFTS_DIR,
    llm: LLMClient | None = None,
    min_claims: int = 1,
    use_batch: bool = False,
) -> dict[str, Any]:
    llm = llm or LLMClient()
    with Path(aggregated_path).open(encoding="utf-8") as f:
        topics = json.load(f)["topics"]

    drafts = Path(drafts_dir)
    drafts.mkdir(parents=True, exist_ok=True)

    written: list[str] = []
    pending: list[tuple[str, dict[str, Any], str]] = []
    for topic_id, topic in topics.items():
        if topic["claim_count"] < min_claims:
            continue
        pending.append((topic_id, topic, build_generate_prompt(topic_id, topic, topics)))

    narratives: dict[str, str] = {}
    if pending:
        if use_batch and llm.supports_batch():
            print(f"  Generate: submitting {len(pending)} requests via batch API...")
            narratives = llm.complete_batch(
                [
                    BatchRequest(
                        request_id=topic_id,
                        system=GENERATE_SYSTEM,
                        user=prompt,
                        task="generate",
                    )
                    for topic_id, _, prompt in pending
                ]
            )
        else:
            if use_batch:
                print("  Generate: batch not supported for this provider; using sync API.")
            for topic_id, topic, prompt in pending:
                try:
                    narratives[topic_id] = llm.complete_text(
                        GENERATE_SYSTEM, prompt, task="generate"
                    )
                except Exception as exc:  # noqa: BLE001
                    narratives[topic_id] = f"_שגיאה בייצור תוכן: {exc}_"

    for topic_id, topic, _ in pending:
        markdown = generate_page(
            topic_id, topic, topics, llm, narrative=narratives.get(topic_id, "")
        )
        (drafts / f"{topic_id}.md").write_text(markdown, encoding="utf-8")
        written.append(topic_id)

    (drafts / "index.md").write_text(_generate_index(topics), encoding="utf-8")

    return {
        "pages_written": len(written),
        "drafts_dir": str(drafts),
        "provider": llm.provider,
        "model": llm.model,
        "batch_mode": use_batch and llm.supports_batch(),
    }


if __name__ == "__main__":
    meta = run(use_batch="--batch" in sys.argv)
    print(f"Wrote {meta['pages_written']} draft pages to {meta['drafts_dir']}/")
