"""Prompt + structured-output schema builders for the community wiki agent.

Kept separate from ``utils/llm_client/prompts.py`` (which is generic client
plumbing); this module holds the step_7-specific system prompt, the action
protocol JSON schema, and the per-batch prompt assembly.
"""

from __future__ import annotations

import json
from typing import Any

from step_3_extract.scrub import FORBIDDEN_TERM_INSTRUCTION
from step_5_aggregate.resolver import EntityResolver
from step_7_community.store import STANCE_HE, PageStore
from utils.llm_client import CacheSegment
from utils.support import engagement_for_claim

# The rules never change, so the system prompt is a stable (cached) prefix; the
# action structure is enforced separately via COMMUNITY_ACTIONS_SCHEMA (provider
# structured output). See build_batch_prompt for the cache layout.
COMMUNITY_AGENT_SYSTEM = (
    "אתה עורך ויקי בעברית הבונה את תוכן הקהילה על פונדקאות לגייז, על בסיס טענות "
    "שחולצו מקבוצת וואטסאפ. אתה עובד באופן הדרגתי: בכל פעם תקבל אצווה (batch) של "
    "טענות השייכות לעמוד מסוים, יחד עם התוכן הנוכחי של העמוד וקטלוג כל העמודים. "
    "כתוב או עדכן את העמוד הרלוונטי.\n"
    "כללים מחייבים:\n"
    "1. כל statement שאתה כותב חייב לכלול claim_ids — מזהי הטענות שעליהן הוא מתבסס. "
    "אל תכתוב דבר שאינו נתמך בטענה שסופקה.\n"
    "2. אם טענה חדשה תומכת בנקודה שכבר קיימת בעמוד — עדכן את ה-statement הקיים (ציין "
    "את ה-statement_id שלו) והוסף את ה-claim_id, במקום ליצור כפילות. כך נספרים תומכים "
    "ייחודיים ללא כפילויות.\n"
    "3. שמור על אנונימיות מלאה של חברי הקבוצה (ללא שמות פרטיים), אך מותר לציין שמות "
    "ספקים, בעלי מקצוע, סוכנויות ומדינות שהוזכרו.\n"
    "4. אל תכתוב מספרי תומכים בעצמך בתוך הטקסט — המערכת מחשבת אותם אוטומטית מתוך "
    "ה-claim_ids.\n"
    "5. כתוב פרוזה רהוטה ומחוברת בעברית. אם הטענות מתקבצות סביב תת-נושאים שונים — חלק "
    "אותן ל-section עם heading קצר.\n"
    "6. אתה רשאי להשתמש בכלים: new_page, split_page, add_link, set_related, read_page.\n"
    f"7. {FORBIDDEN_TERM_INSTRUCTION}\n"
    'החזר אך ורק אובייקט JSON יחיד ותקין עם השדה "actions" (מערך פעולות), '
    "ללא טקסט נוסף וללא code fence."
)


def _prop(base: dict[str, Any], description: str) -> dict[str, Any]:
    """A schema property (a shared type dict plus its Hebrew ``description``)."""

    return {**base, "description": description}


_STR: dict[str, Any] = {"type": "string"}
_STR_LIST: dict[str, Any] = {"type": "array", "items": {"type": "string"}}
_NULLABLE_STR: dict[str, Any] = {"type": ["string", "null"]}


def _action_variant(
    type_name: str, description: str, props: dict[str, Any]
) -> dict[str, Any]:
    """One closed, fully-required action object for the structured-output union.

    Structured outputs (Anthropic and Gemini) don't support ``const``, so the
    ``type`` tag is a single-value ``enum``; every field is required (nullable
    where optional). Field ``description``s carry the guidance that used to live
    in the prompt's prose schema.
    """

    properties = {"type": {"type": "string", "enum": [type_name]}, **props}
    return {
        "type": "object",
        "description": description,
        "additionalProperties": False,
        "properties": properties,
        "required": list(properties),
    }


# The single source of truth for the agent's action protocol: guides the model
# via its ``description`` fields and hard-constrains the output as provider
# structured output (Anthropic output_config / Gemini response_json_schema).
COMMUNITY_ACTIONS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "actions": {
            "type": "array",
            "description": "רשימת הפעולות לביצוע על קטלוג העמודים והתוכן.",
            "items": {
                "anyOf": [
                    _action_variant(
                        "upsert_statement",
                        "כתוב או עדכן statement; צירוף claim_ids ל-statement קיים "
                        "צובר תמיכה ללא כפילויות.",
                        {
                            "page_id": _prop(_STR, "מזהה העמוד שאליו שייך ה-statement."),
                            "section": _prop(
                                _STR, "כותרת תת-סעיף, או מחרוזת ריקה אם אין."
                            ),
                            "statement_id": _prop(
                                _NULLABLE_STR,
                                "מזהה statement קיים לעדכון, או null ליצירת חדש.",
                            ),
                            "text": _prop(_STR, "המשפט/משפטים בעברית."),
                            "claim_ids": _prop(
                                _STR_LIST, "מזהי הטענות שעליהן מתבסס ה-statement."
                            ),
                        },
                    ),
                    _action_variant(
                        "new_page",
                        "צור עמוד חדש בקטלוג.",
                        {
                            "id": _prop(_STR, "מזהה העמוד (slug באותיות לטיניות)."),
                            "title": _prop(_STR, "כותרת העמוד בעברית."),
                            "category": _prop(_STR, "מזהה הקטגוריה."),
                            "parent": _prop(
                                _NULLABLE_STR, "מזהה עמוד האב, או null אם אין."
                            ),
                        },
                    ),
                    _action_variant(
                        "split_page",
                        "פצל עמוד קיים למספר עמודים חדשים.",
                        {
                            "from": _prop(_STR, "מזהה העמוד לפיצול."),
                            "into": {
                                "type": "array",
                                "description": "העמודים החדשים שאליהם לפצל.",
                                "items": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "properties": {
                                        "id": _prop(_STR, "מזהה העמוד החדש (slug)."),
                                        "title": _prop(_STR, "כותרת העמוד החדש."),
                                        "category": _prop(_STR, "מזהה הקטגוריה."),
                                    },
                                    "required": ["id", "title", "category"],
                                },
                            },
                            "reason": _prop(_STR, "הסיבה לפיצול."),
                        },
                    ),
                    _action_variant(
                        "add_link",
                        "הוסף קישור בין שני עמודים.",
                        {
                            "from": _prop(_STR, "מזהה עמוד המקור."),
                            "to": _prop(_STR, "מזהה עמוד היעד."),
                            "reason": _prop(_STR, "הסיבה לקישור."),
                        },
                    ),
                    _action_variant(
                        "set_related",
                        "קבע את רשימת העמודים הקשורים לעמוד.",
                        {
                            "page_id": _prop(_STR, "מזהה העמוד."),
                            "related_pages": _prop(
                                _STR_LIST, "מזהי העמודים הקשורים."
                            ),
                        },
                    ),
                    _action_variant(
                        "read_page",
                        "בקש את תוכן עמוד אחר; הלולאה תריץ מחדש עם התוכן (מוגבל).",
                        {"id": _prop(_STR, "מזהה העמוד לקריאה.")},
                    ),
                ]
            },
        }
    },
    "required": ["actions"],
}


def _claim_line(
    claim: dict[str, Any],
    entities: list[str],
    audit_by_id: dict[str, dict[str, Any]] | None = None,
) -> str:
    stance = STANCE_HE.get(claim.get("stance", ""), claim.get("stance", ""))
    cid = claim["claim_id"]
    engagement = engagement_for_claim(cid, audit_by_id or {})
    bits = [
        f"claim_id: {cid}",
        f"stance: {stance}",
        f"תומכים: {engagement['supporter_count']}",
        f"מתנגדים: {engagement['opposer_count']}",
    ]
    if claim.get("date"):
        bits.append(f"תאריך: {claim['date']}")
    if entities:
        bits.append(f"ישויות: {', '.join(entities)}")
    return f"- {' | '.join(bits)}\n  טקסט: {claim.get('claim_text', '').strip()}"


def _claims_block(
    claims: list[dict[str, Any]],
    resolver: EntityResolver | None,
    audit_by_id: dict[str, dict[str, Any]] | None = None,
) -> str:
    lines = []
    for claim in claims:
        entities = (
            resolver.resolve_claim(claim) if resolver else (claim.get("entities") or [])
        )
        lines.append(_claim_line(claim, entities, audit_by_id))
    return "\n".join(lines)


def build_batch_prompt(
    store: PageStore,
    page_id: str,
    claims: list[dict[str, Any]],
    resolver: EntityResolver | None,
    extra_pages: str = "",
) -> list[CacheSegment]:
    """Order the prompt for prompt caching: almost-static catalog first (cached),
    then the per-call page content + claims (uncached). The output structure is
    enforced via COMMUNITY_ACTIONS_SCHEMA, so the cached prefix is system+catalog.
    """

    catalog = "\n".join(
        [
            "## קטלוג עמודים (id — כותרת)",
            store.catalog(exclude=None),
        ]
    )

    page_view = json.dumps(store.page_view(page_id), ensure_ascii=False, indent=2)
    sections = [
        f"## עמוד נוכחי: {page_id}",
        "התוכן הקיים של העמוד (JSON):",
        page_view,
        "",
        "## טענות באצווה זו",
        _claims_block(claims, resolver, store.audit_by_id),
    ]
    if extra_pages:
        sections += ["", "## תוכן עמודים שביקשת (read_page)", extra_pages]
    sections += [
        "",
        "## פלט",
        "החזר אובייקט JSON יחיד במבנה שהוגדר במערכת (ללא טקסט נוסף וללא code fence).",
        "כל claim_id חייב להופיע ברשימת הטענות שסופקו. אם ביקשת read_page, תוכל לכתוב "
        "statements לאחר שיוצג לך התוכן המבוקש.",
    ]
    return [
        CacheSegment(catalog + "\n", cache=True),
        CacheSegment("\n".join(sections)),
    ]
