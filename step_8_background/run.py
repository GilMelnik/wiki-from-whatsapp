"""Step 8: research a public "background" section per page and assemble drafts.

This is the open-source overview counterpart to the community agent (step 7).
For every page in the community page store (``data/wiki_pages.json``) it runs a
Gemini Google-Search grounded query for neutral public background, then renders
the final ``drafts/<id>.md`` by combining:

1. רקע כללי — grounded public background (+ מקורות citations)
2. מידע מהקהילה — rendered from the structured store (supporter counts only)
3. עמודים קשורים — links/related pages from the store

The community section is the source of truth produced in step 7; this step never
re-derives supporter counts, it only adds public context and assembles markdown.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

from step_3_extract.scrub import (
    FORBIDDEN_TERM_INSTRUCTION,
    correct_surrogate_terminology,
)
from step_7_community.store import PageStore
from utils.llm_client import (
    GroundedCitation,
    GroundedResult,
    LLMClient,
    web_search_enabled,
)
from utils.paths import resolve_claims_path, resolve_wiki_pages_path
from utils.rtl import wrap_rtl_markdown
from utils.taxonomy import category_title, resolve_search_focus

DEFAULT_DRAFTS_DIR = Path("drafts")

GENERATE_BACKGROUND_SYSTEM = (
    "אתה עורך ויקי הכותב סעיף 'רקע כללי' בעברית לערך על פונדקאות לגייז. "
    "משימתך: לסכם מידע ציבורי עובדתי בלבד, על סמך תוצאות חיפוש עדכניות באינטרנט.\n"
    "כללים:\n"
    "1. הסתמך אך ורק על תוצאות החיפוש; אל תמציא עובדות, מספרים, שמות או מקורות.\n"
    "2. כתוב בניסוח ניטרלי ועובדתי, בלשון אחידה, ללא פנייה לקורא.\n"
    "3. אורך: שתיים-שלוש פסקאות קצרות לכל היותר.\n"
    "4. החזר Markdown בלבד, ללא כותרות (ללא H1/H2) וללא רשימת מקורות.\n"
    f"5. {FORBIDDEN_TERM_INSTRUCTION}"
)


def build_background_prompt(page_title: str, search_focus: str) -> str:
    return (
        f'כתוב רקע כללי עובדתי וקצר (שתיים-שלוש פסקאות לכל היותר) בעברית על: "{page_title}".\n'
        f"בצע חיפוש באינטרנט כדי לאסוף מידע ציבורי עדכני, והסתמך אך ורק עליו.\n"
        f"מיקוד החיפוש: {search_focus}\n"
        "אל תכלול דעות או חוויות קהילתיות, ואל תכלול כותרת או רשימת מקורות."
    )


def _format_background_section(result: GroundedResult) -> str:
    if not result.text.strip():
        return ""
    return (
        "## רקע כללי\n\n"
        '!!! info "ממקורות חיצוניים"\n'
        "    המידע להלן נאסף ממקורות ציבוריים באינטרנט ואינו משקף דעות חברי הקבוצה.\n\n"
        f"{result.text.strip()}\n"
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


def _related_ids(store: PageStore, page_id: str) -> list[str]:
    related: list[str] = []
    for link in store.links:
        if link.get("from") == page_id and link.get("to") in store.pages:
            target = link["to"]
            if target != page_id and target not in related:
                related.append(target)
    for target in store.pages.get(page_id, {}).get("related_pages", []):
        if target in store.pages and target != page_id and target not in related:
            related.append(target)
    return related


def _format_links_section(store: PageStore, page_id: str) -> str:
    related = _related_ids(store, page_id)
    if not related:
        return ""
    lines = ["## עמודים קשורים", ""]
    for target in related:
        title = store.pages[target].get("title", target)
        lines.append(f"- [{title}]({target}.md)")
    return "\n".join(lines) + "\n"


def _page_claim_ids(page: dict[str, Any]) -> list[str]:
    seen: list[str] = []
    for sec in page.get("sections", []):
        for st in sec.get("statements", []):
            for cid in st.get("claim_ids", []):
                if cid not in seen:
                    seen.append(cid)
    return seen


def _footer(claim_ids: list[str], claims_by_id: dict[str, dict[str, Any]]) -> str:
    dates = sorted(
        {
            claims_by_id[cid]["date"]
            for cid in claim_ids
            if cid in claims_by_id and claims_by_id[cid].get("date")
        }
    )
    if dates and dates[0] != dates[-1]:
        range_str = f"{dates[0]}–{dates[-1]}"
    elif dates:
        range_str = dates[0]
    else:
        range_str = "לא ידוע"
    return (
        f"\n---\n\n"
        f"*מידע הקהילה מבוסס על {len(claim_ids)} טענות מתוך שיחות הקבוצה "
        f"({range_str}). אינו ייעוץ מקצועי.*\n"
    )


def _grounded_for_page(
    research_llm: LLMClient | None,
    page: dict[str, Any],
    enable_web_search: bool,
) -> GroundedResult | None:
    if not enable_web_search or research_llm is None:
        return None
    search_focus = resolve_search_focus(
        page["id"], page.get("source_tags") or [page["id"]]
    )
    if not search_focus:
        search_focus = page.get("title", page["id"])
    try:
        return research_llm.complete_grounded(
            GENERATE_BACKGROUND_SYSTEM,
            build_background_prompt(page.get("title", page["id"]), search_focus),
        )
    except Exception as exc:  # noqa: BLE001
        return GroundedResult(text=f"_שגיאה בחיפוש רקע: {exc}_", citations=())


def assemble_page(
    store: PageStore,
    page_id: str,
    claims_by_id: dict[str, dict[str, Any]],
    grounded: GroundedResult | None,
) -> str:
    page = store.pages[page_id]
    title = page.get("title", page_id)
    header = (
        f"# {title}\n\n"
        '!!! warning "טיוטה - דורש בדיקה אנושית לפני פרסום"\n'
        "    עמוד זה נוצר אוטומטית. יש לוודא דיוק ואנונימיות לפני פרסום.\n\n"
    )
    background_md = _format_background_section(grounded) if grounded else ""
    citations = grounded.citations if grounded else ()
    parts = [
        header,
        background_md,
        _format_community_section(store.render_community(page_id)),
        _format_sources_section(citations),
        _format_links_section(store, page_id),
        _footer(_page_claim_ids(page), claims_by_id),
    ]
    return wrap_rtl_markdown(correct_surrogate_terminology("".join(p for p in parts if p)))


def _generate_index(store: PageStore) -> str:
    grouped: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for pid, page in store.pages.items():
        if not page.get("sections"):
            continue
        grouped[category_title(page.get("category", "emergent"))].append(
            (pid, page.get("title", pid))
        )
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
    return wrap_rtl_markdown(correct_surrogate_terminology("\n".join(parts)))


def run(
    *,
    pages_path: Path | str | None = None,
    claims_path: Path | str | None = None,
    drafts_dir: Path | str = DEFAULT_DRAFTS_DIR,
    research_llm: LLMClient | None = None,
    enable_web_search: bool | None = None,
) -> dict[str, Any]:
    pages_file = (
        Path(pages_path) if pages_path is not None else resolve_wiki_pages_path()
    )
    with pages_file.open(encoding="utf-8") as f:
        store = PageStore.from_payload(json.load(f))

    claims_file = Path(claims_path) if claims_path is not None else resolve_claims_path()
    if claims_file.exists():
        with claims_file.open(encoding="utf-8") as f:
            claims_by_id = {c["claim_id"]: c for c in json.load(f)["claims"]}
    else:
        claims_by_id = {}

    search_on = web_search_enabled(explicit=enable_web_search)
    if search_on and research_llm is None:
        research_llm = LLMClient.for_stage("research")

    drafts = Path(drafts_dir)
    drafts.mkdir(parents=True, exist_ok=True)

    written: list[str] = []
    for page_id, page in store.pages.items():
        if not page.get("sections"):
            continue  # skip empty seed pages with no community content
        grounded = _grounded_for_page(research_llm, page, search_on)
        markdown = assemble_page(store, page_id, claims_by_id, grounded)
        (drafts / f"{page_id}.md").write_text(markdown, encoding="utf-8")
        written.append(page_id)

    (drafts / "index.md").write_text(_generate_index(store), encoding="utf-8")

    return {
        "pages_written": len(written),
        "drafts_dir": str(drafts),
        "web_search": search_on,
        "research_provider": research_llm.provider if search_on and research_llm else None,
        "research_model": research_llm.model if search_on and research_llm else None,
    }


if __name__ == "__main__":
    run()
